"""Synthetic macOS/broker-free maintenance replacement and fault injection."""
import asyncio
import json
import os
import signal
import subprocess
import pytest

from trader import maintenance_host as M
from trader.models import Unsafe
from test_next_session_readiness import maintenance
from test_system_recovery import recovery
from test_persistent import env


@pytest.fixture
def bridge(maintenance,monkeypatch):
    e,b,db,_,_=maintenance
    db.configure('live_enabled',False)
    for marker in ('READ_ONLY','MAINTENANCE'):(e.runtime/marker).write_text('synthetic maintenance')
    class Child:
        pid=556677
        def __init__(self):self.code=None;self.terminated=0;self.killed=0
        def poll(self):return self.code
        def terminate(self):self.terminated+=1;self.code=0
        def wait(self,timeout=None):return self.code
        def kill(self):self.killed+=1;self.code=-9
    child=Child();calls=[];fault={'value':None}
    def spawn(args,**kwargs):calls.append(args);return child
    monkeypatch.setattr(M.subprocess,'Popen',spawn)
    async def command(*args,**kwargs):
        name=args[0];f=fault['value']
        if name.endswith('/pmset') and args[-1]=='batt':
            source='Battery Power' if f=='ac' else 'AC Power';percent=49 if f=='battery' else 100
            return f"Now drawing from '{source}'\n -InternalBattery-0 {percent}%; charging; present: true\n"
        if name.endswith('/ioreg'):return '"AppleClamshellState" = '+('Yes' if f=='lid' else 'No')
        if name.endswith('/ps'):return str(os.getpid()+1 if f=='parent' else os.getpid())
        if name.endswith('/sntp'):
            if f=='ntp_missing':return 'no response'
            if f=='clock':return '+0.100000 +/- 0.001000 time.apple.com\n'
            return '+0.001000 +/- 0.002000 time.apple.com\n'
        if f=='missing':return ''
        owner=os.getpid()+1 if f=='assertion_owner' else os.getpid()
        pid=child.pid+1 if f=='assertion_pid' else child.pid
        types=('PreventSystemSleep',) if f=='one_assertion' else ('PreventSystemSleep','PreventUserIdleSystemSleep')
        return '\n'.join(f'pid {pid}(caffeinate): [fake] {t}\n Created for PID: {owner}.' for t in types)
    monkeypatch.setattr(M,'command',command)
    return e,b,db,child,calls,fault


def verify(host):return asyncio.run(host.verify())


def test_old_disarmed_watchdog_deadlock_has_independent_ownership(bridge):
    e,b,db,child,calls,_=bridge
    host_file=e.runtime/'host-health.json';old=host_file.read_bytes()
    before=db.db.total_changes;kill=(e.runtime/'KILL').read_bytes()
    with M.MaintenanceHost(e.root) as host:
        proof=verify(host)
        assert proof['scope']=='DISARMED_MAINTENANCE_ONLY'
        assert proof['owner_pid']==os.getpid() and proof['inhibitor_pid']==child.pid
        assert proof['sleep_prevention'] and calls==[['/usr/bin/caffeinate','-is','-w',str(os.getpid()),'-t','900']]
        assert child.poll() is None
    assert child.terminated==1 and db.db.total_changes==before
    assert host_file.read_bytes()==old and (e.runtime/'KILL').read_bytes()==kill
    assert not db.settings().live_enabled and not b.calls and not b.cancels


@pytest.mark.parametrize('fault',['ac','battery','lid','parent','missing','assertion_owner','assertion_pid','one_assertion','clock','ntp_missing'])
def test_physical_or_clock_failure_cannot_certify(bridge,fault):
    e,_,_,child,_,f=bridge;f['value']=fault
    with pytest.raises(Unsafe):
        with M.MaintenanceHost(e.root) as host:verify(host)
    assert child.terminated==1


@pytest.mark.parametrize('fault',['live','readonly','maintenance','kill','account','config','position','intent','stop','database'])
def test_state_change_blocks_bridge(bridge,fault):
    e,_,db,child,_,_=bridge
    with M.MaintenanceHost(e.root) as host:
        verify(host)
        if fault=='live':db.configure('live_enabled',True)
        elif fault in ('readonly','maintenance'):(e.runtime/('READ_ONLY' if fault=='readonly' else 'MAINTENANCE')).unlink()
        elif fault=='kill':(e.runtime/'KILL').write_text('another hold')
        elif fault=='account':db.put('account_number','different-account')
        elif fault=='config':db.put('config_fingerprint','different')
        elif fault=='position':db.db.execute("INSERT INTO trades VALUES('t','SPY','2030-01-01','OPEN','{}')")
        elif fault=='intent':db.db.execute("INSERT INTO orders VALUES('o','i','t','buy','AMBIGUOUS',NULL,'{}')")
        elif fault=='stop':(e.runtime/'STOP').write_text('stop')
        else:db.db.execute('DROP TABLE trades')
        with pytest.raises(Exception):verify(host)
    assert child.terminated==1


def test_worker_exit_or_watchdog_replacement_does_not_transfer_bridge_owner(bridge):
    e,_,db,child,_,_=bridge
    with M.MaintenanceHost(e.root) as host:
        proof=verify(host)
        db.put('worker_identity',{'pid':876,'generation':'replacement','supervisor_pid':765})
        (e.runtime/'supervisor-health.json').write_text('{}')
        assert verify(host)['owner_pid']==proof['owner_pid']
        assert child.poll() is None


@pytest.mark.parametrize('fault',['expired','clock_backward','child_exit','owner_change'])
def test_lease_failure_blocks_and_cleans_up(bridge,monkeypatch,fault):
    e,_,_,child,_,_=bridge
    with M.MaintenanceHost(e.root) as host:
        if fault=='expired':host.started-=901
        elif fault=='clock_backward':host.started+=1
        elif fault=='child_exit':child.code=1
        else:host.owner+=1
        with pytest.raises(Unsafe):verify(host)
    assert child.poll() is not None


def test_signal_restored_and_cleanup_on_exception(bridge):
    e,_,_,child,_,_=bridge;old=signal.getsignal(signal.SIGTERM)
    with pytest.raises(Unsafe):
        with M.MaintenanceHost(e.root) as host:host.interrupted()
    assert signal.getsignal(signal.SIGTERM)==old and child.terminated==1


def test_cleanup_escalates_only_own_child(bridge,monkeypatch):
    e,_,_,child,_,_=bridge
    def wait(timeout=None):
        if not child.killed:raise subprocess.TimeoutExpired('synthetic',timeout)
        return child.code
    monkeypatch.setattr(child,'wait',wait)
    with M.MaintenanceHost(e.root):pass
    assert child.terminated==child.killed==1


@pytest.mark.parametrize('failed_step',[None,'preflight','stop','install','start','certify','handoff'])
def test_controlled_replacement_sequence_and_failure_cleanup(bridge,monkeypatch,failed_step):
    e,_,_,child,_,_=bridge;steps=[]
    def step(name):
        async def run():
            assert child.poll() is None;steps.append(name)
            if name==failed_step:raise Unsafe('Synthetic deployment failure')
            return {'verified':True}
        return run
    def ordinary_host(runtime):
        steps.append('handoff')
        if failed_step=='handoff':raise Unsafe('New watchdog assertion missing')
    monkeypatch.setattr(M,'require_host',ordinary_host)
    run=lambda:asyncio.run(M.guarded_replacement(e.root,**{n:step(n) for n in ('preflight','stop','install','start','certify')}))
    if failed_step:
        with pytest.raises(Unsafe):run()
        assert steps[-1]==failed_step
    else:
        result=run();assert result['service']['verified']
        assert steps==['preflight','stop','preflight','install','start','certify','handoff']
    assert child.terminated==1 and (e.runtime/'KILL').exists()


@pytest.mark.parametrize('during',['stop','install','start'])
@pytest.mark.parametrize('physical_fault',['ac','lid'])
def test_physical_loss_during_replacement_aborts_next_step(bridge,monkeypatch,during,physical_fault):
    e,_,_,child,_,fault=bridge;steps=[]
    def step(name):
        async def run():
            steps.append(name)
            if name==during:fault['value']=physical_fault
        return run
    monkeypatch.setattr(M,'require_host',lambda r:None)
    with pytest.raises(Unsafe):
        asyncio.run(M.guarded_replacement(e.root,**{n:step(n) for n in ('preflight','stop','install','start','certify')}))
    assert steps[-1]==during and child.terminated==1


@pytest.mark.parametrize('seconds',[2,-2,121])
def test_sleep_or_wall_clock_jump_during_collection_rejected(bridge,monkeypatch,seconds):
    from datetime import timedelta
    e,_,_,child,_,_=bridge;real=M.datetime;calls=[]
    class Clock:
        @staticmethod
        def now(tz):
            calls.append(None)
            return real.now(tz)+timedelta(seconds=seconds if len(calls)>1 else 0)
    with pytest.raises(Unsafe):
        with M.MaintenanceHost(e.root) as host:
            monkeypatch.setattr(M,'datetime',Clock);verify(host)
    assert child.terminated==1


def test_second_controller_cannot_acquire_maintenance_ownership(bridge):
    e,_,_,child,calls,_=bridge
    with M.MaintenanceHost(e.root) as first:
        verify(first)
        with pytest.raises(Unsafe):
            with M.MaintenanceHost(e.root):pass
        assert len(calls)==1 and child.poll() is None
    assert child.terminated==1
