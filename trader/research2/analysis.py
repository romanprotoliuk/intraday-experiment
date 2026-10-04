"""Descriptive signal studies and dependency-aware summaries, never live gates."""
from collections import Counter
from datetime import timedelta
from statistics import mean
import math
import numpy as np

from trader.models import dt,iso
from trader.multistrategy.diagnose import bucket
from trader.multistrategy.study import breakdown
from .regimes import classify


def event(day,symbol,frame,direction=1,kind='signal'):
    """Next-minute-open fixed-horizon response; no assumed trigger fill."""
    index=int((dt(frame.at)-day.session.open).total_seconds()/60)+1
    rows=day.minutes[symbol];entry=float(rows[index]['open_price']) if index<len(rows) else None
    returns={}
    for hold in (5,15,30):
        end=index+hold
        if entry is None or end>=len(rows)-day.session.liquidation_minutes:returns[str(hold)]=None;continue
        exit_price=float(rows[end]['open_price'])
        gross=direction*(exit_price/entry-1)*10000
        # Identical mid path; assumed one-bp spread and one-bp slippage each side.
        net=(direction*(exit_price-entry)-entry*.00015-exit_price*.00015)/entry*10000
        returns[str(hold)]=dict(gross_bps=gross,net_bps=net)
    return dict(day=day.session.day,symbol=symbol,at=frame.at,direction='LONG' if direction==1 else 'SHORT_DIAGNOSTIC_ONLY',
        kind=kind,features=frame.values,regime=classify(frame).labels,returns=returns)


def summarize_events(rows):
    out={'events':len(rows),'active_days':len({r['day'] for r in rows})}
    for hold in ('5','15','30'):
        valid=[r['returns'][hold] for r in rows if r['returns'][hold] is not None]
        out[hold]=dict(count=len(valid),gross_bps=mean([v['gross_bps'] for v in valid]) if valid else None,
            net_bps=mean([v['net_bps'] for v in valid]) if valid else None)
    return out


def signal_events(days,tape,signals):
    lookup={d.session.day:d for d in days};rows=[]
    for s in signals:
        day=lookup[s['signal_at'][:10]];i=int((dt(s['signal_at'])-day.session.open).total_seconds()/60)
        rows.append(event(day,s['symbol'],tape[day.session.day][i][s['symbol']]))
    return rows


def orb_events(days,tape):
    """All completed-close OR crossings, including later recrosses; causal count."""
    rows=[]
    for day in days:
        counts=Counter()
        for i,frames in tape[day.session.day].items():
            if i<20 or not day.session.entry_window(dt(next(iter(frames.values())).at)):continue
            for sym,f in frames.items():
                prev=float(f.bars[-2]['close_price']);price=f.values['price'];high=f.values['or15_high'];low=f.values['or15_low']
                for direction,cross in ((1,prev<=high<price),(-1,prev>=low>price)):
                    if not cross:continue
                    counts[(sym,direction)]+=1
                    row=event(day,sym,f,direction,'OR_CROSS')
                    row['features']=dict(f.values,breakout_number=counts[(sym,direction)])
                    rows.append(row)
    return rows


FEATURES=('or15_width','or15_prior_atr','or15_width_atr','or15_breakout_strength','body_fraction','upper_wick_fraction',
    'volume','rvol_tod','vwap_distance','vwap_slope_15','gap_atr','premarket_range','previous_range','previous_high_distance',
    'previous_low_distance','previous_close','atr_14','realized_volatility','opening_volatility','both_above_vwap',
    'consecutive_directional_bars','breakout_number','minutes_since_open','day_of_week','time_of_month')


def decomposition(rows,protocol):
    out={}
    for feature in FEATURES:
        values=[r['features'].get(feature) for r in rows if protocol['train'][0]<=r['day']<protocol['train'][1]]
        values=[float(v) for v in values if v is not None and math.isfinite(float(v))]
        edges=sorted(set(float(x) for x in np.quantile(values,[1/3,2/3]))) if values else []
        results={}
        for name,bounds in (('train',protocol['train']),('validation',protocol['validation']),('replication_test',protocol['replication_test'])):
            part=[r for r in rows if bounds[0]<=r['day']<bounds[1]]
            keys=['UNAVAILABLE'] if not edges else ['<'+str(x) for x in edges]+['>='+str(edges[-1]),'UNAVAILABLE']
            results[name]={key:summarize_events([r for r in part if (bucket(r['features'].get(feature),edges) if edges else 'UNAVAILABLE')==key]) for key in keys}
        out[feature]=dict(train_only_edges=edges,periods=results,
            interpretation='Exploratory dependent event association, not executable trade expectancy or a validated filter')
    return out


def extended_breakdowns(result):
    f=lambda t:t['features']['values']
    return dict(by_symbol=breakdown(result,lambda t:t['symbol']),by_regime=breakdown(result,lambda t:'+'.join(t['regime'])),
        by_time=breakdown(result,lambda t:bucket(f(t)['minutes_since_open'],[30,90,180])),
        by_volatility=breakdown(result,lambda t:bucket(f(t).get('volatility_percentile'),[.2,.8])),
        by_gap=breakdown(result,lambda t:bucket(abs(f(t)['gap_atr']) if f(t).get('gap_atr') is not None else None,[.1,.5])),
        by_exit=breakdown(result,lambda t:t['exit_reason']))


def correlation(a,b):
    return float(np.corrcoef(a,b)[0,1]) if len(a)>1 and np.std(a)>0 and np.std(b)>0 else None


def interactions(results):
    names=sorted(results);daily={k:[d['pnl'] for d in results[k]['daily']] for k in names}
    if len({tuple(d['day'] for d in results[k]['daily']) for k in names})>1:raise ValueError('Unaligned portfolio dates')
    draw={k:np.cumsum(v)-np.maximum.accumulate(np.r_[0,np.cumsum(v)])[1:] for k,v in daily.items()}
    signals={k:{(s['symbol'],s['signal_at']) for s in results[k]['signals']} for k in names}
    regimes={k:Counter(label for t in results[k]['trades'] for label in t['regime']) for k in names}
    return dict(return_correlation={a:{b:correlation(daily[a],daily[b]) for b in names} for a in names},
        drawdown_correlation={a:{b:correlation(draw[a],draw[b]) for b in names} for a in names},
        simultaneous_signals={a:{b:len(signals[a]&signals[b]) for b in names} for a in names},
        regime_trade_counts=regimes,contradictory_executable_signals=0,
        caveats=['All executable hypotheses are long-only; bearish events are excluded from arbitration.',
                 'Zero-variance correlations are undefined, not zero. Sparse fill correlations are unstable.',
                 'No independent books are summed into a deployable portfolio; shared risk/reservation gates still required.'])
