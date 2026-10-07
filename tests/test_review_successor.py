"""Synthetic incident lifecycle; temporary DB, fake broker, no production data."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
import sqlite3

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from trader import legacy_recovery as L, review_successor as S, configuration as C
from trader.models import Unsafe, iso
from trader.persistent.db import Ledger
from trader.persistent.recovery import classify, begin, advance, REQUIRED_EVIDENCE
from trader.store import atomic_text
from test_next_session_readiness import maintenance, reviewed, acknowledge
from test_system_recovery import recovery, healthy_files
from test_persistent import env, raw, tick, eligible_symbols


def diagnostic(kind='RECONCILIATION_FAILURE'):
    leaf=dict(type='McpError',message='Timed out while waiting for response to ClientRequest. Waited 4.0 seconds.',mcp_code=408,
              frames=[dict(file='broker.py',function='_request',line=12),dict(file='session.py',function='send_request',line=S._sdk_deadline_line())])
    return dict(failure_class=kind,operation='READ',tool='get_equity_orders',exception=leaf)


def recon(db,at,**changes):
    value=dict(account_suffix=db.get('production_config')['broker']['account_suffix'],positions=[],orders=[],snapshot_at=iso(at-timedelta(milliseconds=500)),data_healthy=False)
    value.update(changes);db.event(at,'RECONCILIATION',value)


def fresh(e,b,db):
    healthy_files(e,b)
    h=json.loads((e.runtime/'heartbeat.json').read_text());h['at']=b.time.timestamp();(e.runtime/'heartbeat.json').write_text(json.dumps(h))
    db.put('health',dict(at=iso(b.time),auth='AUTHENTICATED_READ_VERIFIED',reconciled=True,data_healthy=False))


@pytest.fixture
def incident(maintenance,monkeypatch):
    e,b,db,_,_=maintenance
    monkeypatch.setattr(S,'code_identity',lambda root:dict(commit='synthetic-committed-build',source_digest='synthetic-source-digest'))
    recon(db,b.time-timedelta(seconds=1))
    parent=reviewed(maintenance)
    b.time+=timedelta(seconds=1);db.event(b.time,'HEALTH_FAILURE',dict(diagnostic=diagnostic()))
    b.time+=timedelta(seconds=3);recon(db,b.time)
    b.time+=timedelta(minutes=6);fresh(e,b,db)
    return maintenance,parent


def create(incident):
    f,parent=incident;e,b,db,_,_=f
    return asyncio.run(S.successor(e.root,parent['id'],investigated=True,now=lambda:b.time))


def stable_state(e,db):
    return dict(hold=L.hold_identity(e.runtime),settings=db.get('settings'),sessions=[tuple(r) for r in db.db.execute('select day,data from sessions')],
                orders=[tuple(r) for r in db.db.execute('select * from orders')],trades=[tuple(r) for r in db.db.execute('select * from trades')])


def test_synthetic_three_read_timeout_regression_append_only(incident):
    f,parent=incident;e,b,db,_,calls=f;before=stable_state(e,db)
    history=[tuple(r) for r in db.db.execute('select * from events')]
    assert not L.valid_review(e.root,db.db,parent,b.time)
    record=create(incident)
    assert record['parent']==parent and db.get(L.KEY)==parent
    assert record['interval']['resolved_reads'][0]['original_class']=='RECONCILIATION_FAILURE'
    assert record['interval']['resolved_reads'][0]['after_event']>record['interval']['resolved_reads'][0]['event_id']
    assert L.valid_review(e.root,db.db,record,b.time) and L.classify_hold(e.root,db.db,b.time)=='C'
    assert stable_state(e,db)==before and len(calls)==2 and not b.calls and not b.cancels
    assert [tuple(r) for r in db.db.execute('select * from events')][:len(history)]==history
    assert record['expires_at']==parent['expires_at']
    # A fresh ledger/worker can load the same immutable receipt; no entry grant.
    opened=Ledger.existing(e.runtime/'persistent.sqlite3')
    try:assert L.valid_review(e.root,opened.db,L.active_review(opened.db),b.time)
    finally:opened.close()
    with pytest.raises(Unsafe):e.health_check(b.time)


def test_valid_review_needs_no_successor(maintenance,monkeypatch):
    e,b,db,_,_=maintenance;parent=reviewed(maintenance)
    with pytest.raises(Unsafe,match='remains valid'):
        asyncio.run(S.successor(e.root,parent['id'],investigated=True,now=lambda:b.time))
    assert db.get(L.KEY)==parent and db.get(S.HEAD) is None


def test_no_reconciliation_after_timeout_refused(incident):
    f,parent=incident;e,b,db,_,_=f
    db.event(b.time,'HEALTH_FAILURE',dict(diagnostic=diagnostic()))
    with pytest.raises(Unsafe):create(incident)
    assert db.get(S.HEAD) is None and db.get(L.KEY)==parent


@pytest.mark.parametrize('kind', ['WORKER_FATAL','WORKER_EXIT','WATCHDOG_STALL','HOST_SLEEP','CLOCK_FAILURE',
    'SYSTEM_HOLD_CREATED','SYSTEM_RECOVERY_ESCALATED','OPERATOR_KILL','ORDER_INTENT','ORDER_ACKNOWLEDGED',
    'ORDER_PARTIAL','UNKNOWN_EVENT','CONFIG_CHANGED','CONFIG_APPLIED','PERSISTENT_WORKER_START','SUPERVISOR_START'])
def test_unexplained_or_hard_intervening_events_rejected(incident,kind):
    f,parent=incident;e,b,db,_,_=f;db.event(b.time,kind,{})
    with pytest.raises(Unsafe):create(incident)
    assert db.get(S.HEAD) is None and db.get(L.KEY)==parent and (e.runtime/'KILL').exists()


@pytest.mark.parametrize('kind',['AMBIGUOUS_MUTATING_ORDER_STATE','OWNERSHIP_POSITION_MISMATCH','DATABASE_FAILURE',
    'UNKNOWN_ACCOUNT','AUTHENTICATION_FAILURE','CLOCK_HEALTH_FAILURE','WORKER_PROCESS_FAILURE'])
def test_hard_health_failures_never_adjudicated_as_read(incident,kind):
    f,parent=incident;e,b,db,_,_=f;recon(db,b.time)
    db.event(b.time,'HEALTH_FAILURE',dict(diagnostic=diagnostic(kind)));recon(db,b.time)
    with pytest.raises(Unsafe):create(incident)
    assert db.get(S.HEAD) is None


@pytest.mark.parametrize('fault',['position','order','partial','missing_positions','stale_snapshot','future_snapshot','ledger_gap','future_event'])
def test_bad_historical_broker_evidence_refused(incident,fault):
    f,parent=incident;e,b,db,_,_=f
    changes={}
    if fault=='position':changes['positions']=[dict(symbol='SPY',quantity='1')]
    if fault in ('order','partial'):changes['orders']=[dict(state='partially_filled' if fault=='partial' else 'confirmed')]
    if fault=='missing_positions':changes['positions']=None
    if fault in ('stale_snapshot','future_snapshot'):changes['snapshot_at']=iso(b.time+timedelta(seconds=1 if fault=='future_snapshot' else -60))
    recon(db,b.time+timedelta(seconds=1 if fault=='future_event' else 0),**changes)
    if fault=='ledger_gap':db.db.execute('delete from events where kind=? and id>?',('HEALTH_FAILURE',parent['watermark']))
    with pytest.raises((Unsafe,TypeError)):create(incident)
    assert db.get(S.HEAD) is None


@pytest.mark.parametrize('fault',['hold','operator_hold','account','config','strategy','budget','unresolved','auth','positions','orders',
    'host','clock','stale_host','future_host','heartbeat','database','stale_broker','future_broker','expiry','generation','missing_history','code'])
def test_current_proof_failures_leave_receipt_and_hold_untouched(incident,fault,monkeypatch):
    f,parent=incident;e,b,db,_,_=f
    if fault=='hold':atomic_text(e.runtime/'KILL',L.TARGET+'\n')
    elif fault=='operator_hold':atomic_text(e.runtime/'KILL','Explicit operator hold')
    elif fault=='account':db.put('account_number','synthetic-different-account')
    elif fault in ('config','strategy'):
        c=deepcopy(e.config);c['trading' if fault=='config' else 'strategy']['capital_per_trade' if fault=='config' else 'target_r']='9' if fault=='config' else '3';C.write(e.root,c)
    elif fault=='budget':db.put('system_recovery_budget',dict(failures=3))
    elif fault=='unresolved':db.db.execute("insert into orders values('synthetic','synthetic','synthetic','buy','AMBIGUOUS',NULL,'{}')")
    elif fault in ('host','clock','stale_host','future_host'):
        p=e.runtime/'host-health.json';v=json.loads(p.read_text())
        if fault=='host':v['ac_power']=False
        elif fault=='clock':v['clock']['offset_seconds']=1
        else:v['at']=iso(b.time+timedelta(seconds=1 if fault=='future_host' else -600))
        p.write_text(json.dumps(v))
    elif fault=='heartbeat':(e.runtime/'heartbeat.json').unlink()
    elif fault=='database':db.db.execute('drop table fills')
    elif fault=='expiry':b.time=e.session().open;fresh(e,b,db)
    elif fault=='generation':v=db.get('worker_identity');v['generation']='unexpected';db.put('worker_identity',v)
    elif fault=='missing_history':db.db.execute('delete from events where id=?',(parent['event_ids'][0],))
    elif fault=='code':monkeypatch.setattr(S,'code_identity',lambda r:(_ for _ in ()).throw(Unsafe('Uncommitted build')))
    else:
        original=b.snapshot
        async def changed(*args):
            if fault=='auth':raise Unsafe('Synthetic authentication failure')
            s=await original(*args)
            if fault=='positions':s['positions']=[dict(symbol='QQQ',quantity='1')]
            elif fault=='orders':s['orders']=[dict(state='confirmed')]
            else:s['observed_at']=iso(b.time+timedelta(seconds=1 if fault=='future_broker' else -60))
            return s
        b.snapshot=changed
    before=(e.runtime/'KILL').read_bytes()
    with pytest.raises((Unsafe,sqlite3.DatabaseError,ValueError,TypeError)):create(incident)
    assert (e.runtime/'KILL').read_bytes()==before and db.get(S.HEAD) is None and db.get(L.KEY)==parent
    assert not b.calls and not b.cancels


def test_repeated_failures_exceed_budget(incident):
    f,parent=incident;e,b,db,_,_=f
    for _ in range(3):
        recon(db,b.time);db.event(b.time,'HEALTH_FAILURE',dict(diagnostic=diagnostic()));recon(db,b.time)
    with pytest.raises(Unsafe,match='budget'):create(incident)


def test_multiple_spaced_incidents_all_included(incident):
    f,parent=incident;e,b,db,_,_=f
    goaway=diagnostic('PROLONGED_CONNECTIVITY_LOSS')
    goaway['exception']=dict(type='ExceptionGroup',frames=[dict(file='broker.py',function='connect')],children=[dict(type='RemoteProtocolError',frames=[dict(file='streamable_http.py',function='_handle_post_request')])])
    for d in (goaway,diagnostic('TRANSIENT_READ_TRANSPORT_FAILURE')):
        recon(db,b.time);db.event(b.time,'HEALTH_FAILURE',dict(diagnostic=d));recon(db,b.time)
        b.time+=timedelta(minutes=6);fresh(e,b,db)
    record=create(incident)
    assert len(record['interval']['resolved_reads'])==3


def test_recovery_beyond_connectivity_horizon_refused(incident):
    f,parent=incident;e,b,db,_,_=f
    recon(db,b.time);db.event(b.time,'HEALTH_FAILURE',dict(diagnostic=diagnostic()))
    b.time+=timedelta(seconds=61);recon(db,b.time);b.time+=timedelta(minutes=6);fresh(e,b,db)
    with pytest.raises(Unsafe,match='horizon'):create(incident)


@pytest.mark.parametrize('race',['new_hold','new_failure','new_generation','parallel_review','stale_proof'])
def test_second_read_races_fail_closed(incident,race):
    f,parent=incident;e,b,db,_,_=f;original=b.snapshot;counter=[0]
    async def snapshot(*args):
        s=await original(*args);counter[0]+=1
        if counter[0]==2:
            if race=='new_hold':atomic_text(e.runtime/'KILL','New operator hold')
            elif race=='new_failure':db.event(b.time,'WORKER_FATAL',{})
            elif race=='new_generation':v=db.get('worker_identity');v['generation']='new';db.put('worker_identity',v)
            elif race=='parallel_review':
                with pytest.raises(Unsafe):await S.successor(e.root,parent['id'],investigated=True,now=lambda:b.time)
            else:b.time+=timedelta(seconds=60)
        return s
    b.snapshot=snapshot
    if race=='parallel_review':assert create(incident)['parent_id']==parent['id']
    else:
        with pytest.raises(Unsafe):create(incident)
        assert db.get(S.HEAD) is None
    assert db.get(L.KEY)==parent and (e.runtime/'KILL').exists() and not b.calls


def test_storage_interruption_is_atomic(incident,monkeypatch):
    f,parent=incident;e,b,db,_,_=f;original=Ledger.event
    def fail(self,now,kind,data):
        if kind=='LEGACY_SUCCESSOR_REVIEWED':raise sqlite3.OperationalError('Synthetic storage failure')
        return original(self,now,kind,data)
    monkeypatch.setattr(Ledger,'event',fail)
    with pytest.raises(sqlite3.OperationalError):create(incident)
    assert db.get(S.HEAD) is None and not db.db.execute("select key from kv where key like 'legacy_review_receipt:%'").fetchall()
    assert db.get(L.KEY)==parent and (e.runtime/'KILL').exists()


def test_successor_consumption_separate_explicit_and_immutable(incident):
    f,parent=incident;e,b,db,_,_=f;record=create(incident)
    assert (e.runtime/'KILL').exists()
    assert acknowledge(f,record)['entry_eligible'] is False
    assert db.get(L.KEY)==parent and db.get(S.PREFIX+record['id'])==record
    assert db.get('legacy_review_status:'+record['id'])['status']=='ACKNOWLEDGED'
    with pytest.raises(Unsafe):acknowledge(f,record)
    assert not b.calls and not b.cancels


@pytest.mark.parametrize('fault',['new_hard','changed_code','receipt','anchor','expire'])
def test_successor_invalidates_without_rewriting_receipts(incident,monkeypatch,fault):
    f,parent=incident;e,b,db,_,_=f;record=create(incident)
    if fault=='new_hard':db.event(b.time,'HEALTH_FAILURE',dict(diagnostic=diagnostic('UNKNOWN_ACCOUNT')))
    elif fault=='changed_code':monkeypatch.setattr(S,'code_identity',lambda root:dict(commit='changed',source_digest='changed'))
    elif fault=='receipt':db.put(S.PREFIX+record['id'],dict(record,status='INVALID'))
    elif fault=='anchor':db.put(L.KEY,dict(parent,event_ids=[]))
    else:b.time=e.session().open
    assert not L.valid_review(e.root,db.db,record,b.time)
    assert (e.runtime/'KILL').exists()


def timeout():
    try:
        try:raise TimeoutError('Synthetic local deadline')
        except TimeoutError:raise McpError(ErrorData(code=408,message='Synthetic SDK deadline'))
    except McpError as exc:return exc


@pytest.mark.parametrize('tool',['get_equity_orders','get_equity_positions','get_portfolio'])
def test_local_sdk_timeout_classification(tool):
    d=classify(ExceptionGroup('Synthetic read batch',[timeout()]),dict(tool=tool))
    assert d['failure_class']=='TRANSIENT_READ_TRANSPORT_FAILURE' and d['transport_kind']=='MCP_LOCAL_READ_TIMEOUT'
    assert classify(timeout(),dict(tool=tool),repeated=True)['failure_class']=='REPEATED_BROKER_COMMUNICATION_FAILURE'


@pytest.mark.parametrize('tool',['place_equity_order','cancel_equity_order'])
def test_timeout_during_mutation_remains_ambiguous(tool):
    assert classify(timeout(),dict(tool=tool))['failure_class']=='AMBIGUOUS_MUTATING_ORDER_STATE'


def test_remote_408_and_mixed_unknown_are_not_local_timeout():
    remote=McpError(ErrorData(code=408,message='Timed out while waiting for response to ClientRequest. Waited 4.0 seconds.'))
    for exc in (remote,ExceptionGroup('mixed',[timeout(),ValueError('Unknown inconsistency')])):
        assert classify(exc,dict(tool='get_equity_orders'))['failure_class']=='RECONCILIATION_FAILURE'
    d=diagnostic();d['exception']['frames'][-1]['line']+=9
    with pytest.raises(Unsafe):S.investigated_read_failure(d)


def test_timeout_with_position_preserves_conservative_exit_and_original_deadline(incident):
    f,_=incident;e,b,_,_,_=f;d=classify(timeout(),dict(tool='get_equity_orders'))
    strict=begin(None,d,b.time,100,15,conservative=True)
    assert strict['state']=='VERIFIED_RISK_REDUCTION'
    bounded=begin(None,d,b.time,100,15)
    retry=begin(bounded,d,b.time+timedelta(seconds=10),110,15)
    assert retry['deadline']==bounded['deadline']
    missing={k:True for k in REQUIRED_EVIDENCE};missing['ownership_reconciled']=False
    state,reason=advance(retry,b.time+timedelta(seconds=16),116,missing)
    assert state['state']=='VERIFIED_RISK_REDUCTION' and reason


@pytest.mark.parametrize('where',['historical','fresh','after_review'])
def test_new_terminal_order_is_not_flat_evidence(incident,where):
    f,parent=incident;e,b,db,_,_=f
    order=dict(id='synthetic-new-order',state='filled',executions=[])
    if where=='historical':
        recon(db,b.time,orders=[order])
        with pytest.raises(Unsafe,match='order history changed'):create(incident)
    elif where=='fresh':
        original=b.snapshot
        async def changed(*args):
            s=await original(*args);s['orders']=[order];return s
        b.snapshot=changed
        with pytest.raises(Unsafe):create(incident)
    else:
        record=create(incident);recon(db,b.time,orders=[order])
        assert not L.valid_review(e.root,db.db,record,b.time)
    assert (e.runtime/'KILL').exists() and db.get(L.KEY)==parent and not b.calls


def test_intervening_account_suffix_change_rejected(incident):
    f,parent=incident;e,b,db,_,_=f
    recon(db,b.time,account_suffix='synthetic-wrong')
    with pytest.raises(Unsafe,match='account binding'):create(incident)


def test_explicit_successor_attestation_required(incident):
    f,parent=incident;e,b,db,_,_=f
    with pytest.raises(Unsafe,match='investigation'):
        asyncio.run(S.successor(e.root,parent['id'],now=lambda:b.time))
    assert db.get(S.HEAD) is None


def test_known_read_after_receipt_blocks_consumption_until_worker_reconciles(incident):
    f,parent=incident;e,b,db,_,_=f;record=create(incident)
    db.event(b.time,'HEALTH_FAILURE',dict(diagnostic=diagnostic('TRANSIENT_READ_TRANSPORT_FAILURE')))
    db.put('health',dict(at=iso(b.time),auth='UNAVAILABLE_OR_UNRECONCILED',reconciled=False))
    assert L.valid_review(e.root,db.db,record,b.time)
    with pytest.raises(Unsafe):acknowledge(f,record)
    assert (e.runtime/'KILL').exists()


def test_successor_of_successor_preserves_entire_chain(incident):
    f,parent=incident;e,b,db,_,_=f;first=create(incident)
    recon(db,b.time);db.event(b.time,'HEALTH_FAILURE',dict(diagnostic=diagnostic()));recon(db,b.time)
    b.time+=timedelta(minutes=6);fresh(e,b,db)
    second=create((f,first))
    assert second['parent']==first and db.get(S.PREFIX+first['id'])==first
    assert db.get(L.KEY)==parent and L.valid_review(e.root,db.db,second,b.time)


def test_market_hours_successor_creation_is_forbidden(incident):
    f,parent=incident;e,b,db,_,_=f;b.time=e.session().open+timedelta(minutes=1)
    with pytest.raises(Unsafe,match='outside regular'):create(incident)
    assert (e.runtime/'KILL').exists()


def test_read_timeout_flat_reconciliation_does_not_certify_opening(maintenance):
    e,b,db,_,_=maintenance;record=reviewed(maintenance)
    e.failure(timeout(),context=dict(tool='get_equity_orders'))
    assert db.get('health')['reconciled'] is False
    assert L.valid_review(e.root,db.db,record,b.time)
    assert not b.calls and not b.cancels
    asyncio.run(e.observe(b,raw(b.time),eligible_symbols(),full=True))
    assert db.get('health')['reconciled'] is True
    assert not db.session(e.session().day)['opening_healthy']
    with pytest.raises(Unsafe):e.health_check(b.time)
    assert (e.runtime/'KILL').exists() and not b.calls
