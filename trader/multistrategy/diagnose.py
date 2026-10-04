"""Read-only decomposition of archived research; never changes strategy rules."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo
from datetime import datetime

from .backtest import metrics
from .study import breakdown


def matched_cost(trade, costs):
    """Attribution for the SAME fills, not a counterfactual no-cost strategy.

    Stops/targets are bid-side barriers in replay. Undo slippage only there;
    undo both spread and slippage for market/time/VWAP/open-gap exits.
    """
    slip=costs['slippage_bps']/10000
    half=costs['spread_bps']/20000
    exit_price=trade['exit']
    barrier=trade['exit_reason'] in ('STOP','TARGET','TARGET_GAP')
    reference=exit_price/(1-slip)/(1 if barrier else 1-half)
    return float(trade['estimated_entry_cost'])+float(trade['quantity'])*(reference-exit_price)+costs['fee_per_side']


def bucket(value, edges):
    if value is None:return 'UNAVAILABLE'
    for edge in edges:
        if value<edge:return '<'+str(edge)
    return '>='+str(edges[-1])


def diagnose(archive, results, output):
    archive=Path(archive);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    previous=json.loads(Path(results).read_text());out={}
    for name,old in previous['strategies'].items():
        path=archive/(name+'-base-trades.json');base=json.loads(path.read_text())
        trades=base['trades'];m=metrics(trades,base['daily'],old['metrics']['max_drawdown'])
        if m['trades']!=old['metrics']['trades'] or abs(m['pnl']-old['metrics']['pnl'])>1e-12:
            raise ValueError('Archived trades do not reproduce published results: '+name)
        costs=[matched_cost(t,base['costs']) for t in trades]
        reasons=['LOW_SAMPLE','DATA_LIMITED','EXECUTION_CONSTRAINED']
        gross=mean([t['pnl']+c for t,c in zip(trades,costs)]) if trades else None
        if gross is not None and gross>0 and m['expectancy']<=0:reasons.append('COST_SENSITIVE_OBSERVED_SAMPLE')
        if name=='filtered_orb':reasons.append('FILTER_ATTRITION')
        definitions={
            'orb':'Only five fills; negative aggregate but positive training. Entry-band and pending-price checks dominate. Negative population edge is not established.',
            'filtered_orb':'One losing fill, shared with ORB; signal evaluations fall from 1018 to 36. Filter attrition is measurable; superiority or excessive filtering is not statistically established.',
            'orb_retest':'102 signal evaluations but zero fills. Neither retest expectancy nor exit quality can be estimated.',
            'vwap_trend':'140 signal evaluations and one losing training fill. Intraday ATR warm-up delays availability until 70 minutes after open.',
            'vwap_reversion':'431 signal evaluations but zero fills. Close-standard-deviation warm-up requires 100 minutes; entry-band/friction rejection and expiry prevent a return sample.',
            'gap_continuation':'295 signal evaluations but zero fills. Positive gap/history/volume/OR requirements plus entry-band and delayed-fill rejection prevent a return sample.',
            'gap_fade':'51 signal evaluations but zero fills. Negative gap, reclaim, volume and remaining 2R gap room create a sparse joint condition; no exit inference is possible.'}
        values=lambda t:t['features']['values']
        m.update(expectancy_r=mean([t['r_multiple'] for t in trades]) if trades else None,
                 trades_per_252_sessions=len(trades)*252/len(base['daily']) if base['daily'] else None,
                 return_on_initial_simulated_cash=m['pnl']/50,
                 gross_expectancy_same_fills=gross,net_expectancy=m['expectancy'],
                 matched_execution_cost=sum(costs),short_results=None)
        out[name]=dict(metrics=m,periods=old['periods'],untouched_test=None,
            test_note='Previously inspected chronological replication; NOT untouched',walk_forward=old['walk_forward'],
            parameter_stability=old['sensitivity'],exploratory_ablations=old.get('exploratory_development_only',{}),
            by_symbol=old['by_symbol'],by_regime=old['by_regime'],by_time=old['by_time'],
            by_volatility=breakdown(base,lambda t:bucket(values(t).get('volatility_percentile'),[.2,.8])),
            by_gap=breakdown(base,lambda t:bucket(abs(values(t)['gap_atr']) if values(t).get('gap_atr') is not None else None,[.1,.5])),
            by_exit=breakdown(base,lambda t:t['exit_reason']),diagnostics=base['diagnostics'],
            classes=reasons,failure_reason=definitions[name],failed_gates=old['decision']['failures'],
            archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            caveats=['Repeated signal/minute rejection counts are not unique setups.',
                     'No inference of regime, volatility, symbol, timing or parameter advantage from 0–5 fills.',
                     'Gross add-back holds execution selection fixed; it does not measure a zero-cost rerun.',
                     'Unmeasured NBBO/latency and previously used data preclude live validation.',
                     'No short execution support; long-only results cannot establish bearish equivalence.'])
    report=dict(version='phase2-diagnostics-1',strategies_unchanged=True,strategies=out,
        implementation_findings=[
            'Five-minute finalized signals observed at minute opens can miss a narrow 10%-of-stop-distance entry band.',
            'After initial reservation, a mandatory next-minute fill faces expiry and unchanged reserved-risk veto.',
            'VWAP exit is correctly disabled for mean-reversion and gap-fade signals; the common sizing function does not impose an above-VWAP entry requirement.',
            'Original independent replay logs regimes but does not apply live regime eligibility.',
            'Original estimated exit costs undo spread for bid-side barrier fills; corrected same-fill attribution is reported here without rewriting old results.',
            'Intrabar barrier exits were timestamped at bar start; exact exit time is unidentified within that minute.',
            'No previously unused local multi-year minute dataset was found; one inspected year cannot provide independent final-test evidence.'])
    (output/'previous-diagnostics.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    lines=['# Previous-strategy diagnosis (rules unchanged)','',
        'These are sparse realized replay samples, not population edge estimates. All seven are LOW_SAMPLE, DATA_LIMITED and EXECUTION_CONSTRAINED. No strategy passes the original gates.',
        '', '| Strategy | Trades | Net expectancy $ | Gross same-fill expectancy $ | Primary finding |', '|---|---:|---:|---:|---|']
    fmt=lambda v:'unavailable' if v is None else f'{v:.8f}'
    for name,d in out.items():
        m=d['metrics'];lines.append(f"| {name} | {m['trades']} | {fmt(m['expectancy'])} | {fmt(m['gross_expectancy_same_fills'])} | {d['failure_reason']} |")
    lines+=['','## Interpretation','',
        'Negative realized P&L does not prove NEGATIVE_EDGE. Zero fills does not prove no signal edge. COST_SENSITIVE, REGIME_DEPENDENT, OVERFIT and PARAMETER_FRAGILE need more observations than these results provide. Missing or zero-trade subgroups remain unavailable.',
        '', 'The JSON companion contains all requested estimable statistics, period/symbol/regime/time/volatility/gap/exit breakdowns, prior sensitivity results, counts and provenance. Sharpe is withheld for sparse dependent outcomes. Untouched test performance is unavailable.',
        '', '## Implementation limitations','']+['- '+x for x in report['implementation_findings']]
    (output/'PREVIOUS_DIAGNOSTICS.md').write_text('\n'.join(lines)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--archive',default='reports/multistrategy-2026-10-02/study')
    p.add_argument('--results',default='docs/RESEARCH_RESULTS.json');p.add_argument('--output',default='reports/strategy-phase-2')
    a=p.parse_args();diagnose(a.archive,a.results,a.output)
