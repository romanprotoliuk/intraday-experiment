"""Synthetic recovery regression: temporary ledger and fake broker; no networking."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json

import httpx
import pytest

from trader import configuration as C
from trader.models import dt, iso, Unsafe
from trader.persistent.system_recovery import record_hold, read_hold, prove_flat, recover_flat
from trader.persistent.recovery import classify
from test_persistent import env, raw, host, eligible_symbols, reserve, tick

# Dates, generations and failures are unrelated to production incident telemetry.
GEN = 'synthetic-new-worker'
OLD = 'synthetic-old-worker'


@pytest.fixture
def recovery(env):
    e,b,db = env
    config = C.validate({'broker': {'account_suffix': e.account[-4:]}})
    C.write(e.root, config)
    # Apply without changing an armed fixture: test fixtures supply the exact snapshot.
    settings=db.get('settings');settings['account_suffix']=e.account[-4:];db.put('settings',settings)
    db.put('production_config', config); db.put('config_fingerprint', C.fingerprint(config))
    db.put('strategy_fingerprint', C.strategy_fingerprint(config))
    e.config=config; e.cfg=C.market_config(config)
    now=b.time
    record=record_hold(e.runtime,db,'WATCHDOG_STALL',41,OLD,now-timedelta(minutes=8))
    db.put('worker_identity',dict(pid=1,generation=GEN,supervisor_pid=9,started_at=iso(now-timedelta(minutes=6))))
    with db.tx():
        day=db.session(e.session().day)
        day.update(opening_healthy=True,opening_checked_at=iso(now-timedelta(seconds=1)),
                   recovery_active=False,connectivity_halt=False)
        db.save_session(e.session().day,day)
    healthy_files(e,b)
    e.last_bars=raw(now)
    obs=asyncio.run(e.observe(b,e.last_bars,eligible_symbols(),full=True))
    yield e,b,db,record,obs


def healthy_files(e,b):
    host(e.root,b.time)
    h=json.loads((e.runtime/'host-health.json').read_text())
    h.update(supervisor_pid=9,ac_power=True,sleep_prevention=True,config_fingerprint=C.fingerprint(e.config))
    (e.runtime/'host-health.json').write_text(json.dumps(h))
    (e.runtime/'supervisor.json').write_text(json.dumps({'pid':9}))
    (e.runtime/'supervisor-health.json').write_text(json.dumps(dict(pid=9,state='RUNNING',at=b.time.timestamp(),worker_pid=1,
        worker_generation=GEN,sleep_prevention=True,config_fingerprint=C.fingerprint(e.config))))


def run_recovery(e,b):
    return asyncio.run(recover_flat(e,b,raw(b.time),eligible_symbols(),GEN))


def test_flat_recovery_preserves_history_and_permission(recovery):
    e,b,db,record,obs=recovery
    proof=prove_flat(e,record,obs,GEN)
    assert all(proof.values()), proof
    preserved=('loss_budget','gross_losses','net','attempts','completed','loss_latched','entry_cutoff_latched','closed')
    before=(db.settings(),{k:db.session(e.session().day)[k] for k in preserved},C.strategy_fingerprint(e.config))
    assert run_recovery(e,b)
    assert not (e.runtime/'KILL').exists() and not b.calls and not b.cancels
    after=(db.settings(),{k:db.session(e.session().day)[k] for k in preserved},C.strategy_fingerprint(e.config))
    assert after==before
    assert db.get('last_system_recovery')['episode']==record['episode']
    # The existing deterministic ORB/risk engine, never recovery, generates entries.
    tick(e,b)
    assert len(b.calls)==2
    tick(e,b)
    assert len(b.calls)==2


@pytest.mark.parametrize('block',['STOP','TRANSITION','LIQUIDATE','MAINTENANCE','READ_ONLY','ENTRIES_DISABLED'])
def test_operator_controls_never_cleared(recovery,block):
    e,b,db,record,obs=recovery;(e.runtime/block).write_text('synthetic operator request')
    assert not run_recovery(e,b)
    assert (e.runtime/'KILL').exists() and (e.runtime/block).exists() and not b.calls


@pytest.mark.parametrize('text',['Operator emergency kill','Worker exited; reconciliation/recovery required','{}','not-json'])
def test_unknown_legacy_and_operator_provenance_fail_closed(recovery,text):
    e,b,db,record,obs=recovery;(e.runtime/'KILL').write_text(text)
    assert not run_recovery(e,b)
    record_hold(e.runtime,db,'WATCHDOG_STALL',2,'another',b.time)
    assert (e.runtime/'KILL').read_text()==text


@pytest.mark.parametrize('change',[
    'quote_stale','quote_future','missing_bars','missing_vwap','wrong_session','old_observation',
    'cached_snapshot','old_snapshot','clock','watchdog','generation','worker','short_stability',
    'configuration','permission','opening','connectivity','loss','attempts','cutoff','closed',
    'unexpected_spy','unexpected_qqq','unexpected_order','unknown_account','missing_positions',
    'missing_orders','database','provenance','no_quiet_period','future_episode',
])
def test_each_recovery_gate_fails_closed(recovery,change):
    e,b,db,r,o=recovery;o=deepcopy(o)
    day=db.session(e.session().day)
    if change.startswith('quote_'):
        o['quotes']['SPY']['venue_bid_time']=iso(b.time+timedelta(seconds=-6 if change=='quote_stale' else 1))
    elif change=='missing_bars':e.last_bars['QQQ']=[]
    elif change=='missing_vwap':
        for bar in e.last_bars['QQQ']:bar['volume']='0'
    elif change=='wrong_session':day['day']='1999-01-01'
    elif change=='old_observation':o['at']=iso(b.time-timedelta(seconds=6))
    elif change=='cached_snapshot':o['full_reconciliation']=False
    elif change=='old_snapshot':o['snapshot']['observed_at']=iso(b.time-timedelta(seconds=6))
    elif change=='clock':
        p=e.runtime/'host-health.json';h=json.loads(p.read_text());h['clock']['offset_seconds']=1;p.write_text(json.dumps(h))
    elif change in ('watchdog','worker'):(e.runtime/('supervisor-health.json' if change=='watchdog' else 'heartbeat.json')).unlink()
    elif change in ('generation','short_stability'):
        w=db.get('worker_identity');w.update({'generation':'wrong'} if change=='generation' else {'started_at':iso(b.time)});db.put('worker_identity',w)
    elif change=='configuration':
        c=deepcopy(e.config);c['trading']['capital_per_trade']='9';C.write(e.root,c)
    elif change=='permission':db.configure('live_enabled',False)
    elif change=='opening':day['opening_healthy']=False
    elif change=='connectivity':day['connectivity_halt']=True
    elif change=='loss':day['loss_latched']=True
    elif change=='attempts':day['attempts']=5
    elif change=='cutoff':day['entry_cutoff_latched']=True
    elif change=='closed':day['closed']=True
    elif change.startswith('unexpected_'):
        if change=='unexpected_order':o['snapshot']['orders'].append({'state':'confirmed'})
        else:o['snapshot']['positions'].append(dict(symbol=change.split('_')[1].upper(),quantity='1',shares_available_for_sells='1'))
    elif change=='unknown_account':o['snapshot']['account']['account_number']='synthetic-unexpected'
    elif change=='missing_positions':del o['snapshot']['positions']
    elif change=='missing_orders':del o['snapshot']['orders']
    elif change=='database':db.db.execute('DROP TABLE orders')
    elif change=='provenance':r=dict(r,automatic_candidate=False)
    elif change=='no_quiet_period':day['last_failure']=iso(b.time)
    elif change=='future_episode':r=dict(r,updated_at=iso(b.time+timedelta(seconds=1)));db.put('system_hold',r)
    db.save_session(e.session().day,day)
    if change=='database':
        import sqlite3
        with pytest.raises(sqlite3.DatabaseError):prove_flat(e,r,o,GEN)
    else:assert not all(prove_flat(e,r,o,GEN).values())
    assert (e.runtime/'KILL').exists() and not b.calls


@pytest.mark.parametrize('state',['RESERVED','DISPATCHING','AMBIGUOUS','SUBMITTED','PARTIAL'])
@pytest.mark.parametrize('symbol',['SPY','QQQ'])
def test_any_symbol_pending_intent_blocks_global_recovery(recovery,state,symbol):
    e,b,db,r,o=recovery
    # Existing KILL prevents normal reservation; create fixture while disarmed from broker writes.
    (e.runtime/'KILL').unlink();oid=reserve(e,b,symbol);(e.runtime/'KILL').write_text(json.dumps(r))
    with db.tx():db.update_order(oid,state)
    assert not run_recovery(e,b) and (e.runtime/'KILL').exists() and not b.calls


@pytest.mark.parametrize('symbols',[['SPY'],['QQQ'],['SPY','QQQ']])
def test_positions_keep_entry_hold_and_existing_protective_exit(recovery,symbols):
    e,b,db,r,o=recovery;(e.runtime/'KILL').unlink()
    for symbol in symbols:
        oid=reserve(e,b,symbol)
        asyncio.run(e.preflight(b,oid,raw(b.time),eligible_symbols()))
    (e.runtime/'KILL').write_text(json.dumps(r))
    assert not run_recovery(e,b)
    # Existing position-management semantics remain authoritative, including KILL exit.
    tick(e,b)
    assert not db.trades() and len(b.calls)==2*len(symbols)
    assert (e.runtime/'KILL').exists()


@pytest.mark.parametrize('later_low,later_high',[('99.5','100.5'),('98','99.9')])
def test_recovery_after_trigger_or_invalidation_never_rearms(recovery,later_low,later_high):
    e,b,db,r,o=recovery
    b.time+=timedelta(minutes=5);healthy_files(e,b)
    bars=raw(b.time)
    for sym in bars:
        extra=dict(bars[sym][-1],begins_at=iso(e.session().open+timedelta(minutes=15)),
            low_price=later_low,high_price=later_high,open_price='99.7',close_price='99.8')
        bars[sym].append(extra)
    assert asyncio.run(recover_flat(e,b,bars,eligible_symbols(),GEN))
    asyncio.run(e.tick(b,bars,eligible_symbols()))
    assert not b.calls
    assert all(c['state']=='MISSED' for c in db.get('observation')['candidates'].values())
    assert not db.orders() and not db.trades()


@pytest.mark.parametrize('reason',['WORKER_EXIT','CYCLE_DEADLINE','DATABASE_FAILURE','AUTHENTICATION_FAILURE','UNCAUGHT_SOFTWARE_EXCEPTION','CONTROLLED_SHUTDOWN','OPERATOR_STOP','POSITION_MANAGEMENT_FAILURE'])
def test_unproven_worker_exit_is_never_automatically_recoverable(env,reason):
    e,b,db=env;r=record_hold(e.runtime,db,reason,42,OLD,b.time)
    assert r['operator_required'] and not r['automatic_candidate']


def test_watchdog_root_cause_survives_observed_sigkill(recovery):
    e,b,db,r,o=recovery
    observed=record_hold(e.runtime,db,'WORKER_EXIT',41,OLD,b.time,exit_code=-9)
    assert observed==r and observed['initial_reason_code']=='WATCHDOG_STALL'


def test_budget_survives_slow_restarts_and_midnight(recovery):
    e,b,db,r,o=recovery
    for n in range(2):r=record_hold(e.runtime,db,'WATCHDOG_STALL',100+n,'new-'+str(n),b.time+timedelta(days=1,minutes=n*10))
    assert r['failures']==3 and r['operator_required']
    later=record_hold(e.runtime,db,'WATCHDOG_STALL',200,'again',b.time+timedelta(days=2))
    assert later['operator_required'] and later['initial_reason_code']==r['initial_reason_code'] and later['failures']==4


@pytest.mark.parametrize('failure',[httpx.RemoteProtocolError('ConnectionTerminated synthetic stream 73'),httpx.ReadTimeout('synthetic timeout'),httpx.ConnectError('synthetic DNS')])
def test_known_read_transport_exit_requires_proven_flatness(env,failure):
    e,b,db=env;d=classify(failure,{'tool':'get_equity_orders'})
    r=record_hold(e.runtime,db,'READ_TRANSPORT_EXIT',42,OLD,b.time,failure=d)
    assert r['automatic_candidate'] and not r['mutation_in_flight']


@pytest.mark.parametrize('side',['buy','sell','cancel'])
def test_mutation_failure_is_never_a_read_recovery(env,side):
    e,b,db=env
    r=record_hold(e.runtime,db,'READ_TRANSPORT_EXIT',42,OLD,b.time,failure={
        'operation':'MUTATION','failure_class':'AMBIGUOUS_MUTATING_ORDER_STATE','side':side})
    assert r['operator_required']


def test_hard_failure_remains_sticky_after_flat_reads(recovery):
    e,b,db,r,o=recovery
    db.event(b.time,'HEALTH_FAILURE',{'diagnostic':{'failure_class':'OWNERSHIP_POSITION_MISMATCH'}})
    assert not run_recovery(e,b) and (e.runtime/'KILL').exists()


def test_operator_hold_replaced_during_broker_read_is_preserved(recovery):
    e,b,db,r,o=recovery;original=b.snapshot
    async def replacing(*args):
        result=await original(*args);(e.runtime/'KILL').write_text('Operator emergency hold');return result
    b.snapshot=replacing
    assert not run_recovery(e,b)
    assert (e.runtime/'KILL').read_text()=='Operator emergency hold'


def test_clock_data_and_reconciliation_sequence_recovers_only_after_proof(recovery):
    e,b,db,r,o=recovery
    (e.runtime/'host-health.json').unlink()
    assert not run_recovery(e,b)
    b.time+=timedelta(seconds=31);healthy_files(e,b)
    assert run_recovery(e,b) and not b.calls


def test_new_outage_does_not_reuse_old_episode_start(env):
    e,b,db=env;day=db.session(e.session().day)
    day.update(outage_active=False,outage_started=iso(b.time-timedelta(hours=4)))
    db.save_session(e.session().day,day)
    e.failure(httpx.RemoteProtocolError('synthetic GOAWAY'),{'tool':'get_equity_orders'})
    row=db.db.execute("SELECT data FROM events WHERE kind='HEALTH_FAILURE' ORDER BY id DESC LIMIT 1").fetchone()
    assert json.loads(row[0])['diagnostic']['failure_class']=='TRANSIENT_READ_TRANSPORT_FAILURE'


def test_flat_crash_loop_stops_until_deliberately_disarmed(recovery):
    from trader.persistent.system_recovery import restart_permitted
    e,b,db,r,o=recovery
    for n in range(2):record_hold(e.runtime,db,'WATCHDOG_STALL',100+n,'crash-'+str(n),b.time+timedelta(minutes=10*n))
    assert not restart_permitted(e.runtime,db)
    # Restarting the supervisor or passing a rolling-window boundary cannot reset it.
    assert not restart_permitted(e.runtime,db)
    db.configure('live_enabled',False)
    assert not restart_permitted(e.runtime,db)
    (e.runtime/'READ_ONLY').write_text('operator diagnostic disarm')
    assert restart_permitted(e.runtime,db)
    assert read_hold(e.runtime)['operator_required']


@pytest.mark.parametrize('minutes',[315,360,375])
def test_entry_cutoff_and_eod_never_recover(recovery,minutes):
    e,b,db,r,o=recovery
    b.time=e.session().open+timedelta(minutes=minutes+15)
    healthy_files(e,b)
    assert not run_recovery(e,b) and (e.runtime/'KILL').exists() and not b.calls


@pytest.mark.parametrize('kind',['AUTHENTICATION_FAILURE','OWNERSHIP_POSITION_MISMATCH','AMBIGUOUS_MUTATING_ORDER_STATE'])
def test_read_failure_during_certification_escalates(recovery,kind):
    from trader.persistent.recovery import RecoveryError
    e,b,db,r,o=recovery;b.error=RecoveryError(kind,'synthetic certification failure')
    with pytest.raises(RecoveryError):run_recovery(e,b)
    assert read_hold(e.runtime)['operator_required'] and not b.calls
    b.error=None;b.time+=timedelta(seconds=31);healthy_files(e,b)
    assert not run_recovery(e,b)


def test_marker_changed_before_unlink_cannot_be_cleared(recovery,monkeypatch):
    import trader.persistent.system_recovery as module
    e,b,db,r,o=recovery
    original=module.prove_flat
    def replacement(*args):
        proof=original(*args);(e.runtime/'KILL').write_text('operator emergency hold');return proof
    monkeypatch.setattr(module,'prove_flat',replacement)
    assert not run_recovery(e,b)
    assert (e.runtime/'KILL').read_text()=='operator emergency hold'


def test_recovery_abort_before_unlink_retains_hold(recovery,monkeypatch):
    e,b,db,r,o=recovery;event=db.event
    def failing(at,kind,data):
        if kind=='SYSTEM_RECOVERY_PROVEN':raise RuntimeError('synthetic storage failure')
        return event(at,kind,data)
    monkeypatch.setattr(db,'event',failing)
    with pytest.raises(RuntimeError):run_recovery(e,b)
    assert (e.runtime/'KILL').exists() and not b.calls


def test_transport_diagnostics_do_not_turn_mutation_into_read():
    import socket
    dns=httpx.ConnectError('synthetic resolution');dns.__cause__=socket.gaierror('synthetic resolver failure')
    assert classify(dns,{'tool':'get_accounts'})['transport_kind']=='DNS_FAILURE'
    for exception,kind in [(httpx.ReadTimeout('synthetic'),'TIMEOUT'),
                           (httpx.RemoteProtocolError('abrupt synthetic close'),'REMOTE_PROTOCOL_FAILURE')]:
        d=classify(exception,{'tool':'place_equity_order'})
        assert d['transport_kind']==kind and d['failure_class']=='AMBIGUOUS_MUTATING_ORDER_STATE'


def test_held_position_at_failure_requires_operator_even_after_flat(recovery):
    e,b,db,r,o=recovery;(e.runtime/'KILL').unlink();tick(e,b)
    record=record_hold(e.runtime,db,'WATCHDOG_STALL',22,'held-worker',b.time)
    assert record['positions_at_failure']==2 and record['operator_required']
    tick(e,b)
    assert not db.trades() and not run_recovery(e,b)


def test_unknown_auth_account_transport_never_gets_auto_permission(env):
    e,b,db=env
    response=httpx.Response(401,request=httpx.Request('GET','https://example.invalid'))
    error=httpx.HTTPStatusError('synthetic auth failure',request=response.request,response=response)
    d=classify(error,{'tool':'get_accounts'})
    assert d['transport_kind']=='AUTHENTICATION'
    r=record_hold(e.runtime,db,'READ_TRANSPORT_EXIT',22,OLD,b.time,failure=d)
    assert r['operator_required']


def test_unknown_crash_loops_also_exhaust_budget(env):
    from trader.persistent.system_recovery import restart_permitted
    e,b,db=env
    for n in range(3):record_hold(e.runtime,db,'WORKER_EXIT',500+n,'crash-'+str(n),b.time+timedelta(minutes=10*n))
    assert not restart_permitted(e.runtime,db)
    record=read_hold(e.runtime)
    assert record['reason_code']=='WORKER_EXIT' and record['operator_required'] and record['failures']==3


def test_synthetic_transport_restart_opening_sequence(recovery):
    e,b,db,r,o=recovery
    original=b.time
    # Use a fresh synthetic episode, with no production record or copied telemetry.
    (e.runtime/'KILL').unlink();db.put('system_recovery_budget',{})
    b.time=original-timedelta(minutes=30)
    failure=classify(httpx.RemoteProtocolError('GOAWAY synthetic stream 73'),{'tool':'get_portfolio'})
    e.failure(httpx.RemoteProtocolError('GOAWAY synthetic stream 73'),{'tool':'get_portfolio'})
    record_hold(e.runtime,db,'READ_TRANSPORT_EXIT',41,OLD,b.time,failure=failure)
    db.recover(b.time+timedelta(seconds=1))
    assert not run_recovery(e,b) and not b.calls
    b.time=original;healthy_files(e,b)
    # Restored transport and flat reconciliation alone cannot manufacture opening proof.
    first=asyncio.run(e.observe(b,raw(b.time),eligible_symbols(),full=True))
    assert not all(prove_flat(e,read_hold(e.runtime),first,GEN).values())
    # Preserve existing thresholds: 30 observations and 300 seconds of healthy data.
    day=db.session(e.session().day)
    day.update(stable_since=iso(b.time-timedelta(seconds=301)),successes=29)
    db.save_session(e.session().day,day)
    assert run_recovery(e,b)
    assert not b.calls and db.session(e.session().day)['opening_healthy']


def test_corrupted_database_never_starts_recovery(tmp_path):
    import sqlite3
    from trader.persistent.db import Ledger
    path=tmp_path/'corrupt.sqlite3';path.write_bytes(b'SQLite format 3\x00' + b'corrupt synthetic page'*500)
    with pytest.raises(sqlite3.DatabaseError):Ledger(path)


def test_waiting_recovery_does_not_extend_hold_origin(recovery):
    e,b,db,r,o=recovery;created=r['created_at']
    for i in range(4):
        b.time+=timedelta(seconds=31)
        # Stale host evidence deliberately remains stale; retries cannot confer health.
        assert not run_recovery(e,b)
        assert read_hold(e.runtime)['created_at']==created
    assert not b.calls


def test_operator_transition_cannot_remove_a_new_kill(tmp_path,monkeypatch):
    from trader import doctor
    from trader.persistent.db import Ledger
    from trader.models import UTC
    from datetime import datetime
    r=tmp_path/'runtime';r.mkdir();C.write(tmp_path,C.validate({}))
    db=Ledger(r/'persistent.sqlite3');C.apply(tmp_path,db,datetime.now(UTC));db.close()
    calls=[]
    async def check(*args):
        calls.append(1)
        if len(calls)==2:(r/'KILL').write_text('Operator emergency replacement')
        return {'ready_for_transition':True,'blockers':[],'config_fingerprint':C.fingerprint(C.load(tmp_path))}
    monkeypatch.setattr(doctor,'doctor',check);monkeypatch.setattr(doctor,'require_host',lambda *args:None)
    with pytest.raises(Unsafe,match='KILL changed'):asyncio.run(doctor.transition(tmp_path,True,True))
    assert (r/'KILL').read_text()=='Operator emergency replacement'
    assert (r/'TRANSITION').exists() and (r/'READ_ONLY').exists()
