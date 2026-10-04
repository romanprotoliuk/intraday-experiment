"""Reproducible offline phase-two runner. No broker, configuration or deployment IO."""
import argparse,gzip,hashlib,json,pickle,subprocess
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from statistics import mean
import numpy as np

from trader.multistrategy.data import load_vendor
from trader.multistrategy.strategies import STRATEGIES,fingerprint
from trader.multistrategy.study import subset,interval,decision
from .features import build_tape
from .hypotheses import Hypothesis,Variant,ExitVariant,NEW
from .regimes import classify
from .replay import run,Costs
from .analysis import signal_events,summarize_events,orb_events,decomposition,extended_breakdowns,interactions,event


def save(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def prepare(raw,output):
    """Self-generated local cache only. Never load downloaded or user-provided pickle."""
    days,manifest=load_vendor(raw)
    digest=fingerprint(dict(data=manifest,features=hashlib.sha256(Path(__file__).with_name('features.py').read_bytes()).hexdigest()))
    cache=output/('local-tape-'+digest+'.pickle.gz')
    # Cache is used only within this explicitly local ignored output directory.
    if cache.exists():
        with gzip.open(cache,'rb') as handle:tape=pickle.load(handle)
    else:
        print('Building causal research tape',flush=True);tape=build_tape(days)
        with gzip.open(cache,'wb',compresslevel=1) as handle:pickle.dump(tape,handle,protocol=5)
    save(output/'data-audit.json',manifest)
    return days,tape,manifest


def summarize(result,protocol):
    periods={name:subset(result,*protocol[key])['metrics'] for name,key in [('train','train'),('validation','validation'),('test','replication_test')]}
    for name,key in [('train','train'),('validation','validation'),('test','replication_test')]:
        ts=subset(result,*protocol[key])['trades']
        periods[name]['expectancy_r']=mean([t['r_multiple'] for t in ts]) if ts else None
        periods[name]['gross_expectancy_same_fills']=mean([t['gross_pnl_same_fills'] for t in ts]) if ts else None
    walks={month:subset(result,month+'-01',str(np.datetime64(month)+np.timedelta64(1,'M'))+'-01')['metrics']
        for month in sorted({d['day'][:7] for d in result['daily'] if protocol['validation'][0]<=d['day']<protocol['replication_test'][1]})}
    m=dict(result['metrics']);ts=result['trades']
    m.update(expectancy_r=mean([t['r_multiple'] for t in ts]) if ts else None,
        gross_expectancy_same_fills=mean([t['gross_pnl_same_fills'] for t in ts]) if ts else None,
        return_on_initial_cash=m['pnl']/50,trades_per_252_sessions=len(ts)*252/len(result['daily']) if result['daily'] else None)
    return dict(metrics=m,periods=periods,walk_forward=walks,funnel=result['funnel'],diagnostics=result['diagnostics'],**extended_breakdowns(result))


def study(raw,output,protocol_path):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);protocol_bytes=Path(protocol_path).read_bytes();protocol=json.loads(protocol_bytes)
    if not Path('docs/PHASE2_PREVIOUS_DIAGNOSTICS.json').exists():raise ValueError('Complete prior diagnosis before strategy research')
    save(output/'frozen-protocol.json',protocol)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    source=fingerprint({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))})
    identity=dict(parent_commit=commit,source_digest=source,protocol_digest=hashlib.sha256(protocol_bytes).hexdigest(),run_id='phase2-'+source[:12])
    save(output/'provenance.json',identity)
    primaries=[c() for c in STRATEGIES.values()]+[Hypothesis(k) for k in NEW]
    save(output/'frozen-hypotheses.json',{s.id:dict(version=s.version,fingerprint=s.fingerprint,parameters=asdict(s.parameters)) for s in primaries})
    days,tape,manifest=prepare(raw,output);development=[d for d in days if d.session.day<protocol['replication_test'][0]]
    orb=orb_events(days,tape);save(output/'orb-events.json',orb);save(output/'orb-decomposition.json',decomposition(orb,protocol))
    bearish=[]
    for day in days:
        for frames in tape[day.session.day].values():
            for sym,f in frames.items():
                if f.values['vwap_loss'] and day.session.entry_window(__import__('trader.models',fromlist=['dt']).dt(f.at)):
                    bearish.append(event(day,sym,f,-1,'VWAP_LOSS'))
    save(output/'bearish-vwap-loss.json',dict(summary=summarize_events(bearish),note='Unsupported short execution: event responses only, never executable trades'))
    results={};books={}
    def replay(ds,strategy,cost=Costs(),label='base',**kw):
        r=run(ds,tape,strategy,cost,run_id=identity['run_id'],code_commit=commit+'+source:'+source[:12],**kw)
        save(output/(strategy.id+'-'+label+'.json'),r)
        return r
    for strategy in primaries:
        print('Research '+strategy.id,flush=True)
        base=replay(days,strategy);books[strategy.id]=base;summary=summarize(base,protocol)
        ev=signal_events(days,tape,base['signals'])
        summary['event_study']={name:summarize_events([r for r in ev if bounds[0]<=r['day']<bounds[1]])
            for name,bounds in [('train',protocol['train']),('validation',protocol['validation']),('replication_test',protocol['replication_test'])]}
        scenarios={}
        for name,args in protocol['costs'].items():
            if name!='base':scenarios[name]=summarize(replay(days,strategy,Costs(**args),name),protocol)
        for name,args in protocol['extra_stresses'].items():
            scenarios[name]=summarize(replay(days,strategy,Costs(**args),name),protocol)
        scenarios['zero_execution_cost']=summarize(replay(days,strategy,Costs(spread_bps=0,slippage_bps=0),'zero_execution_cost'),protocol)
        sensitivity={};exits={}
        if strategy.id in STRATEGIES:
            variant=Variant(strategy.id);sensitivity[variant.id]=summarize(replay(development,variant),protocol)
        else:
            for t in (.8,1.2):
                variant=Hypothesis(strategy.id,threshold=t)
                sensitivity['threshold_'+str(t)]=summarize(replay(development,variant,label='threshold_'+str(t)),protocol)
        for kind in ('target15','hold20','structure'):
            variant=ExitVariant(strategy,kind);exits[kind]=summarize(replay(development,variant),protocol)
        sens={k:{'validation':v['periods']['validation']} for k,v in {**sensitivity,**exits}.items()}
        test=subset(base,*protocol['replication_test']);ci=interval(test['trades'],test['daily'],protocol['gates'])
        gates=decision(summary['periods'],scenarios['stress']['periods']['test'],summary['walk_forward'],sens,ci,protocol)
        summary.update(version=strategy.version,fingerprint=strategy.fingerprint,scenarios=scenarios,sensitivity_development_only=sensitivity,
            exit_research_development_only=exits,confidence_interval=ci,decision=gates,
            classes=['LOW_SAMPLE','DATA_LIMITED','EXECUTION_CONSTRAINED'] if ci['lower'] is None else ['UNVALIDATED_REPLICATION'],
            untouched_test=None,live_enabled=False,shadow_enabled=False)
        results[strategy.id]=summary
        save(output/'results.json',dict(**identity,data=manifest,strategies=results))
        print(strategy.id+': fills='+str(summary['metrics']['trades'])+' status='+gates['status'],flush=True)
    save(output/'interactions.json',interactions(books))
    save(output/'regime-counts.json',dict(Counter(label for records in tape.values() for frames in records.values() for f in frames.values() for label in classify(f).labels)))
    return results


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--raw',default='research/extended/raw');p.add_argument('--output',default='reports/strategy-phase-2/study')
    p.add_argument('--protocol',default='docs/PHASE2_PROTOCOL.json');a=p.parse_args();study(a.raw,a.output,a.protocol)
