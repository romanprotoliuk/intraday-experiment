import pytest
from trader.host_health import problems
from trader.models import dt,iso

@pytest.mark.parametrize('offset,uncertainty,expected',[
    (.089999,0,True),(-.089999,0,True),(.09,0,False),(-.09,0,False),
    (.045,.045,False),(.04,.049999,True),(.090001,0,False),(-.090001,0,False),
])
def test_clock_requires_strictly_below_configured_bound(offset,uncertainty,expected):
    now=dt('2031-04-10T12:00:00Z')
    evidence=dict(at=iso(now),ac_power=True,battery_percent=100,lid_open=True,
        clock=dict(at=iso(now),offset_seconds=offset,uncertainty_seconds=uncertainty))
    assert (not problems(evidence,now,{'clock':{'max_combined_bound_ms':'90'}})) is expected


def test_clock_diagnostics_matches_strict_entry_boundary(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from trader import operator_tools as O
    monkeypatch.setattr(O.C,'load',lambda root: {'clock':{'sample_timeout_seconds':2,'max_combined_bound_ms':'90'}})
    monkeypatch.setattr(O.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,
        stdout='+0.090000 +/- 0.000000 time.apple.com 192.0.2.1\n selected:\n delay: (0.01)\n',stderr=''))
    report=O.clock_diagnostics(tmp_path,samples=1,interval=5)
    assert report['passing_samples']==0
    assert report['policy']=='abs(offset)+uncertainty < 90 ms'
