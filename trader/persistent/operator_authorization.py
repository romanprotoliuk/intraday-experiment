"""Deferred operator intent in the existing ledger, never a readiness override.

Creation is an explicit operator action. No receipt is installed by import,
startup, deployment, tests or ordinary system recovery.
"""
import asyncio
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from .. import configuration as C
from ..calendar import Calendar
from ..models import UTC, NY, Unsafe, dt, iso
from ..store import process_lock
from ..host_health import require_host
from .db import Ledger
from .system_recovery import hold_lock, HARD_FAILURES, BLOCKS

KEY = 'operator_recovery_authorization'
VERSION = 'session-operator-recovery-1'
TARGET = 'Worker exited; reconciliation/recovery required'
HARD_EVENTS = {'WATCHDOG_STALL', 'WORKER_EXIT', 'WORKER_FATAL',
               'SYSTEM_HOLD_CREATED', 'SYSTEM_HOLD_PRESERVED',
               'SYSTEM_HOLD_REPEATED', 'SYSTEM_RECOVERY_ESCALATED'}


def hold_identity(runtime):
    """Content plus filesystem identity detects even same-text replacements.

    The supported KILL writers serialize with hold_lock. Uncooperative external
    filesystem editing is not a supported control interface.
    """
    path = Path(runtime)/'KILL'
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 4096:
            raise Unsafe('KILL identity cannot be bound')
        content = os.read(fd, 4097)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    current = path.lstat()
    fields = ('st_dev', 'st_ino', 'st_ctime_ns', 'st_mtime_ns', 'st_size')
    identity = {k:getattr(before,k) for k in fields}
    if any(identity != {k:getattr(s,k) for k in fields} for s in (after,current)):
        raise Unsafe('KILL changed while reading identity')
    if content.decode('utf-8').strip() != TARGET:
        raise Unsafe('Only the exact reviewed legacy worker-exit hold is supported')
    return dict(identity, sha256=hashlib.sha256(content).hexdigest())


def account_binding(account, receipt_id):
    if not isinstance(account,str) or not account:raise Unsafe('Expected account unavailable')
    # Runtime-only salted binding; no account number or token in the receipt.
    return hashlib.sha256((receipt_id+'\0'+account).encode()).hexdigest()


def config_identity(root):
    p=C.path(root);s=p.stat()
    return dict(device=s.st_dev,inode=s.st_ino,changed_ns=s.st_ctime_ns,
                digest=hashlib.sha256(p.read_bytes()).hexdigest())


def invalidate(ledger, now, reason):
    record=ledger.get(KEY)
    if isinstance(record,dict) and record.get('status') in ('CONSUMED','INVALIDATED','EXPIRED'):
        return
    receipt_id=record.get('id') if isinstance(record,dict) else None
    # Do not echo malformed payloads or arbitrary values into diagnostics.
    if not isinstance(receipt_id,str) or len(receipt_id)!=36:receipt_id=None
    status='EXPIRED' if reason in ('SESSION_CHANGED','EXPIRED') else 'INVALIDATED'
    safe=dict(record) if isinstance(record,dict) else {}
    safe.update(status=status,ended_at=iso(now),end_reason=reason)
    with ledger.tx():
        ledger.put(KEY,safe)
        ledger.event(now,'OPERATOR_RECOVERY_AUTHORIZATION_'+status,dict(id=receipt_id,reason=reason))


def binding_failure(root, ledger, record, now, *, transition_owned=False):
    """Sticky invalidations; temporary absence of market/clock proof is separate."""
    try:
        if not isinstance(record,dict) or record.get('version')!=VERSION:return 'MALFORMED'
        if record.get('status')!='PENDING':return 'NOT_PENDING'
        uuid.UUID(record['id'])
        if record['session']!=now.astimezone(NY).date().isoformat():return 'SESSION_CHANGED'
        config=C.assert_current(root,ledger)
        session=Calendar(config).session(now)
        if not session or session.day!=record['session']:return 'SESSION_CHANGED'
        cutoff=session.close-timedelta(minutes=session.entry_cutoff_minutes)
        if dt(record['expires_at'])!=cutoff or not dt(record['created_at'])<=now<cutoff:return 'EXPIRED'
        if record['target_reason']!=TARGET or record['target']!=hold_identity(Path(root)/'runtime'):return 'TARGET_CHANGED'
        if record['config_fingerprint']!=C.fingerprint(config) or record['config_identity']!=config_identity(root):return 'CONFIG_CHANGED'
        if record['strategy_fingerprint']!=C.strategy_fingerprint(config) or ledger.get('strategy_fingerprint')!=record['strategy_fingerprint']:return 'STRATEGY_CHANGED'
        if record['account_binding']!=account_binding(ledger.get('account_number'),record['id']):return 'ACCOUNT_CHANGED'
        if ledger.db.execute('PRAGMA quick_check').fetchone()[0]!='ok':return 'DATABASE_FAILED'
        if ledger.trades() or ledger.orders():return 'POSITION_OR_ORDER_UNCERTAIN'
        if not ledger.settings().live_enabled:return 'LIVE_PERMISSION_CHANGED'
        forbidden=set(BLOCKS)-({'TRANSITION'} if transition_owned else set())
        if any((Path(root)/'runtime'/name).exists() for name in forbidden):return 'OTHER_OPERATOR_CONTROL'
        budget=ledger.get('system_recovery_budget',{})
        if budget.get('day')==record['session'] and budget.get('failures',0)>=config['watchdog']['restart_limit']:return 'RECOVERY_BUDGET_EXHAUSTED'
        watermark=record['event_watermark']
        if type(watermark) is not int or watermark<0:return 'MALFORMED'
        for row in ledger.db.execute('SELECT kind,data FROM events WHERE id>? ORDER BY id',(watermark,)):
            kind=row['kind'];data=json.loads(row['data'])
            if kind in HARD_EVENTS:return 'NEW_FAILURE_EPISODE'
            if kind=='WORKER_STOP' and (data.get('controlled') is not True or data.get('open_positions') or data.get('unresolved_orders')):return 'UNCERTAIN_WORKER_STOP'
            if kind=='HEALTH_FAILURE':
                diagnostic=data.get('diagnostic',{})
                if diagnostic.get('failure_class') in HARD_FAILURES or diagnostic.get('operation')=='MUTATION':return 'HARD_HEALTH_FAILURE'
                if data.get('outage',0)>=config['connectivity']['outage_threshold']:return 'RECOVERY_BUDGET_EXHAUSTED'
        return None
    except (KeyError,TypeError,ValueError,OSError,Unsafe):
        return 'BINDING_UNVERIFIABLE'


def pending(root, ledger, now, *, transition_owned=False):
    record=ledger.get(KEY)
    if record is None:return None
    if isinstance(record,dict) and record.get('status') in ('CONSUMED','INVALIDATED','EXPIRED'):return None
    reason=binding_failure(root,ledger,record,now,transition_owned=transition_owned)
    if reason:
        invalidate(ledger,now,reason);return None
    return record


def create(root, session_day, *, acknowledge=False, now=None):
    """Explicit operator API. Never grants LIVE or changes a control marker."""
    if not acknowledge:raise Unsafe('Explicit operator acknowledgement required')
    root=Path(root);r=root/'runtime';now=now or datetime.now(UTC)
    if not (r/'persistent.sqlite3').is_file() or (r/'persistent.sqlite3').is_symlink():
        raise Unsafe('Existing production database required; never initialize it for authorization')
    with process_lock(r/'configuration.lock'),hold_lock(r):
        ledger=Ledger(r/'persistent.sqlite3')
        try:
            config=C.assert_current(root,ledger);session=Calendar(config).session(now)
            if not session or session.day!=session_day:raise Unsafe('Authorization requires the current exchange session')
            cutoff=session.close-timedelta(minutes=session.entry_cutoff_minutes)
            if now>=cutoff:raise Unsafe('Session entry cutoff already reached')
            prior=ledger.get(KEY)
            if prior is not None and (not isinstance(prior,dict) or prior.get('session')==session_day or prior.get('status')=='PENDING'):
                raise Unsafe('Existing authorization must not be overwritten or reissued')
            receipt_id=str(uuid.uuid4())
            record=dict(version=VERSION,id=receipt_id,status='PENDING',created_at=iso(now),session=session_day,
                expires_at=iso(cutoff),target_reason=TARGET,target=hold_identity(r),
                config_fingerprint=C.fingerprint(config),config_identity=config_identity(root),
                strategy_fingerprint=C.strategy_fingerprint(config),
                account_binding=account_binding(ledger.get('account_number'),receipt_id),
                worker_generation_at_creation=ledger.get('worker_identity',{}).get('generation'),
                event_watermark=ledger.db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0])
            reason=binding_failure(root,ledger,record,now)
            if reason:raise Unsafe('Authorization creation blocked: '+reason)
            with ledger.tx():
                ledger.put(KEY,record)
                ledger.event(now,'OPERATOR_RECOVERY_AUTHORIZATION_CREATED',record)
            return record
        finally:ledger.close()


def consume(ledger, record, now, proof):
    """Called only inside the existing operator transition's locked DB transaction.

    Commit consumption/proof before unlinking the hold, while TRANSITION blocks
    entries. Interruption is fail-closed; a spent receipt never resumes itself.
    """
    if not ledger.db.in_transaction:raise Unsafe('Consumption requires a transaction')
    if ledger.get(KEY)!=record or record.get('status')!='PENDING':raise Unsafe('Authorization changed during transition')
    done=dict(record,status='CONSUMED',consumed_at=iso(now),proof=proof)
    ledger.put(KEY,done)
    ledger.event(now,'OPERATOR_RECOVERY_AUTHORIZATION_CONSUMED',dict(id=record['id'],proof=proof))


async def evaluate_pending(engine, *, timeout=None):
    """Worker-owned evaluation. No pending receipt means no additional action."""
    root=engine.root;ledger=engine.ledger;now=engine.now()
    with hold_lock(engine.runtime):record=pending(root,ledger,now)
    if record is None:return False
    session=engine.session()
    if not session or not session.entry_window(now):return False
    daily=ledger.session(session.day) or {}
    if not daily.get('opening_healthy') or daily.get('recovery_active',True) or daily.get('connectivity_halt',True):return False
    try:require_host(engine.runtime,engine.now)
    except (Unsafe,OSError,ValueError):return False
    last=getattr(engine,'_operator_recovery_probe',None)
    if last is not None and (now-last).total_seconds()<engine.config['connectivity']['ordinary_recovery_seconds']:return False
    engine._operator_recovery_probe=now
    from ..doctor import transition
    budget=engine.config['watchdog']['cycle_deadline_seconds'] if timeout is None else timeout
    if budget<=0:return False
    try:
        await asyncio.wait_for(transition(root,True,True,True,authorization_id=record['id'],clock=engine.now),budget)
    except (Unsafe,TimeoutError):return False
    return ledger.get(KEY,{}).get('status')=='CONSUMED' and not (engine.runtime/'KILL').exists() and not (engine.runtime/'TRANSITION').exists()
