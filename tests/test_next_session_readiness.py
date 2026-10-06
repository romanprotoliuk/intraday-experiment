"""Synthetic legacy incident + new session; no actual broker/process mutation."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
import json
import sqlite3
from pathlib import Path

import pytest

from trader import readiness as R, legacy_recovery as L, configuration as C
from trader.models import Unsafe, iso, dt, D
from trader.store import atomic_text
from trader.orb import evaluate
from trader.session_forensics import reconstruct
from test_system_recovery import recovery, healthy_files
from test_persistent import env, tick, raw, eligible_symbols


@pytest.fixture
def maintenance(recovery,monkeypatch):
    e,b,db,hold,obs=recovery
    e.runtime.chmod(0o700)
    b.time=e.session().open-timedelta(hours=2)
    atomic_text(e.runtime/'KILL',L.TARGET+'\n')
    db.put('system_hold',None);db.put('system_recovery_budget',{})
    w=db.get('worker_identity');w['started_at']=iso(b.time-timedelta(minutes=10));db.put('worker_identity',w)
    day=db.session(e.session().day);day.update(opening_healthy=False,recovery_active=True,stable_since=w['started_at']);day.pop('last_failure',None)
    db.save_session(e.session().day,day);healthy_files(e,b)
    db.put('health',dict(at=iso(b.time),auth='AUTHENTICATED_READ_VERIFIED',reconciled=True,data_healthy=False))
    db.event(b.time-timedelta(days=1),'WORKER_STOP',dict(controlled=False,open_positions=0,unresolved_orders=0))
    event_id=db.db.execute('select max(id) from events').fetchone()[0]
    b.live_guard=None;b.cancel_guard=None;calls=[]
    @asynccontextmanager
    async def connection(path,*args,**kwargs):
        assert path==e.runtime/'oauth.json';calls.append(1);yield b
    monkeypatch.setattr(R,'connect',connection)
    monkeypatch.setattr(R.platform,'system',lambda:'Darwin')
    monkeypatch.setattr(R,'launchagent_running',lambda pid:pid==9)
    monkeypatch.setattr(R.os,'kill',lambda pid,sig:None if sig==0 and pid in (1,9) else (_ for _ in ()).throw(ProcessLookupError()))
    yield e,b,db,event_id,calls


def reviewed(fixture):
    e,b,db,eid,calls=fixture
    return L.review(e.root,[eid],L.hold_identity(e.runtime),investigated=True,now=b.time)


def acknowledge(fixture,record):
    e,b,db,_,_=fixture
    return asyncio.run(L.acknowledge(e.root,record['id'],acknowledge=True,now=lambda:b.time))


def test_investigation_alone_is_inert_and_bound(maintenance):
    e,b,db,eid,calls=maintenance
    assert L.classify_hold(e.root,db.db,b.time)=='A'
    before=db.settings();record=reviewed(maintenance)
    assert L.classify_hold(e.root,db.db,b.time)=='C'
    assert (e.runtime/'KILL').exists() and db.settings()==before and not calls and not b.calls
    assert e.account not in json.dumps(record)


def test_outside_session_acknowledgment_does_not_make_entries_eligible(maintenance):
    e,b,db,_,calls=maintenance;record=reviewed(maintenance)
    sessions=list(db.db.execute('select day,data from sessions'));settings=db.get('settings')
    assert acknowledge(maintenance,record)['entry_eligible'] is False
    assert len(calls)==2 and not (e.runtime/'KILL').exists() and not (e.runtime/'TRANSITION').exists()
    assert db.get('settings')==settings and [tuple(x) for x in db.db.execute('select day,data from sessions')]==[tuple(x) for x in sessions]
    assert not b.calls and not b.cancels
    with pytest.raises(Unsafe,match='Market closed'):e.health_check(b.time)
    # The window gate still blocks an opening-period tick after acknowledgment.
    b.time=e.session().open+timedelta(minutes=5);healthy_files(e,b)
    obs=asyncio.run(e.observe(b,raw(b.time),eligible_symbols(),full=True))
    with pytest.raises(Unsafe,match='entry window'):e.health_check(b.time)


@pytest.mark.parametrize('fault',['account','positions','orders','auth','clock','host','heartbeat','watchdog','generation','database','config','strategy','unresolved','budget','new_failure','evidence_changed','quiet_period','worker_stable','config_permissions','runtime_permissions'])
def test_any_uncertain_proof_blocks_legacy_acknowledgment(maintenance,fault):
    e,b,db,eid,calls=maintenance;record=reviewed(maintenance)
    if fault in ('account','positions','orders','auth'):
        original=b.snapshot
        async def changed(*args):
            if fault=='auth':raise Unsafe('Synthetic authentication failure')
            s=await original(*args)
            if fault=='account':s['account']['account_number']='synthetic-wrong'
            elif fault=='positions':s['positions']=[{'symbol':'QQQ','quantity':'1'}]
            else:s['orders']=[{'state':'confirmed'}]
            return s
        b.snapshot=changed
    elif fault in ('clock','host'):
        p=e.runtime/'host-health.json';v=json.loads(p.read_text())
        if fault=='clock':v['clock']['offset_seconds']=1
        else:v['ac_power']=False
        p.write_text(json.dumps(v))
    elif fault in ('heartbeat','watchdog'):(e.runtime/('heartbeat.json' if fault=='heartbeat' else 'supervisor-health.json')).unlink()
    elif fault in ('generation','worker_stable'):
        v=db.get('worker_identity');v.update({'generation':'wrong'} if fault=='generation' else {'started_at':iso(b.time)});db.put('worker_identity',v)
    elif fault=='database':db.db.execute('drop table trades')
    elif fault=='config_permissions':C.path(e.root).chmod(0o644)
    elif fault=='runtime_permissions':e.runtime.chmod(0o755)
    elif fault in ('config','strategy'):
        c=deepcopy(e.config)
        if fault=='config':c['trading']['capital_per_trade']='9'
        else:c['strategy']['target_r']='3'
        C.write(e.root,c)
    elif fault=='unresolved':
        db.db.execute("insert into orders values('synthetic','synthetic','synthetic','buy','AMBIGUOUS',NULL,'{}')")
    elif fault=='budget':db.put('system_recovery_budget',{'failures':3})
    elif fault=='new_failure':db.event(b.time,'WORKER_FATAL',{})
    elif fault=='evidence_changed':db.db.execute("update events set data='{}' where id=?",(eid,))
    else:
        day=db.session(e.session().day);day['last_failure']=iso(b.time);db.save_session(e.session().day,day)
    with pytest.raises((Unsafe,sqlite3.DatabaseError)):acknowledge(maintenance,record)
    assert (e.runtime/'KILL').exists() and not b.calls and not b.cancels


@pytest.mark.parametrize('text',['Operator KILL','unknown','{}','{"version":"system-recovery-1"}',L.TARGET+' changed'])
def test_unknown_operator_malformed_or_structured_hold_not_class_c(maintenance,text):
    e,b,db,_,_=maintenance;record=reviewed(maintenance);atomic_text(e.runtime/'KILL',text)
    assert L.classify_hold(e.root,db.db,b.time)=='A'
    with pytest.raises(Unsafe):acknowledge(maintenance,record)
    assert (e.runtime/'KILL').read_text()==text


def test_same_text_replacement_cannot_consume_old_review(maintenance):
    e,b,db,_,_=maintenance;record=reviewed(maintenance);atomic_text(e.runtime/'KILL',L.TARGET+'\n')
    with pytest.raises(Unsafe):acknowledge(maintenance,record)
    assert db.get(L.KEY)['status']=='REVIEWED'


def test_market_hours_and_next_session_expire_review(maintenance):
    e,b,db,_,_=maintenance;record=reviewed(maintenance)
    b.time=e.session().open;healthy_files(e,b)
    with pytest.raises(Unsafe,match='outside regular'):acknowledge(maintenance,record)
    b.time+=timedelta(days=1);healthy_files(e,b)
    assert not L.valid_review(e.root,db.db,record,b.time)


def test_replacement_during_second_broker_read_preserved(maintenance):
    e,b,db,_,_=maintenance;record=reviewed(maintenance);original=b.snapshot;n=[0]
    async def changed(*args):
        n[0]+=1
        if n[0]==2:atomic_text(e.runtime/'KILL','Operator new hold')
        return await original(*args)
    b.snapshot=changed
    with pytest.raises(Unsafe):acknowledge(maintenance,record)
    assert (e.runtime/'KILL').read_text()=='Operator new hold' and (e.runtime/'TRANSITION').exists()


def test_failure_after_consumption_retains_barrier_and_cannot_reuse(maintenance,monkeypatch):
    e,b,db,_,_=maintenance;record=reviewed(maintenance);original=Path.unlink
    def fail(path,*args,**kwargs):
        if path==e.runtime/'KILL':raise OSError('Synthetic unlink failure')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',fail)
    with pytest.raises(OSError):acknowledge(maintenance,record)
    assert db.get(L.KEY)['status']=='ACKNOWLEDGED' and (e.runtime/'TRANSITION').exists() and (e.runtime/'KILL').exists()
    with pytest.raises(Unsafe):acknowledge(maintenance,record)


@pytest.mark.parametrize('marker',['STOP','LIQUIDATE','MAINTENANCE','TRANSITION','READ_ONLY','ENTRIES_DISABLED'])
def test_other_operator_controls_never_removed(maintenance,marker):
    e,b,db,_,_=maintenance;record=reviewed(maintenance);(e.runtime/marker).write_text('synthetic hold')
    with pytest.raises(Unsafe):acknowledge(maintenance,record)
    assert (e.runtime/marker).exists() and (e.runtime/'KILL').exists()


def test_review_and_ack_require_explicit_attestation(maintenance):
    e,b,db,eid,_=maintenance
    with pytest.raises(Unsafe):L.review(e.root,[eid],L.hold_identity(e.runtime),now=b.time)
    record=reviewed(maintenance)
    with pytest.raises(Unsafe):asyncio.run(L.acknowledge(e.root,record['id'],now=lambda:b.time))


def test_readiness_separates_healthy_monitoring_from_hard_hold(maintenance):
    e,b,db,_,_=maintenance
    proof=asyncio.run(R.control_snapshot(e.root,now=lambda:b.time))
    assert proof['passed'],proof
    result=R.summarize(proof,controls=['KILL'],auth={'covers_session':True,'expiry_known':True,'refresh_present':True,'client_present':True,'refresh_proven':True},reset_valid=True,next_session_info={},recovery_class='A')
    assert result['INFRASTRUCTURE_READY'] and result['MONITORING_READY'] and not result['UNATTENDED_LIVE_READY']


@pytest.mark.parametrize('gate',['database','configuration','strategy','risk','ownership','heartbeat','watchdog','processes','launchagent','host_clock','ac_power','broker','local_flat'])
def test_readiness_cannot_ignore_failed_proof(gate):
    checks={k:True for k in ('database','configuration','strategy','risk','ownership','heartbeat','watchdog','processes','launchagent','host_clock','ac_power','broker','local_flat')};checks[gate]=False
    result=R.summarize({'checks':checks,'live_enabled':True},controls=[],auth={'covers_session':True,'expiry_known':True,'refresh_present':True,'client_present':True,'refresh_proven':True},reset_valid=True,next_session_info={},recovery_class='NORMAL')
    assert not result['UNATTENDED_LIVE_READY']


def test_next_session_uses_holiday_and_early_close_calendar():
    config=C.validate({});s=R.next_session(config,dt('2025-11-27T12:00:00Z'))
    assert s.day=='2025-11-28' and s.close.hour==18
    assert R.next_session(config,s.close).day=='2025-12-01'


def test_next_session_reset_preserves_spent_history(env):
    e,b,db=env;before=db.session(e.session().day)
    before.update(attempts=5,loss_latched=True,entry_cutoff_latched=True,closed=True,gross_losses='1.5')
    db.save_session(before['day'],before)
    next_day=R.next_session(C.validate({}),e.session().close).day
    after=db.ensure_session(next_day)
    assert after['attempts']==0 and after['gross_losses']=='0' and not after['opening_healthy'] and after['recovery_active']
    assert db.session(before['day'])==before


@pytest.mark.parametrize('fault',['opening','data','clock','host','auth','reconciliation','connectivity','cutoff','closed','stale_observation'])
def test_post_acknowledgment_still_requires_ordinary_entry_health(maintenance,fault):
    e,b,db,_,_=maintenance;record=reviewed(maintenance);acknowledge(maintenance,record)
    b.time=e.session().open+timedelta(minutes=15,seconds=2);healthy_files(e,b)
    asyncio.run(e.observe(b,raw(b.time),eligible_symbols(),full=True))
    day=db.session(e.session().day);day.update(opening_healthy=True,recovery_active=False,connectivity_halt=False)
    db.save_session(e.session().day,day)
    e.health_check(b.time)
    if fault in ('opening','connectivity','cutoff','closed'):
        day.update({'opening':{'opening_healthy':False},'connectivity':{'connectivity_halt':True},'cutoff':{'entry_cutoff_latched':True},'closed':{'closed':True}}[fault]);db.save_session(e.session().day,day)
    elif fault in ('data','auth','reconciliation'):
        health=db.get('health');health.update({'data':{'data_healthy':False},'auth':{'auth':'UNVERIFIED'},'reconciliation':{'reconciled':False}}[fault]);db.put('health',health)
    elif fault=='stale_observation':
        obs=db.get('observation');obs['at']=iso(b.time-timedelta(seconds=60));db.put('observation',obs)
    else:
        p=e.runtime/'host-health.json';h=json.loads(p.read_text())
        if fault=='clock':h['clock']['offset_seconds']=1
        else:h['ac_power']=False
        p.write_text(json.dumps(h))
    with pytest.raises(Unsafe):e.health_check(b.time)
    assert not b.calls and not b.cancels


def test_worker_reconstruction_after_acknowledgment_is_not_opening_certification(maintenance):
    from trader.persistent.engine import Coordinator
    e,b,db,_,_=maintenance;record=reviewed(maintenance);acknowledge(maintenance,record)
    rebuilt=Coordinator(e.root,db,lambda:b.time)
    with pytest.raises(Unsafe,match='Market closed'):rebuilt.health_check(b.time)
    assert not db.session(e.session().day)['opening_healthy']


def test_missing_schema_is_not_recreated_by_control_transition(maintenance):
    e,b,db,_,_=maintenance;record=reviewed(maintenance);db.db.execute('drop table fills')
    with pytest.raises(sqlite3.DatabaseError):acknowledge(maintenance,record)
    assert not db.db.execute("select 1 from sqlite_master where name='fills'").fetchone()
    assert (e.runtime/'KILL').exists()


def test_sufficient_readiness_proof_is_distinct_from_entry_permission(maintenance):
    e,b,db,_,_=maintenance;record=reviewed(maintenance);acknowledge(maintenance,record)
    proof=asyncio.run(R.control_snapshot(e.root,now=lambda:b.time))
    result=R.summarize(proof,controls=[],auth={'covers_session':True,'expiry_known':True,'refresh_present':True,'client_present':True,'refresh_proven':True},reset_valid=True,next_session_info={},recovery_class='NORMAL')
    assert result['UNATTENDED_LIVE_READY'] and not result['entry_eligible_now']


def test_known_structured_recovery_is_not_reported_as_operator_required(maintenance):
    e,b,db,_,_=maintenance
    proof=asyncio.run(R.control_snapshot(e.root,now=lambda:b.time))
    auth={'covers_session':True,'expiry_known':True,'refresh_present':True,'client_present':True,'refresh_proven':True}
    result=R.summarize(proof,controls=['KILL'],auth=auth,reset_valid=True,next_session_info={},recovery_class='B')
    assert result['UNATTENDED_LIVE_READY'] and result['automatic_recovery_pending']
    assert not result['operator_action_required'] and not result['entry_eligible_now']
    assert (e.runtime/'KILL').exists() and not b.calls


@pytest.mark.parametrize('fault',['expiry','refresh','reset','permission','unknown_recovery'])
def test_next_session_known_block_cannot_be_monitoring_ready_shortcut(maintenance,fault):
    e,b,db,_,_=maintenance;record=reviewed(maintenance);acknowledge(maintenance,record)
    proof=asyncio.run(R.control_snapshot(e.root,now=lambda:b.time))
    auth={'covers_session':True,'expiry_known':True,'refresh_present':True,'client_present':True,'refresh_proven':True}
    if fault=='expiry':auth['expiry_known']=False
    elif fault=='refresh':auth['refresh_proven']=False
    elif fault=='permission':proof['live_enabled']=False
    result=R.summarize(proof,controls=[],auth=auth,reset_valid=fault!='reset',next_session_info={},recovery_class='A' if fault=='unknown_recovery' else 'NORMAL')
    assert result['MONITORING_READY'] and not result['UNATTENDED_LIVE_READY']


@pytest.mark.parametrize('crossing',['trigger','invalidation'])
def test_acknowledgment_never_resets_missed_orb(maintenance,crossing):
    e,b,db,_,_=maintenance;record=reviewed(maintenance);acknowledge(maintenance,record)
    s=e.session();bars=raw(b.time)['SPY'];first=evaluate('SPY',bars,s,s.open+timedelta(minutes=15),e.cfg)
    later=dict(bars[-1],begins_at=iso(s.open+timedelta(minutes=15)),
               high_price=first['trigger'] if crossing=='trigger' else '100',
               low_price=first['invalidation'] if crossing=='invalidation' else '99',close_price='99.5')
    missed=evaluate('SPY',bars+[later],s,s.open+timedelta(minutes=20),e.cfg)
    assert missed['state']=='MISSED' and missed['setup_id']==first['setup_id']


def test_forensic_observation_is_not_counterfactual_trade(env):
    e,b,db=env;day=e.session().day
    for state,reason in [('ARMED','Outside entry range'),('MISSED','ORB missed')]:
        db.event(b.time,'CANDIDATE_ANALYSIS',dict(symbol='SPY',signal={'state':state,'setup_id':'synthetic-same'},final_reason=reason))
        b.time+=timedelta(seconds=1)
    before=db.db.execute('select count(*) from events').fetchone()[0]
    report=reconstruct(e.root,day,b.time)
    assert report['scope']=='INTERIM' and report['actual_trades']==0
    assert report['symbols']['SPY']['first_missed'] and 'NOT_PROVEN' in report['symbols']['SPY']['all_other_gates_passed']
    assert report['counterfactual'].startswith('NOT_COMPUTED') and db.db.execute('select count(*) from events').fetchone()[0]==before


def test_readiness_missing_root_does_not_initialize_database(tmp_path):
    result=asyncio.run(R.certify(tmp_path,broker_check=False))
    assert not result['UNATTENDED_LIVE_READY'] and not list(tmp_path.iterdir())
