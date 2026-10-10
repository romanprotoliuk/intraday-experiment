"""Synthetic authority-boundary regressions; never uses the production ledger."""
import asyncio
from datetime import timedelta
import json
import pytest
from trader import legacy_recovery as L, doctor as D, review_successor as S
from trader.models import Unsafe,iso
from test_next_session_readiness import maintenance,reviewed,acknowledge
from test_review_successor import incident,create,fresh,recon
from test_system_recovery import recovery
from test_persistent import env


def failures(db,at,count):
    for n in range(count):
        db.event(at,'SYSTEM_HOLD_PRESERVED',dict(reason_code='WATCHDOG_STALL',worker_generation='synthetic-failure-'+str(n)))


def test_first_review_cannot_ignore_preserved_exhaustion(maintenance):
    e,b,db,_,_=maintenance
    failures(db,b.time-timedelta(seconds=1),3)
    before=list(db.db.execute('select * from events'));hold=(e.runtime/'KILL').read_bytes()
    with pytest.raises(Unsafe):reviewed(maintenance)
    assert db.get(L.KEY) is None and list(db.db.execute('select * from events'))==before
    assert (e.runtime/'KILL').read_bytes()==hold


def test_old_activation_cannot_recover_legacy_by_reason_text(maintenance,monkeypatch):
    e,b,db,_,_=maintenance
    class Clock:
        @staticmethod
        def now(tz):return b.time.astimezone(tz)
    monkeypatch.setattr(D,'datetime',Clock)
    db.event(b.time-timedelta(minutes=10),'PERSISTENT_WORKER_START',dict(pid=1))
    with pytest.raises(Unsafe):D.kill_recovery(e.root,{'heartbeat':{'pid':1}})
    assert (e.runtime/'KILL').exists() and not b.calls


@pytest.mark.parametrize('stored',range(5))
@pytest.mark.parametrize('preserved',range(5))
def test_both_durable_budget_sources_enforce_unchanged_limit(maintenance,stored,preserved):
    from trader.persistent.system_recovery import require_recovery_budget
    e,b,db,_,_=maintenance
    db.put('system_recovery_budget',{'failures':stored})
    failures(db,b.time,preserved)
    before=db.db.total_changes
    if max(stored,preserved)>=3:
        with pytest.raises(Unsafe,match='budget'):require_recovery_budget(db.db,e.config)
    else:
        proof=require_recovery_budget(db.db,e.config)
        assert proof==dict(stored_failures=stored,preserved_failures=preserved,limit=3)
    assert db.db.total_changes==before


@pytest.mark.parametrize('counter',[None,[],{'failures':-1},{'failures':True},{'failures':'0'},{'failures':1.5}])
def test_malformed_budget_is_not_zero(maintenance,counter):
    from trader.persistent.system_recovery import require_recovery_budget
    e,b,db,_,_=maintenance;db.put('system_recovery_budget',counter)
    with pytest.raises(Unsafe):require_recovery_budget(db.db,e.config)


def test_unpaired_preserved_failure_fails_closed(maintenance):
    from trader.persistent.system_recovery import require_recovery_budget
    e,b,db,_,_=maintenance;db.event(b.time,'SYSTEM_HOLD_PRESERVED',{'reason_code':'WATCHDOG_STALL'})
    with pytest.raises(Unsafe,match='unresolved'):require_recovery_budget(db.db,e.config)


def test_successor_refuses_preserved_budget_before_broker_calls(incident):
    f,parent=incident;e,b,db,_,calls=f
    failures(db,b.time,3);before=list(db.db.execute('select * from events'));hold=(e.runtime/'KILL').read_bytes()
    with pytest.raises(Unsafe,match='budget'):create(incident)
    assert not calls and db.get(L.KEY)==parent and db.get(S.HEAD) is None
    assert list(db.db.execute('select * from events'))==before and (e.runtime/'KILL').read_bytes()==hold


def test_ack_proof_independently_rechecks_budget(maintenance):
    from trader import readiness as R
    e,b,db,_,_=maintenance
    proof=asyncio.run(R.control_snapshot(e.root,now=lambda:b.time))
    failures(db,b.time,3)
    with pytest.raises(Unsafe,match='budget'):L.require_control_proof(e.root,db,proof,lambda:b.time)
    assert (e.runtime/'KILL').exists()


def test_budget_cannot_be_replenished_by_restart_or_midnight(maintenance):
    from trader.persistent.system_recovery import require_recovery_budget
    from trader.persistent.db import Ledger
    e,b,db,_,_=maintenance;failures(db,b.time,3)
    b.time+=timedelta(days=1)
    reopened=Ledger.existing(e.runtime/'persistent.sqlite3')
    try:
        with pytest.raises(Unsafe):require_recovery_budget(reopened.db,e.config)
    finally:reopened.close()


@pytest.mark.parametrize('review_present',[False,True])
def test_activation_requires_dedicated_legacy_workflow_even_with_review(maintenance,review_present):
    e,b,db,_,_=maintenance
    if review_present:reviewed(maintenance)
    with pytest.raises(Unsafe,match='dedicated'):D.kill_recovery(e.root,{'heartbeat':{'pid':1}})
    assert (e.runtime/'KILL').exists()


@pytest.mark.parametrize('fault',['operator','unknown','budget','position','intent','ledger'])
def test_activation_cannot_release_hard_or_mismatched_structured_hold(recovery,fault):
    e,b,db,record,_=recovery
    if fault=='operator':record['operator_required']=True
    elif fault=='unknown':record['failure_class']='UNCAUGHT_SOFTWARE_EXCEPTION'
    elif fault=='budget':record['failures']=record['failure_limit']
    elif fault=='position':record['positions_at_failure']=1
    elif fault=='intent':record['mutation_in_flight']=True
    elif fault=='ledger':record['episode']='different'
    (e.runtime/'KILL').write_text(json.dumps(record))
    with pytest.raises(Unsafe):D.kill_recovery(e.root,{'heartbeat':{'pid':1}})
    assert (e.runtime/'KILL').exists()


def test_narrow_structured_operator_check_still_requires_normal_proof(recovery,monkeypatch):
    e,b,db,record,_=recovery
    class Clock:
        @staticmethod
        def now(tz):return b.time.astimezone(tz)
    monkeypatch.setattr(D,'datetime',Clock)
    db.event(b.time-timedelta(minutes=10),'PERSISTENT_WORKER_START',dict(pid=1))
    day=db.session(e.session().day);day['stable_since']=iso(b.time-timedelta(minutes=10));day.pop('last_failure',None);db.save_session(e.session().day,day)
    D.kill_recovery(e.root,{'heartbeat':{'pid':1}})
    assert (e.runtime/'KILL').exists() and not b.calls
    # Passing this check is not a transition or entry permission.


@pytest.mark.parametrize('source',['stored','preserved'])
def test_readiness_agrees_on_exhaustion(maintenance,source):
    from trader import readiness as R
    e,b,db,_,_=maintenance
    if source=='stored':db.put('system_recovery_budget',{'failures':3})
    else:failures(db,b.time,3)
    proof=asyncio.run(R.control_snapshot(e.root,now=lambda:b.time))
    assert proof['checks']['restart_budget'] is False
    assert (e.runtime/'KILL').exists()


def test_budget_appearing_between_activation_proofs_is_not_cleared(recovery,monkeypatch):
    e,b,db,record,_=recovery
    class Clock:
        @staticmethod
        def now(tz):return b.time.astimezone(tz)
    monkeypatch.setattr(D,'datetime',Clock)
    monkeypatch.setattr(D,'require_host',lambda r:None)
    db.event(b.time-timedelta(minutes=10),'PERSISTENT_WORKER_START',dict(pid=1))
    day=db.session(e.session().day);day['stable_since']=iso(b.time-timedelta(minutes=10));day.pop('last_failure',None);db.save_session(e.session().day,day)
    calls=[]
    async def proof(*args):
        calls.append(1)
        if len(calls)==2:db.put('system_recovery_budget',{'failures':3})
        return {'ready_for_transition':True,'blockers':[],'heartbeat':{'pid':1},'config_fingerprint':db.get('config_fingerprint')}
    monkeypatch.setattr(D,'doctor',proof)
    with pytest.raises(Unsafe,match='budget'):
        asyncio.run(D.transition(e.root,True,True,True))
    assert len(calls)==2 and (e.runtime/'KILL').exists()
    assert not db.settings().live_enabled and (e.runtime/'READ_ONLY').exists() and (e.runtime/'MAINTENANCE').exists()
    assert db.get('system_recovery_budget')['failures']==3 and not b.calls and not b.cancels
