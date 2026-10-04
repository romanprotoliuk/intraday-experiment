"""Post-run descriptive attribution. Never selects parameters or grants eligibility."""
import json
from collections import Counter
from pathlib import Path
from statistics import mean
import numpy as np

from trader.models import dt
from .analysis import FEATURES,correlation,summarize_events
from .study import save


def block_interval(rows,days,hold='30',replicates=5000):
    """Fixed five-session circular blocks; all within-day observations stay together."""
    valid=[r for r in rows if r['returns'][hold] is not None]
    active=len({r['day'] for r in valid})
    if active<20:return dict(lower=None,upper=None,active_days=active,reason='Below 20 active-day reporting floor')
    sums=Counter();counts=Counter()
    for r in valid:sums[r['day']]+=r['returns'][hold]['net_bps'];counts[r['day']]+=1
    x=np.array([sums[d] for d in days]);n=np.array([counts[d] for d in days]);rng=np.random.default_rng(1729)
    starts=rng.integers(len(days),size=(replicates,(len(days)+4)//5))
    idx=((starts[:,:,None]+np.arange(5))%len(days)).reshape(replicates,-1)[:,:len(days)]
    totals=n[idx].sum(axis=1);ok=totals>0;values=x[idx].sum(axis=1)[ok]/totals[ok]
    lo,hi=np.quantile(values,[.025,.975])
    return dict(lower=float(lo),upper=float(hi),active_days=active,
        method='Five-session circular block bootstrap, 5000 draws, seed 1729, pointwise 95%',
        caveat='Exploratory event response; overlapping events, multiplicity and execution constraints preclude gate use')


def feature_correlations(rows,protocol):
    out={}
    rows=[{**r,'features':{**r['features'],
        'previous_close_distance':r['features']['price']-r['features']['previous_close'] if r['features'].get('previous_close') else None,
        'vwap_relationship':r['features']['price']>r['features']['vwap']}} for r in rows]
    features=FEATURES+('previous_close_distance','vwap_relationship','or15_distance','lower_wick_fraction','prior_daily_atr','cross_relative_momentum')
    for direction in ('LONG','SHORT_DIAGNOSTIC_ONLY'):
        out[direction]={}
        for feature in features:
            periods={}
            for period,bounds in [('train',protocol['train']),('validation',protocol['validation']),('replication_test',protocol['replication_test'])]:
                obs=[r for r in rows if r['direction']==direction and bounds[0]<=r['day']<bounds[1]
                     and r['features'].get(feature) is not None and r['returns']['30'] is not None]
                periods[period]=dict(n=len(obs),pearson=correlation([float(r['features'][feature]) for r in obs],[r['returns']['30']['net_bps'] for r in obs]))
            out[direction][feature]=periods
    return out


def overlaps(books):
    out={}
    for a,book in books.items():
        out[a]={}
        for b,other in books.items():
            pairs=[(x,y) for x in book['trades'] for y in other['trades'] if x['symbol']==y['symbol']
                   and max(dt(x['entry_at']),dt(y['entry_at']))<min(dt(x['exit_at']),dt(y['exit_at']))]
            out[a][b]=len(pairs)
    return out


def supplement(directory,protocol_path):
    directory=Path(directory);protocol=json.loads(Path(protocol_path).read_text());results=json.loads((directory/'results.json').read_text())
    rows=json.loads((directory/'orb-events.json').read_text());books={k:json.loads((directory/(k+'-base.json')).read_text()) for k in results['strategies']}
    days=[d['day'] for d in books['orb']['daily']];breakdowns={}
    for direction in ('LONG','SHORT_DIAGNOSTIC_ONLY'):
        breakdowns[direction]={}
        for period,bounds in [('train',protocol['train']),('validation',protocol['validation']),('replication_test',protocol['replication_test'])]:
            selected=[r for r in rows if r['direction']==direction and bounds[0]<=r['day']<bounds[1]];pd=[d for d in days if bounds[0]<=d<bounds[1]]
            def describe(rs):return dict(summary=summarize_events(rs),net_30m_interval=block_interval(rs,pd))
            breakdowns[direction][period]=dict(all=describe(selected),by_symbol={s:describe([r for r in selected if r['symbol']==s]) for s in ('SPY','QQQ')},
                first=describe([r for r in selected if r['features']['breakout_number']==1]),later=describe([r for r in selected if r['features']['breakout_number']>1]))
    exits={};attrition={}
    for name,book in books.items():
        index={(t['symbol'],t['entry_at']):t for t in book['trades'] if t['day']<protocol['replication_test'][0]};exits[name]={}
        for kind in ('target15','hold20','structure'):
            altered=json.loads((directory/(name+'_'+kind+'-base.json')).read_text())
            paired=[(index[(t['symbol'],t['entry_at'])],t) for t in altered['trades'] if (t['symbol'],t['entry_at']) in index
                    and abs(float(index[(t['symbol'],t['entry_at'])]['quantity'])-float(t['quantity']))<1e-9]
            exits[name][kind]=dict(matched_entries=len(paired),mean_pnl_change=mean([b['pnl']-a['pnl'] for a,b in paired]) if paired else None,
                note='Same symbol, entry timestamp and quantity; development/validation only; tiny dependent sample, not causal proof of improvement')
        v=results['strategies'][name];f=v['funnel'];z=v['scenarios']['zero_execution_cost']['metrics']
        classes=['LOW_SAMPLE','DATA_LIMITED','EXECUTION_CONSTRAINED']
        if name=='filtered_orb':classes.append('FILTER_ATTRITION')
        if name=='opening_drive':classes.append('CAPABILITY_LIMITED')
        if z['pnl']>0 and (v['metrics']['expectancy'] is None or v['metrics']['expectancy']<=0):classes.append('COST_AND_SELECTION_SENSITIVE')
        attrition[name]=dict(classes=classes,unique_setups=f['unique_signals'],reservations=f['reserved'],fills=f['filled'],
            fill_fraction_of_setups=f['filled']/f['unique_signals'] if f['unique_signals'] else None,
            unique_rejection_reasons=f['unique_rejections'],
            net_sample_negative=v['metrics']['expectancy'] is not None and v['metrics']['expectancy']<0,
            population_negative_edge_established=False,cost_only_causation_established=False,
            classification_note='Cost/selection sensitivity means zero-cost rerun is positive while realistic replay is not; cohorts differ, so this is not a matched cost-only effect.',
            unknowable_from_fills=['regime advantage','symbol advantage','time-of-day advantage','volatility/gap advantage','parameter stability','population Sharpe'])
    save(directory/'supplement.json',dict(orb_event_breakdowns=breakdowns,orb_feature_correlations=feature_correlations(rows,protocol),
        same_symbol_position_overlap=overlaps(books),matched_entry_exit_comparisons=exits,failure_attribution=attrition,
        event_warning='Forward mid-price responses bypass trigger fills; these are descriptive diagnostics, never a deployable strategy or acceptance evidence'))


if __name__=='__main__':supplement('reports/strategy-phase-2/study','docs/PHASE2_PROTOCOL.json')
