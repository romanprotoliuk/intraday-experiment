"""Synthetic research invariants; no broker or production filesystem access."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import pytest

from test_multistrategy import day,frame,Always
from trader.models import dt,Unsafe
from trader.multistrategy.strategies import STRATEGIES,Parameters
from trader.research2.features import enrich,build_tape
from trader.research2.hypotheses import Hypothesis,NEW,Variant,ExitVariant
from trader.research2.regimes import classify,eligible
from trader.research2.replay import run,Costs
from trader.research2.analysis import event,correlation,decomposition


def fixture(name):
    d=day();f=enrich(frame(d,20));v=dict(f.values);bs=[dict(b) for b in f.bars]
    v.update(price=100.3,vwap=100.1,vwap_slope_15=.1,momentum_15=.002,momentum_5=.001,
        body_fraction=.8,rvol_tod=2,atr_14=.5,short_range_scale=.1,range_expansion=1.5,both_above_vwap=True,
        open=99.8,minutes_since_open=100,consecutive_directional_bars=3,previous_bar_volume_ratio=1.2,
        sma_6=100.1,sma_6_slope=.01,prior_range_high_6=100.2,prior_range_low_6=100.,
        prior_range_high_12=100.5,prior_range_low_12=99.5,vwap_reclaim=True,momentum_acceleration=.001,
        vwap_distance_z=-3,lower_wick_fraction=.4,or15_low=100.,or15_high=102.,vwap_crosses=3)
    bs[-1].update(open_price='100.1',low_price='100.1',high_price='100.31',close_price='100.3')
    if name=='opening_drive':
        f=replace(f,at=(d.session.open+timedelta(minutes=10)).isoformat());v['minutes_since_open']=10
    if name=='failed_breakout':
        bs[-3]['close_price']='99.8';bs[-1]['low_price']='100.01'
    if name=='exhaustion_reversion':
        v.update(price=100.3,vwap=102.,vwap_slope_15=0,momentum_15=0,previous_bar_volume_ratio=.5)
    if name=='momentum_continuation':v['momentum_15']=.006
    return d,replace(f,values=v,bars=tuple(bs))


@pytest.mark.parametrize('name',NEW)
def test_each_new_family_positive_negative_and_cutoff(name):
    d,f=fixture(name);h=Hypothesis(name)
    assert h.evaluate(f,d.session).signal is not None
    negative=replace(f,values={**f.values,'vwap_slope_15':None,'momentum_15':None,'minutes_since_open':100,'gap_atr':None})
    assert h.evaluate(negative,d.session).signal is None
    late=replace(f,at=(d.session.close-timedelta(minutes=30)).isoformat())
    assert h.evaluate(late,d.session).signal is None
    assert h.fingerprint!=Hypothesis(name,threshold=.8).fingerprint
    assert name not in STRATEGIES


def test_regime_boundary_and_uncertainty_fail_closed():
    d,f=fixture('trend_pullback')
    assert eligible('trend_pullback',f)
    f=replace(f,values={**f.values,'momentum_15':0,'vwap_slope_15':.2,'gap_atr':None})
    assert 'UNCERTAIN' in classify(f).labels and not eligible('trend_pullback',f)


def test_enrichment_never_uses_current_bar_in_prior_compression():
    d=day();f=enrich(frame(d,15))
    bars=list(f.bars);bars[-1]={**bars[-1],'high_price':'999'}
    changed=enrich(replace(f,bars=tuple(bars)))
    assert changed.values['prior_range_high_6']==f.values['prior_range_high_6']


def test_research_tape_excludes_future_and_unobserved_previous_day():
    d=day();a=build_tape([d]);changed=deepcopy(d)
    for b in changed.minutes['SPY'][16:]:b['high_price']='999'
    for b in changed.five['SPY'][4:]:b['high_price']='999'
    b=build_tape([changed])
    assert a[d.session.day][15]['SPY'].audit()==b[d.session.day][15]['SPY'].audit()
    assert a[d.session.day][15]['SPY'].values['previous_range'] is None


@pytest.mark.parametrize('date,length',[('2023-03-10',390),('2023-03-13',390),('2023-07-03',210)])
def test_research_calendar_and_completed_minutes(date,length):
    d=day(date);t=build_tape([d]);f=t[d.session.day][10]['SPY']
    assert f.values['momentum_1']==0 and len(d.minutes['SPY'])==length
    assert max(t[d.session.day])==length-5


def test_stop_first_exit_time_and_risk_caps():
    d=day();d.minutes['SPY'][16].update(low_price='98',high_price='104')
    r=run([d],build_tape([d]),Always())
    spy=next(t for t in r['trades'] if t['symbol']=='SPY')
    assert spy['exit_reason']=='STOP' and not spy['exit_time_exact']
    assert dt(spy['exit_at'])==d.session.open+timedelta(minutes=17)
    assert spy['pnl']<0 and spy['gross_pnl_same_fills']>spy['pnl']
    for t in r['trades']:assert float(t['cost'])<=10 and float(t['planned_risk'])<=.15
    assert r['daily'][0]['attempts']==2 and r['funnel']['filled']==2


def test_pending_failure_consumes_setup_and_no_fill_does_not_create_pnl():
    d=day();d.minutes['SPY'][15]['low_price']='98'
    r=run([d],build_tape([d]),Always())
    assert r['funnel']['reserved']==2 and r['funnel']['filled']==1
    assert r['diagnostics']['FILL:STOP_WHILE_PENDING']==1
    r=run([day()],build_tape([day()]),Always(),Costs(fill_fraction=0))
    assert not r['trades'] and r['metrics']['pnl']==0 and r['funnel']['reserved']==2


def test_intrabar_exit_does_not_release_symbol_at_same_minute_open():
    from trader.multistrategy.strategies import Strategy
    class Repeating(Strategy):
        id='repeating-synthetic'
        def evaluate(self,f,s):return self.signal(f,s,100,99,'synthetic',anchor=f.at,vwap_exit=False)
    d=day();d.minutes['SPY'][20]['low_price']='98'
    r=run([d],build_tape([d]),Repeating())
    starts=[dt(x['at']) for x in r['reservations'] if x['symbol']=='SPY']
    assert d.session.open+timedelta(minutes=20) not in starts
    assert d.session.open+timedelta(minutes=21) in starts


def test_outside_band_distinguishes_waiting_from_extension():
    d=day()
    for s in ('SPY','QQQ'):
        d.minutes[s][15].update(open_price='99.9',low_price='99.8')
        d.minutes[s][16].update(open_price='101',high_price='101.1')
    r=run([d],build_tape([d]),Always())
    assert r['diagnostics']['RISK:BELOW_TRIGGER']>=2
    assert r['diagnostics']['RISK:ABOVE_ENTRY_BAND']>=2


def test_new_research_strategies_cannot_be_enabled_via_production_registry():
    from trader.multistrategy.registry import Registry
    for name in NEW:
        with pytest.raises(Unsafe,match='Unknown'):
            Registry(dict(version=1,mode='MULTI_LIVE',strategies=[dict(id=name,status='LIVE_ELIGIBLE',live_enabled=True)]))


def test_failed_breakout_timeout_and_opening_setup_identity():
    d,f=fixture('failed_breakout');bs=list(f.bars);bs[-3]['close_price']='100.01';bs[3]['close_price']='99.8'
    assert Hypothesis('failed_breakout').evaluate(replace(f,bars=tuple(bs)),d.session).signal is None
    d,f=fixture('opening_drive');h=Hypothesis('opening_drive')
    a=h.evaluate(f,d.session).signal
    later=replace(f,at=(d.session.open+timedelta(minutes=15)).isoformat(),values={**f.values,'minutes_since_open':15})
    b=h.evaluate(later,d.session).signal
    assert a.setup_id==b.setup_id and dt(a.valid_until)==dt(b.signal_at)


def test_signal_event_response_is_not_trigger_fill_and_is_directional():
    d=day();f=enrich(frame(d,3));d.minutes['SPY'][31]['open_price']='101'
    long=event(d,'SPY',f);short=event(d,'SPY',f,-1)
    assert long['returns']['15']['gross_bps']==-short['returns']['15']['gross_bps']
    assert long['returns']['15']['net_bps']<long['returns']['15']['gross_bps']
    assert short['direction']=='SHORT_DIAGNOSTIC_ONLY'
    assert correlation([0,0],[0,0]) is None


def test_train_only_bins_do_not_change_with_future_labels_or_features():
    d=day();r=event(d,'SPY',enrich(frame(d,3)))
    protocol=dict(train=['2023-03-01','2023-04-01'],validation=['2023-04-01','2023-05-01'],replication_test=['2023-05-01','2023-06-01'])
    future={**r,'day':'2023-05-01','features':{**r['features'],'or15_width':10000}}
    a=decomposition([r],protocol);b=decomposition([r,future],protocol)
    assert a['or15_width']['train_only_edges']==b['or15_width']['train_only_edges']


@pytest.mark.parametrize('name',list(STRATEGIES))
def test_old_definitions_unchanged_and_variants_distinct(name):
    old=STRATEGIES[name]();p=old.fingerprint
    assert Variant(name).fingerprint!=p
    assert ExitVariant(old,'hold20').fingerprint!=p
    assert old.fingerprint==p and old.parameters==Parameters()
