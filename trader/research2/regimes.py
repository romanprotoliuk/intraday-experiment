"""Research-only deterministic multi-label regimes and declared eligibility."""
from dataclasses import dataclass

VERSION='research-regimes-2'


@dataclass(frozen=True)
class Regime:
    labels: tuple
    reasons: tuple
    version: str=VERSION


def classify(frame):
    f=frame.values;labels=[];reasons=[]
    def add(label,reason):labels.append(label);reasons.append(reason)
    price=f['price'];vwap=f['vwap'];s=f.get('vwap_slope_15');m=f.get('momentum_15');atr=f.get('atr_14')
    if s is not None and m is not None:
        if price>vwap and s>0 and m>0:add('TREND_UP','price above VWAP; positive 15-minute slope and return')
        elif price<vwap and s<0 and m<0:add('TREND_DOWN','price below VWAP; negative 15-minute slope and return')
        elif atr and abs(s)<=.25*atr and f['vwap_crosses']>=2:
            add('RANGE','at least two VWAP crosses and absolute slope <= 0.25 ATR')
            add('MEAN_REVERSION_FAVORABLE','range evidence without consistent directional trend')
    vol=f.get('volatility_percentile')
    if vol is not None and vol>=.8:add('HIGH_VOLATILITY','prior comparable-time volatility percentile >=80%')
    elif vol is not None and vol<=.2:add('LOW_VOLATILITY','prior comparable-time volatility percentile <=20%')
    if 10<=f['minutes_since_open']<=30 and f.get('momentum_5') is not None:
        if price>vwap and f['momentum_5']>0 and f['body_fraction'] is not None and f['body_fraction']>=.5:
            add('OPENING_MOMENTUM_UP','first 30 minutes: above VWAP, positive return, body >= half range')
        elif price<vwap and f['momentum_5']<0 and f['body_fraction'] is not None and f['body_fraction']>=.5:
            add('OPENING_MOMENTUM_DOWN','first 30 minutes: below VWAP, negative return, body >= half range')
    if f.get('gap_atr') is not None and abs(f['gap_atr'])>=.1:
        if f['gap_open'] and (price-f['open'])*f['gap_atr']>0:add('GAP_CONTINUATION','material gap remains open and extends')
        elif f.get('gap_fill_fraction') is not None and f['gap_fill_fraction']>=.5:add('GAP_FAILURE','material gap at least half filled')
    context=set(labels)-{'HIGH_VOLATILITY','LOW_VOLATILITY'}
    if not context:add('UNCERTAIN','no positive directional/range/opening/gap evidence')
    return Regime(tuple(labels),tuple(reasons))


ELIGIBLE={
    'opening_drive':{'OPENING_MOMENTUM_UP'},
    'failed_breakout':{'RANGE','GAP_FAILURE','TREND_UP','OPENING_MOMENTUM_UP'},
    'vwap_reclaim':{'TREND_UP','RANGE','GAP_FAILURE','OPENING_MOMENTUM_UP'},
    'trend_pullback':{'TREND_UP'},'range_breakout':{'TREND_UP','OPENING_MOMENTUM_UP'},
    'momentum_continuation':{'TREND_UP','OPENING_MOMENTUM_UP'},
    'exhaustion_reversion':{'MEAN_REVERSION_FAVORABLE'},
}


def eligible(strategy,frame):
    regime=classify(frame)
    return 'UNCERTAIN' not in regime.labels and bool(ELIGIBLE.get(strategy,set())&set(regime.labels))
