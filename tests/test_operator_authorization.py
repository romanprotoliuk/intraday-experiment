"""Deferred operator recovery: synthetic clocks, temp ledgers, read-only fake MCP."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from trader import doctor, host_health, configuration as C
from trader.models import UTC, Unsafe, iso
from trader.store import atomic_text
from trader.persistent import operator_authorization as A
from test_system_recovery import recovery, healthy_files
from test_persistent import env, raw, tick


@pytest.fixture
def deferred(recovery,monkeypatch):
    e,b,db,old,obs=recovery
    atomic_text(e.runtime/'KILL',A.TARGET+'\n')
    e.runtime.chmod(0o700)
    with db.tx():
        day=db.session(e.session().day)
        day.update(last_failure=iso(b.time-timedelta(minutes=6)),stable_since=iso(b.time-timedelta(minutes=6)))
        db.save_session(e.session().day,day)
        db.event(b.time-timedelta(minutes=6),'PERSISTENT_WORKER_START',dict(pid=1))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return b.time.astimezone(tz) if tz else b.time.replace(tzinfo=None)
    monkeypatch.setattr(doctor,'datetime',Clock)
    monkeypatch.setattr(host_health,'datetime',Clock)
    monkeypatch.setattr(doctor.time,'time',lambda:b.time.timestamp())
    monkeypatch.setattr(doctor.os,'kill',lambda pid,sig:None if sig==0 and pid in (1,9) else (_ for _ in ()).throw(ProcessLookupError()))
    b.live_guard=None;b.cancel_guard=None;b.connection_info={'initialized':True};connections=[]
    @asynccontextmanager
    async def connect(path,*args,**kwargs):
        assert path==e.runtime/'oauth.json'
        assert not kwargs.get('interactive')
        connections.append(1)
        yield b
    monkeypatch.setattr(doctor,'connect',connect)
    yield e,b,db,connections


def authorize(e,b):return A.create(e.root,e.session().day,acknowledge=True,now=b.time)
def run(e):return asyncio.run(A.evaluate_pending(e))


def test_no_authorization_no_broker_calls_or_clear(deferred):
    e,b,db,calls=deferred
    assert not run(e) and not calls and (e.runtime/'KILL').exists()


def test_create_is_inert_and_never_sets_live(deferred):
    e,b,db,calls=deferred;before=db.settings();target=(e.runtime/'KILL').read_bytes()
    record=authorize(e,b)
    assert record['status']=='PENDING' and db.settings()==before
    assert (e.runtime/'KILL').read_bytes()==target and not calls and not b.calls
    assert e.account not in json.dumps(record)
    assert record['target']==A.hold_identity(e.runtime)


def test_all_existing_operator_checks_pass_then_consume_once(deferred):
    e,b,db,calls=deferred;before=db.settings();authorize(e,b)
    readiness=asyncio.run(doctor.doctor(e.root,True))
    assert readiness['ready_for_transition'],(readiness['blockers'],readiness.get('market_data'))
    calls.clear()
    assert run(e)
    assert len(calls)==3 and not (e.runtime/'KILL').exists() and not (e.runtime/'TRANSITION').exists()
    assert db.get(A.KEY)['status']=='CONSUMED' and db.settings()==before
    assert not b.calls and not b.cancels
    assert not run(e) and len(calls)==3


@pytest.mark.parametrize('fault',['opening','clock','stale_host','connectivity','missing_bars','stale_quote','daily_loss','attempts'])
def test_failed_prerequisites_never_clear_hold(deferred,fault):
    e,b,db,calls=deferred;authorize(e,b)
    if fault in ('opening','connectivity','daily_loss','attempts'):
        day=db.session(e.session().day)
        day.update({'opening':{'opening_healthy':False},'connectivity':{'recovery_active':True},
                    'daily_loss':{'loss_latched':True},'attempts':{'attempts':5}}[fault])
        db.save_session(e.session().day,day)
    elif fault in ('clock','stale_host'):
        path=e.runtime/'host-health.json';h=json.loads(path.read_text())
        if fault=='clock':h['clock']['offset_seconds']=1
        else:h['at']=iso(b.time-timedelta(seconds=20))
        path.write_text(json.dumps(h))
    elif fault=='missing_bars':
        async def missing(*args):return {'SPY':[],'QQQ':[]}
        b.bars=missing
    else:
        original=b.quotes
        async def stale(symbols):
            q=await original(symbols)
            for v in q.values():v['venue_bid_time']=iso(b.time-timedelta(seconds=20))
            return q
        b.quotes=stale
    assert not run(e) and (e.runtime/'KILL').exists() and not b.calls
    assert db.get(A.KEY)['status']=='PENDING'


@pytest.mark.parametrize('fault',['position','order','account'])
def test_fresh_broker_disagreement_invalidates_receipt(deferred,fault):
    e,b,db,calls=deferred;authorize(e,b)
    original=b.snapshot
    async def altered(*args):
        s=await original(*args)
        if fault=='position':s['positions']=[dict(symbol='QQQ',quantity='1',shares_available_for_sells='1')]
        elif fault=='order':s['orders']=[dict(state='confirmed')]
        else:s['account']['account_number']='synthetic-wrong-account'
        return s
    b.snapshot=altered
    assert not run(e) and (e.runtime/'KILL').exists()
    assert db.get(A.KEY)['status']=='INVALIDATED' and not b.calls


@pytest.mark.parametrize('fault',['ambiguous','ownership','authentication','repeated','new_failure'])
def test_hard_failure_since_authorization_is_sticky(deferred,fault):
    e,b,db,calls=deferred;authorize(e,b)
    if fault=='new_failure':db.event(b.time,'SYSTEM_HOLD_PRESERVED',dict(reason_code='WORKER_EXIT'))
    else:
        cls={'ambiguous':'AMBIGUOUS_MUTATING_ORDER_STATE','ownership':'OWNERSHIP_POSITION_MISMATCH',
             'authentication':'AUTHENTICATION_FAILURE','repeated':'REPEATED_BROKER_COMMUNICATION_FAILURE'}[fault]
        db.event(b.time,'HEALTH_FAILURE',dict(diagnostic={'failure_class':cls},outage=3 if fault=='repeated' else 1))
    assert not run(e) and not calls and db.get(A.KEY)['status']=='INVALIDATED'
    assert (e.runtime/'KILL').exists()


@pytest.mark.parametrize('replacement',['same_text','operator','structured','in_place'])
def test_hold_replacement_invalidates_even_with_same_text(deferred,replacement):
    e,b,db,calls=deferred;authorize(e,b)
    if replacement=='in_place':(e.runtime/'KILL').write_text(A.TARGET+'\n')
    else:atomic_text(e.runtime/'KILL',{'same_text':A.TARGET+'\n','operator':'Operator emergency hold','structured':'{"version":"system-recovery-1"}'}[replacement])
    after=(e.runtime/'KILL').read_bytes()
    assert not run(e) and db.get(A.KEY)['status']=='INVALIDATED'
    assert (e.runtime/'KILL').read_bytes()==after


@pytest.mark.parametrize('fault',['session','expired','cutoff','eod','next_session','strategy','configuration','account','malformed','spent','budget'])
def test_binding_expiration_and_single_use(deferred,fault):
    e,b,db,calls=deferred;record=authorize(e,b)
    if fault=='session':record['session']='2025-02-11';db.put(A.KEY,record)
    elif fault=='expired':record['expires_at']=iso(b.time-timedelta(seconds=1));db.put(A.KEY,record)
    elif fault in ('cutoff','eod','next_session'):
        b.time=e.session().close-timedelta(minutes=60) if fault=='cutoff' else e.session().close if fault=='eod' else b.time+timedelta(days=1)
        healthy_files(e,b)
    elif fault in ('strategy','configuration'):
        cfg=deepcopy(e.config)
        if fault=='strategy':cfg['strategy']['target_r']='3'
        else:cfg['trading']['capital_per_trade']='9'
        C.write(e.root,cfg)
    elif fault=='account':db.put('account_number','synthetic-new-account')
    elif fault=='malformed':db.put(A.KEY,['malformed'])
    elif fault=='spent':record['status']='CONSUMED';db.put(A.KEY,record)
    else:db.put('system_recovery_budget',dict(day=e.session().day,failures=e.config['watchdog']['restart_limit']))
    assert not run(e) and (e.runtime/'KILL').exists() and not calls and not b.calls


def test_valid_authorization_survives_controlled_worker_restart(deferred):
    e,b,db,calls=deferred;record=authorize(e,b)
    db.event(b.time,'WORKER_STOP',dict(controlled=True,open_positions=0,unresolved_orders=0))
    identity=db.get('worker_identity');identity['generation']='another-synthetic-generation';db.put('worker_identity',identity)
    # Receipt is scoped to a hold instance, not a PID that must never restart.
    assert A.pending(e.root,db,b.time)==record


@pytest.mark.parametrize('fault',['trigger','invalidation'])
def test_completed_missed_setup_not_rearmed_by_authorization(deferred,fault):
    e,b,db,calls=deferred;authorize(e,b);b.time+=timedelta(minutes=5);healthy_files(e,b)
    original=raw(b.time)
    for symbol,bars in original.items():
        bars.append(dict(bars[-1],begins_at=iso(e.session().open+timedelta(minutes=15)),
            open_price='100',high_price='101' if fault=='trigger' else '100',
            low_price='98' if fault=='invalidation' else '99',close_price='100',volume='1000000'))
    async def bars(*args):return original
    b.bars=bars
    assert run(e)
    asyncio.run(e.tick(b,original,{}))
    assert all(c['state']=='MISSED' for c in db.get('observation')['candidates'].values())
    assert not b.calls


def test_recovery_before_trigger_leaves_normal_strategy_and_risk_path(deferred):
    e,b,db,calls=deferred;authorize(e,b);assert run(e)
    b.live_guard=None
    tick(e,b)
    assert len(b.calls)==2
    tick(e,b);assert len(b.calls)==2


def test_race_replacement_during_broker_check_never_clears_new_hold(deferred,monkeypatch):
    e,b,db,calls=deferred;authorize(e,b);original=doctor.doctor;count=0
    async def raced(*args):
        nonlocal count
        result=await original(*args);count+=1
        if count==2:atomic_text(e.runtime/'KILL','Operator replacement')
        return result
    monkeypatch.setattr(doctor,'doctor',raced)
    assert not run(e) and (e.runtime/'KILL').read_text()=='Operator replacement'
    assert db.get(A.KEY)['status']=='INVALIDATED'
    assert (e.runtime/'TRANSITION').exists()


def test_storage_failure_leaves_kill_and_durable_barrier(deferred,monkeypatch):
    e,b,db,calls=deferred;authorize(e,b)
    def failed(*args):raise OSError('synthetic commit failure')
    monkeypatch.setattr(A,'consume',failed)
    with pytest.raises(OSError):run(e)
    assert (e.runtime/'KILL').exists() and (e.runtime/'TRANSITION').exists()
    assert not b.calls


def test_no_acknowledgement_no_receipt(deferred):
    e,b,db,calls=deferred
    with pytest.raises(Unsafe):A.create(e.root,e.session().day,now=b.time)
    assert db.get(A.KEY) is None


def test_cannot_reissue_in_same_session(deferred):
    e,b,db,calls=deferred;authorize(e,b)
    with pytest.raises(Unsafe):authorize(e,b)
    A.invalidate(db,b.time,'OPERATOR_REQUEST')
    with pytest.raises(Unsafe):authorize(e,b)


def test_premarket_receipt_never_initiates_transition(deferred):
    e,b,db,calls=deferred;b.time=e.session().open-timedelta(minutes=20);healthy_files(e,b)
    authorize(e,b)
    assert not run(e) and not calls and db.get(A.KEY)['status']=='PENDING'
    assert (e.runtime/'KILL').exists()


@pytest.mark.parametrize('marker',['STOP','TRANSITION','LIQUIDATE','MAINTENANCE','READ_ONLY','ENTRIES_DISABLED'])
def test_other_operator_control_invalidates_without_removing_it(deferred,marker):
    e,b,db,calls=deferred;authorize(e,b);atomic_text(e.runtime/marker,'synthetic operator hold')
    assert not run(e) and not calls and db.get(A.KEY)['status']=='INVALIDATED'
    assert (e.runtime/marker).exists() and (e.runtime/'KILL').exists()


def test_local_ambiguous_intent_blocks_without_broker_retry(deferred):
    e,b,db,calls=deferred;authorize(e,b)
    db.db.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?)',('synthetic-id','synthetic-setup','synthetic-trade','buy','AMBIGUOUS',None,'{}'))
    assert not run(e) and not calls and not b.calls
    assert db.get(A.KEY)['status']=='INVALIDATED' and (e.runtime/'KILL').exists()


def test_worker_generation_mismatch_cannot_consume(deferred):
    e,b,db,calls=deferred;authorize(e,b)
    p=e.runtime/'supervisor-health.json';s=json.loads(p.read_text());s['worker_generation']='unrelated-generation';p.write_text(json.dumps(s))
    assert not run(e) and db.get(A.KEY)['status']=='PENDING' and (e.runtime/'KILL').exists()


def test_boolean_ready_cannot_replace_missing_evidence(deferred,monkeypatch):
    e,b,db,calls=deferred;authorize(e,b)
    async def incomplete(*args):return {'ready_for_transition':True}
    monkeypatch.setattr(doctor,'doctor',incomplete)
    assert not run(e) and (e.runtime/'KILL').exists() and db.get(A.KEY)['status']=='PENDING'


def test_daily_loss_latch_between_certification_and_commit_blocks(deferred,monkeypatch):
    e,b,db,calls=deferred;authorize(e,b);original=doctor.doctor;count=0
    async def raced(*args):
        nonlocal count
        result=await original(*args);count+=1
        if count==3:
            day=db.session(e.session().day);day['loss_latched']=True;db.save_session(e.session().day,day)
        return result
    monkeypatch.setattr(doctor,'doctor',raced)
    assert not run(e) and (e.runtime/'KILL').exists() and not b.calls


def test_same_text_replacement_after_proof_commit_is_not_cleared(deferred,monkeypatch):
    e,b,db,calls=deferred;record=authorize(e,b);original=A.consume
    def raced(*args):
        original(*args)
        atomic_text(e.runtime/'KILL',A.TARGET+'\n')
    monkeypatch.setattr(A,'consume',raced)
    assert not run(e) and (e.runtime/'KILL').exists()
    assert A.hold_identity(e.runtime)!=record['target']
    assert db.get(A.KEY)['status']=='CONSUMED' and (e.runtime/'TRANSITION').exists()
    assert not run(e)


def test_failure_after_unlink_keeps_barrier_and_spent_receipt(deferred,monkeypatch):
    from pathlib import Path
    e,b,db,calls=deferred;authorize(e,b);original=Path.unlink
    def failed(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if path==e.runtime/'KILL':raise OSError('synthetic interruption after unlink')
        return result
    monkeypatch.setattr(Path,'unlink',failed)
    with pytest.raises(OSError):run(e)
    assert (e.runtime/'KILL').exists() and (e.runtime/'TRANSITION').exists()
    assert db.get(A.KEY)['status']=='CONSUMED' and not b.calls


def test_authorization_creation_cannot_initialize_missing_database(tmp_path):
    with pytest.raises(Unsafe,match='Existing production database'):
        A.create(tmp_path,'2025-02-11',acknowledge=True)
    assert not (tmp_path/'runtime/persistent.sqlite3').exists()


def test_database_corruption_cannot_be_repaired_by_authorizing(tmp_path):
    import sqlite3
    r=tmp_path/'runtime';r.mkdir();p=r/'persistent.sqlite3'
    original=b'corrupt synthetic database';p.write_bytes(original)
    atomic_text(r/'KILL',A.TARGET+'\n')
    with pytest.raises(sqlite3.DatabaseError):A.create(tmp_path,'2025-02-11',acknowledge=True)
    assert p.read_bytes()==original and (r/'KILL').exists()


def test_quote_age_uses_validation_time_not_later_report_completion(deferred,monkeypatch):
    e,b,db,calls=deferred;authorize(e,b);original=doctor.doctor;count=0
    async def delayed_report(*args):
        nonlocal count
        result=await original(*args);count+=1
        if count==3:
            result['market_data_checked_at']=iso(b.time-timedelta(seconds=6))
            result['ended_at']=iso(b.time)
        return result
    monkeypatch.setattr(doctor,'doctor',delayed_report)
    assert not run(e) and (e.runtime/'KILL').exists() and not b.calls


def test_deferred_transition_cannot_create_missing_database(tmp_path):
    with pytest.raises(Unsafe,match='Existing production database'):
        asyncio.run(doctor.transition(tmp_path,True,True,True,authorization_id='synthetic'))
    assert not (tmp_path/'runtime/persistent.sqlite3').exists()
