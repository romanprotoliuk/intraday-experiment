"""Synthetic reliability regressions; no credentials, production data or network."""
import asyncio
from datetime import timedelta
import json
import httpx
import pytest
from trader.models import Unsafe, iso
from trader.persistent import system_recovery as R
from trader.persistent.recovery import classify
from trader import review_successor as S, legacy_recovery as L
from test_persistent import env
from test_system_recovery import recovery, healthy_files
from test_next_session_readiness import maintenance, reviewed
from test_review_successor import incident, recon, fresh, create

@pytest.mark.parametrize('marker',['legacy historical hold','operator stop','malformed-json'])
def test_preserved_kill_still_bounds_restarts(recovery,marker):
    e,b,db,_,_=recovery
    (e.runtime/'KILL').write_text(marker)
    original=(e.runtime/'KILL').stat()
    for n in range(3):
        R.record_hold(e.runtime,db,'WATCHDOG_STALL',200+n,'synthetic-'+str(n),b.time)
        R.record_hold(e.runtime,db,'WORKER_EXIT',200+n,'synthetic-'+str(n),b.time,exit_code=-9)
    assert not R.restart_permitted(e.runtime,db)
    assert (e.runtime/'KILL').read_text()==marker
    assert (e.runtime/'KILL').stat().st_mtime_ns==original.st_mtime_ns
    assert not b.calls and not b.cancels

@pytest.mark.parametrize('error',[ValueError('unknown'),RuntimeError('unknown'),Unsafe('unknown')])
def test_mixed_read_exception_is_not_benign_transport(error):
    exc=ExceptionGroup('synthetic',[httpx.ReadTimeout('read unavailable'),error])
    assert classify(exc,{'tool':'get_equity_orders'})['failure_class']=='RECONCILIATION_FAILURE'


def test_new_investigation_can_follow_expired_receipt_without_reusing_it(incident):
    f,parent=incident;e,b,db,_,_=f
    b.time=e.session().close+timedelta(minutes=2)
    fresh(e,b,db);recon(db,b.time)
    record=create(incident)
    assert record['parent']==parent and db.get(L.KEY)==parent
    assert record['expires_at']>parent['expires_at']
    assert not L.valid_review(e.root,db.db,parent,b.time)
    assert L.valid_review(e.root,db.db,record,b.time)
    assert (e.runtime/'KILL').exists() and not b.calls and not b.cancels


def test_expired_receipt_never_skips_hard_intervening_event(incident):
    f,parent=incident;e,b,db,_,_=f
    b.time=e.session().close+timedelta(minutes=2);fresh(e,b,db);recon(db,b.time)
    db.event(b.time,'WORKER_FATAL',dict(generation='synthetic',exception={'type':'RuntimeError'}))
    with pytest.raises(Unsafe):create(incident)
    assert db.get(S.HEAD) is None and db.get(L.KEY)==parent


def test_historical_preserved_stalls_count_without_mutating_ledger(recovery):
    e,b,db,_,_=recovery;(e.runtime/'KILL').write_text('legacy')
    for n in range(3):
        for reason in ('WATCHDOG_STALL','WORKER_EXIT'):
            db.event(b.time,'SYSTEM_HOLD_PRESERVED',dict(reason_code=reason,legacy_or_operator=True))
            db.event(b.time,reason,dict(pid=500+n,worker_generation='old-'+str(n)) if reason=='WATCHDOG_STALL' else dict(pid=500+n,exit_code=-9))
    before=db.db.total_changes
    assert R.preserved_failure_evidence(db.db)['failures']==3
    assert not R.restart_permitted(e.runtime,db) and db.db.total_changes==before
    b.time+=timedelta(days=1)
    assert not R.restart_permitted(e.runtime,db)


def test_legacy_budget_never_disables_position_monitoring(recovery):
    e,b,db,_,_=recovery;(e.runtime/'KILL').write_text('legacy')
    for n in range(3):R.record_hold(e.runtime,db,'WATCHDOG_STALL',300+n,'g-'+str(n),b.time)
    db.db.execute("insert into trades values('t','SPY','2000-01-01','OPEN','{}')")
    assert R.restart_permitted(e.runtime,db)


def test_failed_host_does_not_hide_broker_and_other_control_evidence(maintenance):
    from trader import readiness
    e,b,db,_,_=maintenance
    p=e.runtime/'host-health.json';data=json.loads(p.read_text());data['ac_power']=False;p.write_text(json.dumps(data))
    proof=asyncio.run(readiness.control_snapshot(e.root,now=lambda:b.time))
    assert not proof['checks']['host_clock']
    assert proof['checks']['broker'] is True and proof['controls']==['KILL']
    assert not proof['passed']


@pytest.mark.parametrize('field,value',[('net','1'),('loss_budget','99'),('attempts',1),('completed',1),('opening_healthy',True),('connectivity_halt',True),('successes',1),('config_fingerprint','wrong'),('strategy_fingerprint','wrong'),('gross_losses','0.01')])
def test_next_session_readiness_rejects_stale_accounting_and_certification(recovery,field,value):
    from trader.readiness import session_initial_state
    e,b,db,_,_=recovery;s=e.calendar.session(b.time+timedelta(days=1));day=db.ensure_session(s.day)
    assert session_initial_state(day,s,e.config)
    day[field]=value
    assert not session_initial_state(day,s,e.config)


def test_controlled_flat_restart_is_reinvestigated_not_silently_whitelisted(maintenance,monkeypatch):
    e,b,db,_,_=maintenance
    monkeypatch.setattr(S,'code_identity',lambda root:dict(commit='synthetic',source_digest='source'))
    db.event(b.time,'PERSISTENT_WORKER_START',dict(pid=99,generation='old',supervisor_pid=8))
    recon(db,b.time);parent=reviewed(maintenance)
    b.time+=timedelta(seconds=1)
    db.event(b.time,'WORKER_STOP',dict(pid=99,generation='old',controlled=True,open_positions=0,unresolved_orders=0))
    db.event(b.time,'SUPERVISOR_START',dict(pid=9,architecture='persistent-v2'))
    db.event(b.time,'RESTART_RECOVERY',dict(open_positions=0,pending=0,failure_class='WORKER_PROCESS_FAILURE'))
    db.event(b.time,'PERSISTENT_WORKER_START',dict(pid=1,generation=db.get('worker_identity')['generation'],supervisor_pid=9,started_at=iso(b.time)))
    b.time+=timedelta(seconds=1);recon(db,b.time);b.time+=timedelta(minutes=6);fresh(e,b,db)
    record=asyncio.run(S.successor(e.root,parent['id'],investigated=True,now=lambda:b.time))
    assert len(record['interval']['controlled_restarts'])==1
    assert db.get(L.KEY)==parent and (e.runtime/'KILL').exists()

@pytest.mark.parametrize('fault',['uncontrolled','position','order','wrong_generation','missing_start','wrong_supervisor','same_generation','slow_reconciliation'])
def test_controlled_restart_review_rejects_partial_or_unsafe_sequence(maintenance,monkeypatch,fault):
    e,b,db,_,_=maintenance
    monkeypatch.setattr(S,'code_identity',lambda root:dict(commit='synthetic',source_digest='source'))
    db.event(b.time,'PERSISTENT_WORKER_START',dict(pid=1,generation='old',supervisor_pid=8))
    recon(db,b.time);parent=reviewed(maintenance);b.time+=timedelta(seconds=1)
    stop=dict(pid=1,generation='old',controlled=True,open_positions=0,unresolved_orders=0)
    if fault=='uncontrolled':stop['controlled']=False
    if fault=='position':stop['open_positions']=1
    if fault=='order':stop['unresolved_orders']=1
    if fault=='wrong_generation':stop['generation']='unknown'
    db.event(b.time,'WORKER_STOP',stop)
    db.event(b.time,'SUPERVISOR_START',dict(pid=9,architecture='persistent-v2'))
    db.event(b.time,'RESTART_RECOVERY',dict(open_positions=0,pending=0,failure_class='WORKER_PROCESS_FAILURE'))
    if fault!='missing_start':db.event(b.time,'PERSISTENT_WORKER_START',dict(pid=2,generation='old' if fault=='same_generation' else 'new',supervisor_pid=99 if fault=='wrong_supervisor' else 9,started_at=iso(b.time)))
    b.time+=timedelta(seconds=50 if fault=='slow_reconciliation' else 1);recon(db,b.time)
    b.time+=timedelta(minutes=6);fresh(e,b,db)
    with pytest.raises(Unsafe):asyncio.run(S.successor(e.root,parent['id'],investigated=True,now=lambda:b.time))
    assert db.get(S.HEAD) is None and db.get(L.KEY)==parent


def test_multiple_successors_across_sessions_preserve_chain(incident):
    f,parent=incident;e,b,db,_,_=f
    first=create(incident)
    b.time=e.session().close+timedelta(minutes=1);fresh(e,b,db);recon(db,b.time)
    second=asyncio.run(S.successor(e.root,first['id'],investigated=True,now=lambda:b.time))
    assert second['parent_id']==first['id'] and db.get(S.PREFIX+first['id'])==first and db.get(L.KEY)==parent
    assert second['interval']['start_exclusive']==parent['watermark']
    assert second['interval']['end_inclusive']>first['watermark']
    assert L.valid_review(e.root,db.db,second,b.time)
    assert not L.valid_review(e.root,db.db,first,b.time)


@pytest.mark.parametrize('operation',['get_portfolio','get_equity_positions','get_equity_orders','place_equity_order','cancel_equity_order'])
@pytest.mark.parametrize('fault',['timeout','connection_reset','dns','mixed_unknown'])
def test_read_mutation_partition_invariant(operation,fault):
    import socket
    exc={'timeout':TimeoutError('synthetic'),'connection_reset':ConnectionResetError('synthetic'),'dns':socket.gaierror('synthetic'),'mixed_unknown':ExceptionGroup('synthetic',[httpx.ReadTimeout('synthetic'),ValueError('unexplained')])}[fault]
    result=classify(exc,dict(tool=operation))
    expected='AMBIGUOUS_MUTATING_ORDER_STATE' if not operation.startswith('get_') else 'RECONCILIATION_FAILURE' if fault=='mixed_unknown' else 'TRANSIENT_READ_TRANSPORT_FAILURE'
    assert result['failure_class']==expected


def test_readiness_empty_root_has_all_dimensions_without_creating_state(tmp_path):
    from trader.readiness import certify
    before=list(tmp_path.iterdir());r=asyncio.run(certify(tmp_path,broker_check=False))
    for k in ('ENGINEERING_READY','DEPLOYMENT_READY','INFRASTRUCTURE_READY','MONITORING_READY','CONTROL_READY','SESSION_READY','UNATTENDED_LIVE_READY'):assert r[k] is False
    assert list(tmp_path.iterdir())==before


def test_resolved_hold_does_not_block_unrelated_no_hold_startup(recovery):
    e,b,db,_,_=recovery;(e.runtime/'KILL').write_text('synthetic legacy')
    for n in range(3):R.record_hold(e.runtime,db,'WATCHDOG_STALL',300+n,'g-'+str(n),b.time)
    assert not R.restart_permitted(e.runtime,db)
    # Test fixture represents an independently audited completed hold transition.
    # No test writes a production marker or calls a broker.
    (e.runtime/'KILL').unlink()
    assert R.restart_permitted(e.runtime,db)
    assert R.preserved_failure_evidence(db.db)['failures']==3


def test_disarmed_held_diagnostics_remain_awake_overnight(recovery):
    from trader.persistent.runtime import needs_awake
    e,b,db,_,_=recovery
    db.configure('live_enabled',False)
    b.time=e.session().close+timedelta(hours=2)
    (e.runtime/'READ_ONLY').write_text('synthetic diagnostic control')
    assert needs_awake(e.runtime,db,e.session(),b.time,e.config)
    (e.runtime/'KILL').unlink()
    assert not needs_awake(e.runtime,db,e.session(),b.time,e.config)


def test_classified_read_cannot_hide_unknown_sibling():
    from trader.persistent.recovery import RecoveryError,Failure
    exc=ExceptionGroup('synthetic',[RecoveryError(Failure.TRANSIENT_READ_TRANSPORT_FAILURE,'known read'),ValueError('unknown')])
    assert classify(exc,{'tool':'get_equity_orders'})['failure_class']=='RECONCILIATION_FAILURE'

@pytest.mark.parametrize('tool',['get_equity_quotes','get_equity_tradability','get_equity_historicals'])
def test_known_market_data_read_uses_same_investigated_unavailability_proof(incident,tool):
    from test_review_successor import diagnostic
    f,parent=incident;e,b,db,_,_=f
    recon(db,b.time);d=diagnostic('TRANSIENT_READ_TRANSPORT_FAILURE');d['tool']=tool
    db.event(b.time,'HEALTH_FAILURE',dict(diagnostic=d));b.time+=timedelta(seconds=1);recon(db,b.time)
    b.time+=timedelta(minutes=6);fresh(e,b,db)
    record=create(incident)
    assert record['interval']['resolved_reads'][-1]['tools']==[tool]
    assert db.get(L.KEY)==parent and (e.runtime/'KILL').exists()
