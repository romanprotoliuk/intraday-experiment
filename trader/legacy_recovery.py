"""Explicit outside-session acknowledgment of an investigated legacy incident.

Never called by worker startup, normal recovery, readiness or deployment.
The operator first binds reviewed ledger evidence to one exact filesystem hold.
Unknown legacy text alone is never evidence of a recoverable incident.
"""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from . import configuration as C
from .calendar import Calendar
from .doctor import read_db, kv
from .host_health import require_host
from .models import UTC, Unsafe, dt, iso
from .store import process_lock, atomic_text
from .persistent.db import Ledger
from .persistent.system_recovery import hold_lock, read_hold, AUTOMATIC, HARD_FAILURES, BLOCKS

TARGET='Worker exited; reconciliation/recovery required'
KEY='legacy_incident_review'
VERSION='investigated-legacy-1'
CHANGING_EVENTS={'SYSTEM_HOLD_CREATED','SYSTEM_HOLD_REPEATED','SYSTEM_HOLD_PRESERVED',
                 'SYSTEM_RECOVERY_ESCALATED','WORKER_FATAL','WORKER_EXIT','WATCHDOG_STALL','CONFIG_CHANGED','CONFIG_APPLIED',
                 'WORKER_STOP','PERSISTENT_WORKER_START','SUPERVISOR_START','RESTART_RECOVERY'}


def hold_identity(runtime):
    path=Path(runtime)/'KILL';fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>4096:raise Unsafe('Unbindable KILL')
        content=os.read(fd,4097);after=os.fstat(fd)
    finally:os.close(fd)
    fields=('st_dev','st_ino','st_ctime_ns','st_mtime_ns','st_size')
    identity={k:getattr(before,k) for k in fields}
    if any(identity!={k:getattr(s,k) for k in fields} for s in (after,path.lstat())):raise Unsafe('KILL changed')
    if content.decode().strip()!=TARGET:raise Unsafe('Unknown or nonlegacy KILL')
    return dict(identity,sha256=hashlib.sha256(content).hexdigest())


def account_binding(account,review_id):
    if not isinstance(account,str) or not account:raise Unsafe('Expected account missing')
    return hashlib.sha256((review_id+'\0'+account).encode()).hexdigest()


def evidence_digest(db, ids):
    if not isinstance(ids,list) or not ids or len(ids)>100 or any(type(x) is not int or x<=0 for x in ids):raise Unsafe('Specific incident event IDs required')
    rows=[]
    for eid in sorted(set(ids)):
        row=db.execute('SELECT id,at,kind,data FROM events WHERE id=?',(eid,)).fetchone()
        if not row:raise Unsafe('Incident evidence missing')
        rows.append(list(row))
    if not any(r[2] in ('WORKER_STOP','WORKER_EXIT','WATCHDOG_STALL') for r in rows):raise Unsafe('Worker incident evidence required')
    return hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()


def outside_session(root,now):
    s=Calendar(C.load(root)).session(now)
    if s and s.contains(now):raise Unsafe('Legacy acknowledgment only outside regular market hours')


def valid_review(root,db,record,now):
    if isinstance(record,dict) and record.get('version')=='investigated-successor-1':
        from .review_successor import valid_successor
        return valid_successor(root,db,record,now)
    try:
        config=C.load(root)
        if not isinstance(record,dict) or record.get('version')!=VERSION or record.get('status')!='REVIEWED':return False
        uuid.UUID(record['id'])
        if not dt(record['at'])<=now<dt(record['expires_at']):return False
        if record['target']!=hold_identity(Path(root)/'runtime'):return False
        if record['config_fingerprint']!=C.fingerprint(config) or kv(db,'production_config')!=config:return False
        if record['strategy_fingerprint']!=C.strategy_fingerprint(config) or kv(db,'strategy_fingerprint')!=record['strategy_fingerprint']:return False
        if record['account_binding']!=account_binding(kv(db,'account_number'),record['id']):return False
        if record['evidence_digest']!=evidence_digest(db,record['event_ids']):return False
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':return False
        budget=kv(db,'system_recovery_budget',{})
        if budget.get('failures',0)>=config['watchdog']['restart_limit']:return False
        for kind,data in db.execute('SELECT kind,data FROM events WHERE id>? ORDER BY id',(record['watermark'],)):
            d=json.loads(data)
            if kind in CHANGING_EVENTS:return False
            if kind=='HEALTH_FAILURE' and (d.get('diagnostic',{}).get('failure_class') in HARD_FAILURES or d.get('diagnostic',{}).get('operation')=='MUTATION'):return False
            if kind=='WORKER_STOP' and (d.get('controlled') is not True or d.get('open_positions') or d.get('unresolved_orders')):return False
        return True
    except (OSError,ValueError,TypeError,KeyError,Unsafe):return False


def classify_hold(root,db,now=None):
    r=Path(root)/'runtime'
    if not (r/'KILL').exists():return 'NORMAL'
    hold=read_hold(r)
    if hold:
        if (hold==kv(db,'system_hold') and hold.get('automatic_candidate') is True and hold.get('operator_required') is False
            and hold.get('reason_code') in AUTOMATIC and hold.get('failure_class') not in HARD_FAILURES
            and hold.get('operation')!='MUTATION' and hold.get('mutation_in_flight') is False
            and hold.get('positions_at_failure')==0 and hold.get('unresolved_at_failure')==0
            and hold.get('failures',999)<hold.get('failure_limit',0)):return 'B'
        return 'A'
    return 'C' if valid_review(root,db,active_review(db),now or datetime.now(UTC)) else 'A'


def active_review(db):
    """Legacy receipt never overwritten when a successor becomes current."""
    head=kv(db,'legacy_successor_head')
    if head is None:return kv(db,KEY)
    if not isinstance(head,str):return None
    return kv(db,'legacy_review_receipt:'+head)


def review(root,event_ids,expected_target,*,investigated=False,now=None):
    """Explicit operator attestation; only records a review, never removes a hold."""
    if not investigated:raise Unsafe('Explicit forensic investigation attestation required')
    root=Path(root);r=root/'runtime';now=now or datetime.now(UTC);outside_session(root,now)
    from .readiness import next_session
    if not (r/'persistent.sqlite3').is_file() or (r/'persistent.sqlite3').is_symlink():raise Unsafe('Existing database required')
    with process_lock(r/'configuration.lock'),hold_lock(r):
        ledger=Ledger.existing(r/'persistent.sqlite3')
        try:
            config=C.assert_current(root,ledger);target=hold_identity(r)
            if target!=expected_target:raise Unsafe('Reviewed KILL instance changed')
            prior=ledger.get(KEY)
            if prior and (prior.get('status')!='ACKNOWLEDGED' or prior.get('target')==target):raise Unsafe('Existing incident review cannot be overwritten')
            rid=str(uuid.uuid4());record=dict(version=VERSION,id=rid,status='REVIEWED',at=iso(now),
                expires_at=iso(next_session(config,now).open),target=target,event_ids=event_ids,
                evidence_digest=evidence_digest(ledger.db,event_ids),
                config_fingerprint=C.fingerprint(config),strategy_fingerprint=C.strategy_fingerprint(config),
                account_binding=account_binding(ledger.get('account_number'),rid),
                watermark=ledger.db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0])
            if not valid_review(root,ledger.db,record,now):raise Unsafe('Incident binding failed')
            with ledger.tx():ledger.put(KEY,record);ledger.event(now,'LEGACY_INCIDENT_REVIEWED',record)
            return record
        finally:ledger.close()


def require_proof(root,ledger,review_record,proof,clock,transition_text=None):
    now=clock();outside_session(root,now)
    if active_review(ledger.db)!=review_record or not valid_review(root,ledger.db,review_record,now):raise Unsafe('Incident review invalid or replaced')
    require_control_proof(root,ledger,proof,clock,transition_text)
    if review_record['version']=='investigated-successor-1' and proof.get('broker',{}).get('order_history_digest')!=review_record['interval']['order_history_digest']:
        raise Unsafe('Broker order history changed after successor investigation')


def require_control_proof(root,ledger,proof,clock,transition_text=None):
    """Same flat, outside-session health proof for review and acknowledgment."""
    now=clock();outside_session(root,now)
    required={'platform','config_permissions','runtime_permissions','database','configuration','strategy','risk','local_flat','ownership','worker_stable','quiet_period','heartbeat','watchdog','processes','launchagent','host_clock','ac_power','broker','worker_auth'}
    if not all(proof.get('checks',{}).get(k) is True for k in required):raise Unsafe('Control-plane proof incomplete')
    config=C.assert_current(root,ledger)
    if C.path(root).stat().st_mode&0o077 or (Path(root)/'runtime').stat().st_mode&0o077:
        raise Unsafe('Protected configuration/runtime permissions changed')
    if not 0<=(now-dt(proof['broker']['observed_at'])).total_seconds()<=config['execution']['preflight_max_age_seconds']:raise Unsafe('Broker proof stale')
    if ledger.trades() or ledger.orders():raise Unsafe('Local positions or orders appeared')
    if ledger.get('worker_identity',{}).get('generation')!=proof.get('generation'):raise Unsafe('Worker generation changed')
    r=Path(root)/'runtime';worker=ledger.get('worker_identity',{})
    h=json.loads((r/'heartbeat.json').read_text());w=json.loads((r/'supervisor-health.json').read_text());now=clock()
    if not (h.get('pid')==w.get('worker_pid')==worker.get('pid') and w.get('worker_generation')==worker.get('generation')
            and w.get('pid')==worker.get('supervisor_pid') and w.get('state')=='RUNNING'
            and h.get('state') in ('POLLING','HEALTHY','ENTRIES_BLOCKED')
            and 0<=now.timestamp()-h['at']<=config['watchdog']['heartbeat_stale_seconds']
            and 0<=now.timestamp()-w['at']<=config['host']['health_max_age_seconds']):raise Unsafe('Worker/watchdog evidence changed')
    os.kill(h['pid'],0);os.kill(w['pid'],0)
    if proof.get('config_fingerprint')!=C.fingerprint(config) or proof.get('strategy_fingerprint')!=C.strategy_fingerprint(config):raise Unsafe('Proof fingerprint mismatch')
    require_host(Path(root)/'runtime',clock)
    for marker in BLOCKS:
        path=Path(root)/'runtime'/marker
        if path.exists() and not (marker=='TRANSITION' and transition_text is not None and path.read_text()==transition_text):
            raise Unsafe('Conflicting operator control')


async def acknowledge(root,review_id,*,acknowledge=False,now=None):
    """Class C only; no LIVE flag, session, setup, risk or entry-gate mutation."""
    if not acknowledge:raise Unsafe('Explicit incident acknowledgment required')
    from .readiness import control_snapshot
    root=Path(root);r=root/'runtime';clock=now or (lambda:datetime.now(UTC));outside_session(root,clock())
    if not (r/'persistent.sqlite3').is_file() or (r/'persistent.sqlite3').is_symlink():raise Unsafe('Existing database required')
    with process_lock(r/'configuration.lock'):
        ledger=Ledger.existing(r/'persistent.sqlite3')
        try:
            record=active_review(ledger.db)
            if not record or record.get('id')!=review_id:raise Unsafe('Exact reviewed incident required')
            proof=await control_snapshot(root,now=clock)
            text='Legacy incident transition '+review_id+'; entries blocked\n'
            with hold_lock(r):
                require_proof(root,ledger,record,proof,clock)
                atomic_text(r/'TRANSITION',text)
            # KILL remains throughout both independent flat-account snapshots.
            proof=await control_snapshot(root,now=clock)
            with hold_lock(r):
                with ledger.tx():
                    require_proof(root,ledger,record,proof,clock,text)
                    done=dict(record,status='ACKNOWLEDGED',acknowledged_at=iso(clock()))
                    if record['version']=='investigated-successor-1':
                        ledger.put('legacy_review_status:'+record['id'],done)
                    else:ledger.put(KEY,done)
                    ledger.event(clock(),'LEGACY_INCIDENT_ACKNOWLEDGED',dict(review_id=review_id,target=record['target'],proof=proof))
                # Proof and consumption durable before unlink. Crash leaves TRANSITION.
                if hold_identity(r)!=record['target'] or (r/'TRANSITION').read_text()!=text:raise Unsafe('Control changed after proof commit')
                (r/'KILL').unlink()
                if (r/'KILL').exists():raise Unsafe('KILL removal unverified')
                ledger.event(clock(),'LEGACY_CONTROL_REMOVED',dict(review_id=review_id))
                (r/'TRANSITION').unlink()
            return dict(status='ACKNOWLEDGED',review_id=review_id,entry_eligible=False,live_permission_unchanged=True)
        finally:ledger.close()
