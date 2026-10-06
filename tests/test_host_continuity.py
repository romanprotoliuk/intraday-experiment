"""Synthetic host collection failures. No subprocesses, broker or production IO."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import pytest

from trader import host_health as H
from trader.configuration import validate
from trader.models import dt, iso, Unsafe
from test_host_health import evidence

NOW=dt('2025-02-11T15:10:00Z')


def assertions(pid=71,owner=51):
    return '\n'.join(f'   pid {pid}(caffeinate): [0x001] 00:01:00 {kind} named: "synthetic"\n'
        f'\tDetails: caffeinate asserting on behalf of Process ID {owner}\n\tCreated for PID: {owner}. '
        for kind in ('PreventSystemSleep','PreventUserIdleSystemSleep'))


@pytest.mark.parametrize('fault',['none','wrong_child','wrong_owner','missing_system','missing_idle','only_totals','no_owner'])
def test_assertions_require_exact_child_owner_and_both_types(fault):
    output=assertions()
    if fault=='wrong_child':output=assertions(pid=72)
    if fault=='wrong_owner':output=assertions(owner=52)
    if fault=='missing_system':output=output.replace('PreventSystemSleep','OtherAssertion')
    if fault=='missing_idle':output=output.replace('PreventUserIdleSystemSleep','OtherAssertion')
    if fault=='only_totals':output='PreventSystemSleep 1\nPreventUserIdleSystemSleep 1'
    if fault=='no_owner':output=output.replace('Created for PID:','Unrelated PID:')
    assert H.sleep_assertions(output,71,51) is (fault=='none')


def test_ac_required_for_system_sleep_protection_even_when_battery_full():
    config=validate({});r=evidence(NOW);r.update(sleep_prevention=True,ac_power=False)
    assert 'AC power required for verified system sleep prevention' in H.problems(r,NOW,config)
    r['ac_power']=True
    assert not H.problems(r,NOW,config)


@pytest.mark.parametrize('wall_delta,mono_delta',[(300,0),(300,300),(-1,1),(2,1),(1,-1)])
def test_continuity_detects_sleep_stall_or_wall_step(wall_delta,mono_delta):
    c=H.Continuity(validate({}));assert not c.observe(NOW,100)
    assert c.observe(NOW+timedelta(seconds=wall_delta),100+mono_delta)
    assert c.generation==1


def test_normal_poll_preserves_generation():
    c=H.Continuity(validate({}));c.observe(NOW,100)
    assert not c.observe(NOW+timedelta(seconds=5),105)
    assert c.generation==0


def files(tmp_path,record):
    (tmp_path/'host-health.json').write_text(json.dumps(record))
    (tmp_path/'supervisor.json').write_text(json.dumps({'pid':123}))


def test_read_then_clock_avoids_false_future_rejection(tmp_path,monkeypatch):
    files(tmp_path,evidence(NOW));original=Path.read_text;after=NOW+timedelta(milliseconds=3)
    def racing_read(path,*a,**kw):
        if path.name=='host-health.json':
            path.write_text(json.dumps(evidence(after)))
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'read_text',racing_read)
    # Previous API usage rejected the freshly published, perfectly valid file.
    with pytest.raises(Unsafe,match='future'):H.require_host(tmp_path,NOW)
    H.require_host(tmp_path,lambda:after)


@pytest.mark.parametrize('age',[16,-.001])
def test_post_read_clock_does_not_tolerate_genuine_stale_or_future(tmp_path,age):
    files(tmp_path,evidence(NOW-timedelta(seconds=age)))
    with pytest.raises(Unsafe,match='stale or future'):H.require_host(tmp_path,lambda:NOW)


def test_cached_evidence_rejected_immediately_after_clock_discontinuity(tmp_path,monkeypatch):
    r=evidence(NOW);r['monotonic_at']=100;files(tmp_path,r)
    monkeypatch.setattr(H.time,'monotonic',lambda:101)
    with pytest.raises(Unsafe,match='continuity changed'):
        H.require_host(tmp_path,lambda:NOW+timedelta(seconds=2))


def test_restart_cannot_use_previous_supervisor_evidence(tmp_path):
    files(tmp_path,evidence(NOW))
    (tmp_path/'supervisor.json').write_text(json.dumps({'pid':124}))
    with pytest.raises(Unsafe,match='another supervisor'):H.require_host(tmp_path,lambda:NOW)


def test_timezone_equivalence_is_not_a_clock_error():
    r=evidence(NOW);r['at']='2025-02-11T10:10:00-05:00'
    assert not H.problems(r,NOW)


class Clock:
    def __init__(self):self.wall=NOW;self.mono=100
    def advance(self,seconds,monotonic=True):
        self.wall+=timedelta(seconds=seconds)
        if monotonic:self.mono+=seconds


def harness(tmp_path,monkeypatch):
    config=validate({});clock=Clock();calls=[];fault={'mode':None}
    def sup():
        (tmp_path/'supervisor-health.json').write_text(json.dumps(dict(
            pid=51,inhibitor_pid=71,sleep_prevention=True,at=clock.wall.timestamp())))
    sup()
    async def command(*args,**kw):
        calls.append(args)
        if args[0].endswith('sntp'):
            if fault['mode']=='ntp_sleep':clock.advance(240,False)
            if fault['mode']=='ntp_failure':raise TimeoutError('synthetic')
            return '+0.001 +/- 0.002 time.apple.com ::1\n'
        if args[-1]=='batt':
            if fault['mode']=='power_sleep':clock.advance(240,False)
            return "Now drawing from 'AC Power'\n -InternalBattery-0 (id=77)\t88%; charging present: true\n"
        if args[-1]=='assertions':
            return '' if fault['mode']=='no_assertions' else assertions()
        return '"AppleClamshellState" = No'
    monkeypatch.setattr(H,'command',command)
    continuity=H.Continuity(config);record={}
    def run():
        asyncio.run(H.collect(tmp_path,51,config,record,continuity,lambda:clock.wall,lambda:clock.mono))
        return deepcopy(record)
    return run,clock,calls,fault,sup


def test_sleep_invalidates_cached_clock_and_requires_new_measurement(tmp_path,monkeypatch):
    run,c,calls,fault,sup=harness(tmp_path,monkeypatch)
    assert run()['healthy'];assert len([x for x in calls if x[0].endswith('sntp')])==1
    c.advance(5);sup();assert run()['healthy']
    assert len([x for x in calls if x[0].endswith('sntp')])==1
    c.advance(240,False);sup();fault['mode']='ntp_failure'
    assert not run()['healthy']
    fault['mode']=None;c.advance(5);sup();recovered=run()
    assert recovered['healthy'] and recovered['continuity_generation']==1
    assert recovered['clock']['at']==iso(c.wall)
    assert len([x for x in calls if x[0].endswith('sntp')])==3


@pytest.mark.parametrize('mode',['power_sleep','ntp_sleep'])
def test_sleep_during_collection_cannot_publish_healthy_record(tmp_path,monkeypatch,mode):
    run,c,calls,fault,sup=harness(tmp_path,monkeypatch);fault['mode']=mode
    r=run();assert not r['healthy'] and not r['collection_valid'] and not r['clock']
    fault['mode']=None;c.advance(5);sup()
    assert run()['healthy']


def test_running_inhibitor_without_assertions_is_not_sleep_protection(tmp_path,monkeypatch):
    run,c,calls,fault,sup=harness(tmp_path,monkeypatch);fault['mode']='no_assertions'
    r=run();assert not r['healthy'] and not r['sleep_prevention']


def test_post_resume_generation_cannot_reuse_old_clock():
    r=evidence(NOW);r.update(continuity_generation=2,collection_valid=True)
    r['clock']['continuity_generation']=1
    assert 'Clock measurement predates host continuity change' in H.problems(r,NOW)
