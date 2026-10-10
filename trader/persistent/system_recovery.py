"""Structured system holds and conservative, flat-only entry recovery.

KILL existence remains the entry/exit guard. Legacy/operator/unknown holds never
become automatic by parsing text. No strategy, risk setting or LIVE flag is set
here. This module is exercised in isolated tests; deployment is a separate act.
"""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import fcntl
import json
import math
import os
import uuid

from ..models import UTC, NY, D, Unsafe, dt, iso, valid_quote
from ..store import atomic_text
from ..host_health import require_host
from ..configuration import assert_current
from ..risk import TERMINAL as BROKER_TERMINAL
from .accounting import reconcile_owned

VERSION = 'system-recovery-1'
AUTOMATIC = {'WATCHDOG_STALL', 'READ_TRANSPORT_EXIT'}
HARD_FAILURES = {
    'AMBIGUOUS_MUTATING_ORDER_STATE', 'OWNERSHIP_POSITION_MISMATCH',
    'RECONCILIATION_FAILURE', 'AUTHENTICATION_FAILURE', 'DATABASE_FAILURE',
    'UNKNOWN_ACCOUNT', 'UNCAUGHT_SOFTWARE_EXCEPTION', 'POSITION_MANAGEMENT_FAILURE',
}
BLOCKS = ('STOP', 'TRANSITION', 'LIQUIDATE', 'MAINTENANCE', 'READ_ONLY', 'ENTRIES_DISABLED')


@contextmanager
def hold_lock(runtime):
    """Short blocking lock for KILL writers; never hold across broker awaits."""
    with (Path(runtime)/'system-recovery.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read_hold(runtime):
    try:
        record = json.loads((Path(runtime)/'KILL').read_text())
        if (not isinstance(record, dict) or record.get('version') != VERSION
                or record.get('origin') != 'persistent_supervisor'
                or not isinstance(record.get('episode'), str)
                or not record.get('episode')):
            return None
        return record
    except (OSError, ValueError):
        return None


def record_hold(runtime, ledger, reason, pid, generation, now, *, failure=None, exit_code=None):
    """Latch before restart. Preserve a stronger/legacy hold and original cause."""
    runtime = Path(runtime)
    with hold_lock(runtime):
        previous = read_hold(runtime)
        if (runtime/'KILL').exists() and previous is None:
            ledger.event(now, 'SYSTEM_HOLD_PRESERVED', dict(reason_code=reason, legacy_or_operator=True,
                worker_pid=pid, worker_generation=generation, failure=failure or {}, exit_code=exit_code))
            return None
        if previous and previous.get('operator_required'):
            # Stronger cause stays immutable, but subsequent distinct failures still
            # consume the cumulative restart budget instead of looping forever.
            if generation and generation != previous.get('last_observed_generation', previous.get('worker_generation')):
                previous.update(failures=previous['failures']+1,last_observed_generation=generation)
                atomic_text(runtime/'KILL',json.dumps(previous,sort_keys=True))
                with ledger.tx():
                    ledger.put('system_hold',previous)
                    ledger.put('system_recovery_budget',dict(day=now.astimezone(NY).date().isoformat(),failures=previous['failures']))
                    ledger.event(now,'SYSTEM_HOLD_REPEATED',dict(episode=previous['episode'],failures=previous['failures']))
            return previous
        if previous and previous.get('worker_generation') == generation:
            # The observed SIGKILL exit is a consequence, not a new root cause.
            if reason == 'WORKER_EXIT':
                ledger.event(now, 'SYSTEM_HOLD_EXIT_OBSERVED', dict(episode=previous['episode'], exit_code=exit_code))
                return previous
        flat = not ledger.trades() and not ledger.orders()
        failure = failure or {}
        automatic = (reason in AUTOMATIC and flat and isinstance(generation, str)
                     and bool(generation) and failure.get('failure_class') not in HARD_FAILURES
                     and failure.get('operation') != 'MUTATION')
        if reason == 'READ_TRANSPORT_EXIT':
            automatic = automatic and failure.get('operation') == 'READ' and failure.get('failure_class') in {
                'TRANSIENT_READ_TRANSPORT_FAILURE', 'REPEATED_BROKER_COMMUNICATION_FAILURE',
                'PROLONGED_CONNECTIVITY_LOSS'}
        counter = ledger.get('system_recovery_budget', {})
        day = now.astimezone(NY).date().isoformat()
        # Never reset an outstanding episode at midnight or on worker restart.
        count = int(counter.get('failures', 0)) if counter.get('day') == day or previous else 0
        count += 1
        limit = (ledger.get('production_config') or __import__('trader.configuration', fromlist=['DEFAULTS']).DEFAULTS)['watchdog']['restart_limit']
        if count >= limit:
            automatic = False
        record = dict(version=VERSION, episode=previous['episode'] if previous else str(uuid.uuid4()),
            origin='persistent_supervisor', reason_code=reason,
            initial_reason_code=previous['initial_reason_code'] if previous else reason,
            created_at=previous['created_at'] if previous else iso(now), updated_at=iso(now),
            session=previous['session'] if previous else day, worker_pid=pid, worker_generation=generation,
            failure_class=failure.get('failure_class', reason), operation=failure.get('operation', 'UNKNOWN'),
            mutation_in_flight=any(o['status'] in ('DISPATCHING', 'AMBIGUOUS') for o in ledger.orders()),
            positions_at_failure=len(ledger.trades()), unresolved_at_failure=len(ledger.orders()),
            automatic_candidate=bool(automatic), operator_required=not automatic,
            reconciliation_required=True, failures=count, failure_limit=limit,
            state='RECOVERY_PENDING' if automatic else 'OPERATOR_REQUIRED', exit_code=exit_code)
        # Marker first: an interrupted DB write cannot expose entries.
        atomic_text(runtime/'KILL', json.dumps(record, sort_keys=True))
        with ledger.tx():
            ledger.put('system_recovery_budget', dict(day=day, failures=count))
            ledger.put('system_hold', record)
            ledger.event(now, 'SYSTEM_HOLD_CREATED', record)
        return record


def fresh(at, now, seconds):
    try:
        age = (now-dt(at)).total_seconds()
        return math.isfinite(age) and 0 <= age <= seconds
    except (ValueError, TypeError, KeyError):
        return False


def prove_flat(engine, record, obs, generation):
    """Positive evidence only; recomputed after the fresh full broker read."""
    now = engine.now(); ledger = engine.ledger; r = engine.runtime; config = engine.config
    checks = {}
    try:
        checks['provenance'] = (record == ledger.get('system_hold') and record.get('automatic_candidate') is True
            and record.get('operator_required') is False and record.get('reason_code') in AUTOMATIC
            and record.get('mutation_in_flight') is False and record.get('positions_at_failure') == 0
            and record.get('unresolved_at_failure') == 0 and record.get('failures', 999) < record.get('failure_limit', 0))
        checks['database'] = ledger.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert_current(engine.root, ledger); checks['configuration'] = True
        checks['permission_unchanged'] = ledger.settings().live_enabled is True
        checks['operator_controls'] = not any((r/n).exists() for n in BLOCKS)
        checks['local_flat'] = not ledger.trades() and not ledger.orders()
        worker = ledger.get('worker_identity', {})
        h = json.loads((r/'heartbeat.json').read_text()); w = json.loads((r/'supervisor-health.json').read_text())
        identity = worker.get('generation') == generation and h.get('pid') == worker.get('pid')
        checks['worker'] = (identity and h.get('state') in ('POLLING', 'HEALTHY', 'ENTRIES_BLOCKED')
            and 0 <= now.timestamp()-h['at'] <= config['watchdog']['heartbeat_stale_seconds'])
        checks['watchdog'] = (w.get('state') == 'RUNNING' and w.get('worker_pid') == worker.get('pid')
            and w.get('worker_generation') == generation and w.get('pid') == worker.get('supervisor_pid')
            and w.get('config_fingerprint') == ledger.get('config_fingerprint')
            and 0 <= now.timestamp()-w['at'] <= config['host']['health_max_age_seconds'])
        checks['stable_worker'] = ((now-dt(worker['started_at'])).total_seconds() >= config['connectivity']['recovery_min_seconds']
            and dt(worker['started_at']) >= dt(record['updated_at'])
            and generation != record['worker_generation'] and worker.get('pid') != record.get('worker_pid'))
        checks['episode_time'] = dt(record['created_at']) <= dt(record['updated_at']) <= now
        require_host(r, engine.now); checks['clock_host'] = True
        session = engine.session(); daily = ledger.session(session.day) if session else None
        checks['session'] = bool(session and session.entry_window(now) and daily and daily.get('day') == session.day)
        checks['connectivity'] = bool(daily and daily.get('recovery_active') is False and daily.get('connectivity_halt') is False)
        checks['opening'] = bool(daily and daily.get('opening_healthy') is True
            and fresh(daily.get('opening_checked_at'), now, (now-session.open).total_seconds()))
        checks['daily_risk'] = bool(daily and not any(daily.get(k, True) for k in ('loss_latched','entry_cutoff_latched','closed'))
            and 0 <= daily['attempts'] < ledger.settings().max_entry_attempts_per_day
            and D(0) <= D(daily['gross_losses']) < D(daily['loss_budget']) <= ledger.settings().daily_loss)
        checks['failure_quiet'] = bool(daily and (not daily.get('last_failure') or
            (now-dt(daily['last_failure'])).total_seconds() >= config['connectivity']['recovery_min_seconds']))
        health = ledger.get('health', {})
        checks['health'] = (health.get('auth') == 'AUTHENTICATED_READ_VERIFIED' and health.get('reconciled') is True
            and health.get('data_healthy') is True and fresh(health.get('at'), now, config['execution']['preflight_max_age_seconds']))
        snapshot = obs['snapshot']
        checks['full_fresh_snapshot'] = (obs.get('full_reconciliation') is True
            and fresh(obs.get('at'), now, config['execution']['preflight_max_age_seconds'])
            and fresh(snapshot.get('observed_at'), now, config['execution']['preflight_max_age_seconds']))
        checks['broker_flat'] = (snapshot['positions'] == [] and isinstance(snapshot['orders'], list)
            and all(o.get('state') in BROKER_TERMINAL for o in snapshot['orders']))
        checks['account'] = (snapshot['account']['account_number'] == engine.account == ledger.get('account_number')
            and bool(ledger.settings().account_suffix)
            and engine.account.endswith(ledger.settings().account_suffix))
        reconcile_owned(ledger, snapshot, engine.account, now, engine.cfg); checks['ownership'] = True
        checks['data'] = obs.get('data_healthy') is True
        from ..orb import evaluate
        from ..risk import eligible
        for symbol in engine.cfg.universe:
            valid_quote(obs['quotes'][symbol], now, engine.cfg)
            candidate = evaluate(symbol, engine.last_bars.get(symbol, []), session, now, engine.cfg)
            checks['strategy_state_'+symbol] = bool(candidate.get('opening_high') and candidate.get('vwap')
                and candidate.get('session') == session.day and candidate.get('state') in ('ARMED', 'MISSED')
                and eligible(obs['tradability'][symbol]))
        # A hard failure during this episode remains sticky even if later reads look flat.
        checks['no_hard_failure'] = True
        for row in ledger.db.execute("SELECT data FROM events WHERE kind='HEALTH_FAILURE' AND at>=?", (record['created_at'],)):
            diagnostic = json.loads(row[0]).get('diagnostic', {})
            if diagnostic.get('failure_class') in HARD_FAILURES or diagnostic.get('operation') == 'MUTATION':
                checks['no_hard_failure'] = False
        checks['complete'] = True
    except (Unsafe, OSError, ValueError, TypeError, KeyError, ArithmeticError):
        checks['complete'] = False
    return checks


async def recover_flat(engine, broker, bars, tradability, generation):
    """Never runs for legacy holds or positions; never submits a broker mutation."""
    ledger = engine.ledger; r = engine.runtime; now = engine.now()
    record = read_hold(r)
    if not record or record.get('automatic_candidate') is not True or record.get('operator_required') is not False:
        return False
    if ledger.trades() or ledger.orders() or any((r/n).exists() for n in BLOCKS):
        return False
    session = engine.session()
    if not session or not session.entry_window(now):
        return False
    last = getattr(engine, '_system_recovery_probe', None)
    # Derive probe cadence from the existing ordinary-recovery observation window.
    if last is not None and (now-last).total_seconds() < engine.config['connectivity']['ordinary_recovery_seconds']:
        return False
    engine._system_recovery_probe = now
    worker = ledger.get('worker_identity', {})
    if not fresh(worker.get('started_at'), now, 86400*365):
        return False
    if (now-dt(worker['started_at'])).total_seconds() < engine.config['connectivity']['recovery_min_seconds']:
        return False
    try:
        # Existing coordinator owns authentication, all pagination and reconciliation.
        engine.last_bars = bars
        obs = await engine.observe(broker, bars, tradability, full=True)
        checks = prove_flat(engine, record, obs, generation)
    except Exception as exc:
        from .recovery import classify
        failure = classify(exc, getattr(broker, 'failure_context', {}))
        if failure['failure_class'] in HARD_FAILURES:
            with hold_lock(r):
                if read_hold(r) == record:
                    record.update(automatic_candidate=False, operator_required=True, state='OPERATOR_REQUIRED',
                                  failure_class=failure['failure_class'])
                    atomic_text(r/'KILL', json.dumps(record, sort_keys=True))
                    with ledger.tx():
                        ledger.put('system_hold', record)
                        ledger.event(engine.now(), 'SYSTEM_RECOVERY_ESCALATED', dict(episode=record['episode'], failure_class=failure['failure_class']))
        raise
    with hold_lock(r):
        if read_hold(r) != record:
            return False
        # Recheck current controls, clock, timestamps, generation and ledger after awaits.
        checks = prove_flat(engine, record, obs, generation)
        if read_hold(r) != record:
            return False
        if checks.get('no_hard_failure') is False:
            record.update(automatic_candidate=False,operator_required=True,state='OPERATOR_REQUIRED',failure_class='RECONCILIATION_FAILURE')
            atomic_text(r/'KILL',json.dumps(record,sort_keys=True))
            with ledger.tx():
                ledger.put('system_hold',record)
                ledger.event(engine.now(),'SYSTEM_RECOVERY_ESCALATED',dict(episode=record['episode'],failure_class='RECONCILIATION_FAILURE'))
            return False
        state = 'RECOVERY_PROVEN' if checks and all(v is True for v in checks.values()) else 'RECOVERY_PENDING'
        ledger.event(engine.now(), 'SYSTEM_RECOVERY_EVIDENCE', dict(episode=record['episode'], state=state, checks=checks))
        if state != 'RECOVERY_PROVEN' or read_hold(r) != record:
            return False
        # Commit proof before unlink. Interruption leaves either the hold or an audit.
        with ledger.tx():
            ledger.put('last_system_recovery', dict(episode=record['episode'], at=iso(engine.now()), generation=generation))
            ledger.event(engine.now(), 'SYSTEM_RECOVERY_PROVEN', dict(episode=record['episode'], checks=checks))
        if read_hold(r) != record or any((r/n).exists() for n in BLOCKS):
            return False
        (r/'KILL').unlink()
        ledger.event(engine.now(), 'SYSTEM_HOLD_CLEARED', dict(episode=record['episode']))
    return True


def preserved_failure_evidence(db):
    """Read-only reconstruction, including records predating generation storage.

    Preserving a stronger hold must not erase the failures underneath it. Count
    distinct worker generations, not the consequent SIGKILL twice. Unknown
    generations count conservatively as separate failures. No midnight reset.
    """
    generations=set();by_pid={};pending=None;event_ids=[]
    for eid,kind,text in db.execute("SELECT id,kind,data FROM events WHERE kind IN ('SYSTEM_HOLD_PRESERVED','WATCHDOG_STALL','WORKER_EXIT') ORDER BY id"):
        d=json.loads(text)
        if kind=='SYSTEM_HOLD_PRESERVED':
            event_ids.append(eid)
            if d.get('worker_generation'):
                generations.add(d['worker_generation']);pending=None
            else:pending=d.get('reason_code')
        elif kind=='WATCHDOG_STALL' and pending=='WATCHDOG_STALL':
            generation=d.get('worker_generation') or 'unknown-event:'+str(eid)
            generations.add(generation);by_pid[d.get('pid')]=generation;pending=None
        elif kind=='WORKER_EXIT' and pending=='WORKER_EXIT':
            generations.add(by_pid.get(d.get('pid'),'unknown-event:'+str(eid)));pending=None
    if pending is not None:generations.add('unresolved-preserved-failure')
    return dict(failures=len(generations),event_ids=event_ids,unresolved=pending is not None)


def require_recovery_budget(db, config):
    """Read-only budget veto at operator/review authority boundaries.

    Legacy preservation records may be the only durable counter. Never treat a
    missing newer KV counter as proof that no failures occurred. The two views
    can describe the same failures, so use the larger count, never sum/reset.
    This supplies no disposition, reset, receipt or hold-clear authority.
    """
    row=db.execute("SELECT value FROM kv WHERE key='system_recovery_budget'").fetchone()
    stored=json.loads(row[0]) if row else {}
    if not isinstance(stored,dict):raise Unsafe('Malformed recovery budget')
    count=stored.get('failures',0)
    if type(count) is not int or count<0:raise Unsafe('Malformed recovery budget')
    preserved=preserved_failure_evidence(db)
    limit=config['watchdog']['restart_limit']
    if preserved['unresolved'] or max(count,preserved['failures'])>=limit:
        raise Unsafe('Recovery budget exhausted or unresolved; independent incident disposition required')
    return dict(stored_failures=count,preserved_failures=preserved['failures'],limit=limit)


def restart_permitted(runtime, ledger):
    """Stop flat crash loops after the cumulative budget; retain position monitoring.

    An operator can deliberately disarm LIVE and set hard READ_ONLY to permit
    observation-only diagnosis. Neither step erases this hold or its budget.
    """
    record = read_hold(runtime)
    if record:
        exhausted=record.get('failures',0)>=record.get('failure_limit',1)
    else:
        if not (Path(runtime)/'KILL').exists():return True
        config=ledger.get('production_config') or __import__('trader.configuration',fromlist=['DEFAULTS']).DEFAULTS
        exhausted=preserved_failure_evidence(ledger.db)['failures']>=config['watchdog']['restart_limit']
    if not exhausted:return True
    if ledger.trades() or ledger.orders():
        return True  # Existing rolling throttle still applies; protection takes priority.
    return not ledger.settings().live_enabled and (Path(runtime)/'READ_ONLY').exists()
