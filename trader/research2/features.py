"""Additive causal research features; existing live feature engine is unchanged."""
from dataclasses import replace
from datetime import timedelta
from statistics import mean

from trader.models import dt
from trader.multistrategy.features import FeatureEngine

VERSION = 'research-features-2'


def enrich(frame, previous=None):
    f=dict(frame.values);bs=frame.bars
    o,h,l,c=([float(b[k]) for b in bs] for k in ('open_price','high_price','low_price','close_price'))
    span=h[-1]-l[-1];n=len(bs)
    f.update(upper_wick_fraction=(h[-1]-max(o[-1],c[-1]))/span if span else None,
             lower_wick_fraction=(min(o[-1],c[-1])-l[-1])/span if span else None,
             short_range_scale=mean(a-b for a,b in zip(h[-6:],l[-6:])),
             day_of_week=dt(frame.at).weekday(),time_of_month=int(frame.session[-2:]),
             previous_high=None,previous_low=None,previous_range=None,premarket_range=None,
             previous_high_distance=None,previous_low_distance=None,
             or15_prior_atr=f['or15_width']/f['prior_daily_atr'] if f['or15_width'] and f['prior_daily_atr'] else None)
    for length in (3,6,12):
        f['sma_'+str(length)]=mean(c[-length:]) if n>=length else None
        f['prior_range_high_'+str(length)]=max(h[-length-1:-1]) if n>length else None
        f['prior_range_low_'+str(length)]=min(l[-length-1:-1]) if n>length else None
    f['sma_6_slope']=mean(c[-6:])-mean(c[-7:-1]) if n>=7 else None
    if previous:
        pb=previous.five[frame.symbol];ph=max(float(b['high_price']) for b in pb);pl=min(float(b['low_price']) for b in pb)
        f.update(previous_high=ph,previous_low=pl,previous_range=ph-pl,
                 previous_high_distance=c[-1]-ph,previous_low_distance=c[-1]-pl)
    return replace(frame,values=f,version=VERSION,unavailable=tuple(sorted(k for k,v in f.items() if v is None)))


def build_tape(days):
    tape={};history=[]
    for day in days:
        engine=FeatureEngine(history);records={}
        previous=history[-1] if history and history[-1].session.day==engine.previous_day(day.session) else None
        for index in range(5,len(day.minutes['SPY']),5):
            now=day.session.open+timedelta(minutes=index)
            rows={s:day.five[s][:index//5] for s in ('SPY','QQQ')}
            records[index]={s:enrich(engine.snapshot(s,rows[s],day.session,now,rows,
                minute_rows=day.minutes[s][:index]),previous) for s in rows}
        tape[day.session.day]=records;history.append(day)
    return tape
