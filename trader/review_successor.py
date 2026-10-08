"""Explicit, flat-only successor investigation. Never called by a worker.

Receipts and their predecessor remain immutable. This module does not remove a
hold or change LIVE. Unknown events are rejection, not evidence of recovery.
"""
from collections import Counter
from datetime import datetime
import hashlib
import inspect
import json
from pathlib import Path
import re
import subprocess
import uuid

from mcp.shared.session import BaseSession
from . import configuration as C
from .doctor import kv
from .models import UTC,Unsafe,dt,iso
import sqlite3
from .persistent.db import Ledger
from .persistent.system_recovery import hold_lock,HARD_FAILURES
from .risk import TERMINAL
from .readiness import order_history_digest
from .store import process_lock

VERSION='investigated-successor-1'
HEAD='legacy_successor_head'
PREFIX='legacy_review_receipt:'
ROUTINE={'CANDIDATE_ANALYSIS','RECONNECT_BACKOFF','CONNECTIVITY_RECOVERED',
         'CONNECTIVITY_HALT_RECOVERED','OPENING_EQUIVALENT_HEALTH_PASSED',
         'SESSION_ENTRY_CUTOFF_LATCHED','SESSION_LIQUIDATION_DUE','SESSION_CLOSED'}
# Reuse the broker capability allowlist; order preflight belongs to intent
# investigation, not this flat-read-only maintenance workflow.
from .broker import READ_TOOLS
READS=READ_TOOLS-{'review_equity_order'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def code_identity(root):
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
    if git('status','--porcelain'):raise Unsafe('Successor investigation requires a clean committed build')
    files=('trader/legacy_recovery.py','trader/review_successor.py','trader/readiness.py','trader/persistent/recovery.py')
    return dict(commit=git('rev-parse','HEAD'),source_digest=digest({p:hashlib.sha256((Path(root)/p).read_bytes()).hexdigest() for p in files}))


def _exception_leaves(record):
    if not isinstance(record,dict):raise Unsafe('Missing exception evidence')
    if record.get('type') in ('ExceptionGroup','BaseExceptionGroup'):
        if not record.get('children'):raise Unsafe('Empty exception group')
        return [leaf for child in record['children'] for leaf in _exception_leaves(child)]
    return [record]


def _sdk_deadline_line():
    lines,start=inspect.getsourcelines(BaseSession.send_request)
    for i,line in enumerate(lines):
        if line.strip()=='except TimeoutError:':
            if lines[i+1].strip()=='raise McpError(':return start+i+1
    raise Unsafe('Installed SDK local timeout provenance not recognized')


def investigated_read_failure(diagnostic):
    """Adjudicate retained local exception evidence, never rewrite its class.

    Old SDK timeouts lacked a serialized implicit context. Require its exact
    local raise location as well as type/code/message and the broker request
    frame. A remote 408, mixed exception, or mere matching class name fails.
    This is only an explicitly investigated receipt path, not auto-recovery.
    """
    if diagnostic.get('operation')!='READ' or diagnostic.get('tool') not in READS:
        raise Unsafe('Intervening failure is not a supported broker read')
    kind=diagnostic.get('failure_class')
    allowed={'RECONCILIATION_FAILURE','TRANSIENT_READ_TRANSPORT_FAILURE',
             'REPEATED_BROKER_COMMUNICATION_FAILURE','PROLONGED_CONNECTIVITY_LOSS'}
    if kind not in allowed:raise Unsafe('Hard intervening failure')
    exception=diagnostic.get('exception')
    leaves=_exception_leaves(exception)
    outer_frames=exception.get('frames',[])
    for leaf in leaves:
        frames=leaf.get('frames',[])
        if leaf.get('type')=='McpError':
            if not any(f.get('file')=='broker.py' and f.get('function')=='_request' for f in frames):
                raise Unsafe('Broker read provenance missing')
            if not (not leaf.get('cause') and leaf.get('mcp_code')==408 and re.fullmatch(r'Timed out while waiting for response to ClientRequest\. Waited [0-9.]+ seconds\.',leaf.get('message','')) and
                    any(f.get('file')=='session.py' and f.get('function')=='send_request' and f.get('line')==_sdk_deadline_line() for f in frames)):
                raise Unsafe('Unproven MCP timeout or broker-side error')
        elif leaf.get('type') in ('ReadTimeout','ConnectTimeout','PoolTimeout','WriteTimeout','RemoteProtocolError','ConnectError','ReadError'):
            if kind=='RECONCILIATION_FAILURE':raise Unsafe('Historical hard failure is not the supported local SDK timeout')
            if not (any(f.get('file')=='broker.py' and f.get('function') in ('connect','_request') for f in frames+outer_frames) and
                    any(f.get('file')=='streamable_http.py' and f.get('function')=='_handle_post_request' for f in frames)):
                raise Unsafe('HTTP read provenance missing')
            cause=leaf.get('cause')
            if cause and cause.get('type')!=leaf['type']:raise Unsafe('Unexplained transport cause')
        else:raise Unsafe('Unknown or mixed read failure')
    return {'original_class':kind,'finding':'READ_UNAVAILABLE_RECONCILED','tools':[diagnostic['tool']]}


def _rows(db,start,end):
    rows=[list(r) for r in db.execute('SELECT id,at,kind,data FROM events WHERE id>? AND id<=? ORDER BY id',(start,end))]
    if [r[0] for r in rows]!=list(range(start+1,end+1)):raise Unsafe('Unexplained ledger interval gap')
    return rows


def _flat_reconciliation(data,event_at,config):
    if data.get('account_suffix')!=config['broker']['account_suffix']:raise Unsafe('Retained account binding changed')
    if not isinstance(data.get('positions'),list) or data['positions']:raise Unsafe('Intervening broker exposure')
    if not isinstance(data.get('orders'),list) or any(o.get('state') not in TERMINAL for o in data['orders']):
        raise Unsafe('Intervening working or ambiguous broker order')
    age=(dt(event_at)-dt(data['snapshot_at'])).total_seconds()
    if not 0<=age<=config['connectivity']['account_max_age_seconds']:raise Unsafe('Stale/future retained reconciliation')


def controlled_restarts(db,rows,config):
    """Adjudicate a complete controlled flat lifecycle, never a crash/stall.

    A shutdown flag alone is insufficient: bind the prior generation, new
    supervisor, flat startup, different worker generation and prompt full read.
    Every row still goes through interval identity and broker-history checks.
    """
    accepted=set();findings=[]
    for i,(eid,at,kind,text) in enumerate(rows):
        if kind!='WORKER_STOP':continue
        d=json.loads(text)
        if d.get('controlled') is not True or d.get('open_positions')!=0 or d.get('unresolved_orders')!=0:
            raise Unsafe('Uncontrolled or nonflat worker stop')
        prior=db.execute("SELECT data FROM events WHERE id<? AND kind='PERSISTENT_WORKER_START' ORDER BY id DESC LIMIT 1",(eid,)).fetchone()
        previous=json.loads(prior[0]) if prior else {}
        if not d.get('generation') or (d.get('generation'),d.get('pid'))!=(previous.get('generation'),previous.get('pid')):
            raise Unsafe('Controlled stop generation unproven')
        lifecycle=rows[i+1:i+4]
        if [r[2] for r in lifecycle]!=['SUPERVISOR_START','RESTART_RECOVERY','PERSISTENT_WORKER_START']:
            raise Unsafe('Incomplete controlled deployment lifecycle')
        supervisor,recovery,worker=[json.loads(r[3]) for r in lifecycle]
        if (supervisor.get('architecture')!='persistent-v2' or not supervisor.get('pid') or
            recovery.get('open_positions')!=0 or recovery.get('pending')!=0 or recovery.get('failure_class')!='WORKER_PROCESS_FAILURE' or
            worker.get('supervisor_pid')!=supervisor['pid'] or not worker.get('generation') or
            worker['generation']==d['generation'] or not worker.get('pid') or worker['pid']==d['pid']):
            raise Unsafe('Controlled startup identity/flatness unproven')
        before=db.execute("SELECT id,at FROM events WHERE id<? AND kind='RECONCILIATION' ORDER BY id DESC LIMIT 1",(eid,)).fetchone()
        after=next((r for r in rows[i+4:] if r[2]=='RECONCILIATION'),None)
        if (not before or not after or
            not 0<=(dt(at)-dt(before[1])).total_seconds()<=config['connectivity']['idle_poll_seconds']+config['watchdog']['cycle_deadline_seconds'] or
            not 0<=(dt(after[1])-dt(at)).total_seconds()<=config['watchdog']['startup_timeout_seconds'] or
            not dt(at)<=dt(worker['started_at'])<=dt(after[1])):
            raise Unsafe('Controlled restart reconciliation stale or missing')
        covered=[eid]+[r[0] for r in lifecycle];accepted.update(covered)
        findings.append(dict(events=covered,before_event=before[0],after_event=after[0],old_generation=d['generation'],new_generation=worker['generation']))
    return accepted,findings


def investigate_interval(root,db,anchor,end,now):
    """Cover every row, require complete flat reads before/after each failure."""
    from .legacy_recovery import KEY
    config=C.load(root);rows=_rows(db,anchor['watermark'],end)
    lifecycle_ids,lifecycles=controlled_restarts(db,rows,config)
    prior=db.execute("SELECT id,at,kind,data FROM events WHERE id<=? AND kind='RECONCILIATION' ORDER BY id DESC LIMIT 1",(anchor['watermark'],)).fetchone()
    before=list(prior) if prior else None
    if before is None:raise Unsafe('No initial broker reconciliation')
    initial=json.loads(before[3]);_flat_reconciliation(initial,before[1],config)
    orders_digest=order_history_digest(initial['orders'])
    unresolved=[];findings=[];failures=[];last_at=dt(anchor['at'])
    for eid,at,kind,text in rows:
        when=dt(at);data=json.loads(text)
        if when>now or when<last_at:raise Unsafe('Future or nonmonotonic audit evidence')
        last_at=when
        if any(data.get(k)!=anchor[k] for k in ('config_fingerprint','strategy_fingerprint')):
            raise Unsafe('Intervening configuration/strategy binding mismatch')
        if kind=='RECONCILIATION':
            _flat_reconciliation(data,at,config)
            if order_history_digest(data['orders'])!=orders_digest:raise Unsafe('Intervening broker order history changed')
            for item in unresolved:
                if (when-dt(item['at'])).total_seconds()>config['connectivity']['halt_after_seconds']:
                    raise Unsafe('Read outage exceeds existing connectivity horizon')
                item['after_event']=eid;findings.append(item)
            unresolved=[];before=[eid,at,kind,text]
        elif kind=='HEALTH_FAILURE':
            if before is None:raise Unsafe('No prior broker reconciliation')
            _flat_reconciliation(json.loads(before[3]),before[1],config)
            if (when-dt(before[1])).total_seconds()>config['connectivity']['idle_poll_seconds']+config['watchdog']['cycle_deadline_seconds']:
                raise Unsafe('Prior reconciliation too old for investigated read outage')
            adjudication=investigated_read_failure(data.get('diagnostic',{}))
            failures.append(when)
            if sum(0<=(when-t).total_seconds()<=config['connectivity']['recovery_min_seconds'] for t in failures)>=config['watchdog']['restart_limit']:
                raise Unsafe('Repeated read failures exceed existing recovery budget')
            unresolved.append(dict(event_id=eid,at=at,before_event=before[0],**adjudication))
        elif kind=='LEGACY_INCIDENT_REVIEWED':
            if data!=anchor:raise Unsafe('Unrelated legacy review in interval')
        elif kind in ('LEGACY_REVIEW_INVALIDATED','LEGACY_SUCCESSOR_REVIEWED'):
            # Only receipts committed through this workflow, with an event binding.
            rid=data.get('id') if kind=='LEGACY_SUCCESSOR_REVIEWED' else data.get('successor_id')
            stored=kv(db,PREFIX+str(rid))
            if not stored or (kind=='LEGACY_SUCCESSOR_REVIEWED' and stored!=data):raise Unsafe('Unbound review lifecycle event')
        elif eid in lifecycle_ids:pass  # Already proven complete and flat, not a generic whitelist.
        elif kind not in ROUTINE:raise Unsafe('Unexplained intervening event: '+kind)
    if unresolved:raise Unsafe('Read outage has no complete subsequent reconciliation')
    # Empty/read-only intervals may be reinvestigated after expiration; they
    # never authorize reuse of the expired receipt.
    return dict(start_exclusive=anchor['watermark'],end_inclusive=end,row_count=len(rows),
                interval_digest=digest(rows),order_history_digest=orders_digest,event_kind_counts=dict(Counter(r[2] for r in rows)),resolved_reads=findings,controlled_restarts=lifecycles)


def valid_successor(root,db,record,now):
    from .legacy_recovery import (KEY,hold_identity,account_binding,evidence_digest,CHANGING_EVENTS)
    try:
        if record.get('version')!=VERSION or record.get('status')!='REVIEWED':return False
        if kv(db,HEAD)!=record['id'] or kv(db,PREFIX+record['id'])!=record or kv(db,'legacy_review_status:'+record['id']):return False
        if not dt(record['at'])<=now<dt(record['expires_at']):return False
        anchor=kv(db,KEY)
        if digest(anchor)!=record['anchor_digest'] or digest(record['parent'])!=record['parent_digest']:return False
        parent=record['parent']
        if parent['id']!=record['parent_id']:return False
        if parent!=(anchor if parent.get('version')=='investigated-legacy-1' else kv(db,PREFIX+parent['id'])):return False
        if record['target']!=hold_identity(Path(root)/'runtime'):return False
        config=C.load(root)
        if record['config_fingerprint']!=C.fingerprint(config) or kv(db,'production_config')!=config:return False
        if record['strategy_fingerprint']!=C.strategy_fingerprint(config) or kv(db,'strategy_fingerprint')!=record['strategy_fingerprint']:return False
        if record['account_binding']!=account_binding(kv(db,'account_number'),record['id']):return False
        if evidence_digest(db,record['event_ids'])!=record['evidence_digest']:return False
        if record['code_identity']!=code_identity(root):return False
        interval=record['interval']
        if digest(_rows(db,interval['start_exclusive'],interval['end_inclusive']))!=interval['interval_digest']:return False
        budget=kv(db,'system_recovery_budget',{})
        if budget.get('failures',0)>=config['watchdog']['restart_limit']:return False
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':return False
        witnessed=False
        for at,kind,data in db.execute('SELECT at,kind,data FROM events WHERE id>? ORDER BY id',(record['watermark'],)):
            d=json.loads(data)
            if dt(at)>now:return False
            if any(d.get(k)!=record[k] for k in ('config_fingerprint','strategy_fingerprint')):return False
            if kind=='RECONCILIATION':
                _flat_reconciliation(d,at,config)
                if order_history_digest(d['orders'])!=interval['order_history_digest']:return False
            if kind not in ROUTINE|{'RECONCILIATION','HEALTH_FAILURE','LEGACY_REVIEW_INVALIDATED','LEGACY_SUCCESSOR_REVIEWED'}:return False
            if kind=='LEGACY_SUCCESSOR_REVIEWED' and d==record:witnessed=True
            if kind in CHANGING_EVENTS:return False
            if kind=='HEALTH_FAILURE':
                diag=d.get('diagnostic',{})
                if diag.get('operation')!='READ' or diag.get('failure_class') not in {'TRANSIENT_READ_TRANSPORT_FAILURE','REPEATED_BROKER_COMMUNICATION_FAILURE','PROLONGED_CONNECTIVITY_LOSS'}:return False
            if kind=='WORKER_STOP' and (d.get('controlled') is not True or d.get('open_positions') or d.get('unresolved_orders')):return False
        return witnessed
    except (OSError,ValueError,TypeError,KeyError,Unsafe,sqlite3.DatabaseError,subprocess.SubprocessError):return False


async def successor(root,previous_id,*,investigated=False,now=None):
    """Explicit operator-only review creation; no KILL or LIVE transition."""
    from .legacy_recovery import (KEY,active_review,hold_identity,account_binding,evidence_digest,
                                  outside_session,require_control_proof,valid_review)
    from .readiness import control_snapshot,next_session
    if not investigated:raise Unsafe('Explicit new investigation required')
    root=Path(root);runtime=root/'runtime';clock=now or (lambda:datetime.now(UTC));outside_session(root,clock())
    with process_lock(runtime/'configuration.lock'):
        ledger=Ledger.existing(runtime/'persistent.sqlite3')
        try:
            parent=active_review(ledger.db);anchor=ledger.get(KEY);config=C.assert_current(root,ledger)
            if not parent or parent.get('id')!=previous_id or parent.get('status')!='REVIEWED':raise Unsafe('Exact unconsumed predecessor required')
            if ledger.get('legacy_review_status:'+parent['id']):raise Unsafe('Consumed predecessor')
            if not dt(parent['at'])<=clock():raise Unsafe('Predecessor future')
            if not dt(parent['at'])<dt(parent['expires_at']):raise Unsafe('Malformed predecessor lifetime')
            if valid_review(root,ledger.db,parent,clock()):raise Unsafe('Current review remains valid; no successor needed')
            target=hold_identity(runtime)
            if target!=parent['target'] or target!=anchor['target']:raise Unsafe('Hold changed')
            if (parent['config_fingerprint']!=C.fingerprint(config) or parent['strategy_fingerprint']!=C.strategy_fingerprint(config) or
                parent['account_binding']!=account_binding(ledger.get('account_number'),parent['id'])):raise Unsafe('Predecessor identity binding changed')
            if evidence_digest(ledger.db,anchor['event_ids'])!=anchor['evidence_digest']:raise Unsafe('Original incident evidence changed')
            build=code_identity(root)
            if ledger.get('system_recovery_budget',{}).get('failures',0)>=config['watchdog']['restart_limit']:raise Unsafe('Recovery budget exhausted')
            # Reject unknown/hard intervals before touching the broker.
            end=ledger.db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
            investigate_interval(root,ledger.db,anchor,end,clock())
            first=await control_snapshot(root,now=clock)
            require_control_proof(root,ledger,first,clock)
            second=await control_snapshot(root,now=clock)
            with hold_lock(runtime),ledger.tx():
                require_control_proof(root,ledger,second,clock)
                if active_review(ledger.db)!=parent or ledger.get(KEY)!=anchor or hold_identity(runtime)!=target:raise Unsafe('Review or hold changed concurrently')
                if first.get('generation')!=second.get('generation'):raise Unsafe('Worker restarted during investigation')
                if code_identity(root)!=build:raise Unsafe('Code changed during investigation')
                end=ledger.db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
                interval=investigate_interval(root,ledger.db,anchor,end,clock())
                if interval['controlled_restarts'] and interval['controlled_restarts'][-1]['new_generation']!=second.get('generation'):
                    raise Unsafe('Current worker differs from investigated restart')
                if any(proof.get('broker',{}).get('order_history_digest')!=interval['order_history_digest'] for proof in (first,second)):
                    raise Unsafe('Fresh broker order history differs from investigated interval')
                created=clock();expires=next_session(config,created).open
                if not created<expires:raise Unsafe('Investigation expired before commit')
                rid=str(uuid.uuid4());record=dict(version=VERSION,id=rid,status='REVIEWED',at=iso(created),expires_at=iso(expires),
                    target=target,parent_id=parent['id'],parent=parent,parent_digest=digest(parent),anchor_digest=digest(anchor),
                    event_ids=anchor['event_ids'],evidence_digest=anchor['evidence_digest'],
                    config_fingerprint=C.fingerprint(config),strategy_fingerprint=C.strategy_fingerprint(config),
                    account_binding=account_binding(ledger.get('account_number'),rid),code_identity=build,
                    generation=second['generation'],interval=interval,watermark=end,
                    proofs=[first,second])
                if ledger.get(PREFIX+rid) is not None:raise Unsafe('Receipt identity collision')
                ledger.put(PREFIX+rid,record);ledger.put(HEAD,rid)
                ledger.event(created,'LEGACY_REVIEW_INVALIDATED',dict(previous_id=parent['id'],previous_digest=digest(parent),successor_id=rid,interval_digest=interval['interval_digest']))
                ledger.event(created,'LEGACY_SUCCESSOR_REVIEWED',record)
            return record
        finally:ledger.close()
