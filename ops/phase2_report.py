"""Render aggregate phase-two artifacts without importing broker/runtime modules."""
import json,hashlib,platform,subprocess
from pathlib import Path

ROOT=Path('reports/strategy-phase-2')
STUDY=ROOT/'study'


def read(path):return json.loads(Path(path).read_text())
def write(path,obj):Path(path).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
def fmt(x,n=6):return '—' if x is None else f'{x:.{n}f}' if isinstance(x,(int,float)) else str(x)
def table(headers,rows):return ['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+['| '+' | '.join(map(str,row))+' |' for row in rows]


def main():
    results=read(STUDY/'results.json');strategies=results['strategies'];sup=read(STUDY/'supplement.json')
    interactions=read(STUDY/'interactions.json');recent=read(ROOT/'recent/results.json');protocol=read('docs/PHASE2_PROTOCOL.json')
    out=['# Phase-two research report','',
        '**No strategy satisfies the existing acceptance gates. No new strategy is shadow-eligible or live-eligible.**',
        'The implementation is an isolated offline research package. No production strategy, risk limit, broker path, recovery policy or enablement configuration was changed. The public repository is outside this work.',
        '', '## A. Previous-strategy diagnostics','',
        'The previous seven families yielded 5, 1, 0, 1, 0, 0 and 0 fills respectively. Diagnosis was completed and committed before new hypothesis definitions. See [the complete diagnosis](PHASE2_PREVIOUS_DIAGNOSTICS.md) and its JSON companion for all prior metrics, breakdowns and sensitivity results.',
        'All were LOW_SAMPLE, DATA_LIMITED and EXECUTION_CONSTRAINED. Negative observed P&L is not enough to establish a negative population edge. Removing estimated costs from the same fills leaves ORB, filtered ORB and VWAP trend negative. Filtered ORB exhibits measurable filter attrition; overfiltering, overfitting and regime advantage are not proven.',
        '', '## B. Data audit','',
        'Historical sample: FirstRate one-minute SPY/QQQ OHLCV, 2022-09-30 through 2023-09-29. There are 251 expected sessions: SPY has 250 complete sessions, QQQ 251; the paired replay uses 250. SPY is missing four minutes on 2023-06-05, so that paired day is excluded. No rows are imputed. Strict import checks reject duplicates, malformed/nonfinite OHLCV, nonpositive volume and ambiguous timestamps; no such failures were found in the included input.',
        'Naive vendor timestamps are explicitly interpreted as Eastern time with DST validation. Calendar-defined regular sessions and early closes are used. Prior-day features require the actual preceding exchange session; missing days reset contiguous daily history. Off-hours observations are not certified for premarket features. Corporate-action adjustment basis is unspecified for the old files, volume is not independently verified, and no historical NBBO or tick sequence exists. SPY/QQQ selection avoids a changing constituent-universe reconstruction but remains a chosen two-asset universe and one market year.',
        'The [vendor’s current sample page](https://firstratedata.com/free-intraday-data) supplies a separate two-week sample. The recent check covers 2026-09-21–2026-10-02: ten complete paired sessions, zero exclusions. Vendor metadata describes Eastern-time split-adjusted bars. Originals and hashes are preserved locally; conversion only adds a column header. The period overlaps dates previously observed operationally, so it is **not claimed untouched**. Ten sessions cannot warm up a 14-day daily ATR. No old 2023 history is bridged across the multi-year gap.',
        'The [alternative download service](https://hfdatalibrary.com/pages/download) requires account registration; no account was created and no personal details were transmitted. Raw licensed data, local runtime files and credentials are not committed.',
        '', '## C. Feature analysis','',
        'Research features add prior-day range/high/low distances, candle wicks, short-range scale, SMA3/6/12 and SMA6 slope, prior 6/12-bar consolidation ranges, actual one/three-minute momentum and calendar descriptors. Prior ranges exclude the current candle. Existing VWAP remains an OHLCV typical-price approximation. Research minute features do not imply that the live five-minute feed supplies them.',
        'Feature associations use training-only terciles plus descriptive period-by-period Pearson correlations. Correlations are exploratory, dependent and multiply examined; none becomes an entry filter or validation success. Premarket range remains unavailable. The full feature table, including missing counts, is in PHASE2_SUPPLEMENT.json and PHASE2_ORB_DECOMPOSITION.json.',
        '', '## D. ORB decomposition','',
        'The guarded control reproduces the original five fills and P&L exactly. Unique setup/reservation/fill counts are 500/86/5. Of the 81 failed reservations: 30 are above the band at delayed fill, 25 below trigger, 14 expired and 12 exceed originally reserved risk. Waiting below a trigger is not counted as evidence of a missed breakout. Initial rejection categories overlap across observations and are not disjoint event probabilities.',
        'A separate completed-close OR-crossing study has 831 long and 804 bearish events, with causal first/later crossing identities. Later crossings do not rearm the production ORB. Responses are next-minute mid-open to 30-minute mid-open, with an illustrative spread/slippage deduction; they ignore trigger fills, overlapping exposure and reservations and therefore are not executable profits.', '']
    rows=[]
    for direction,parts in sup['orb_event_breakdowns'].items():
        for period,p in parts.items():
            m=p['all']['summary']['30'];ci=p['all']['net_30m_interval']
            rows.append([direction,period,m['count'],fmt(m['gross_bps'],3),fmt(m['net_bps'],3),f"[{fmt(ci['lower'],3)}, {fmt(ci['upper'],3)}]"])
    out+=table(['Direction','Period','Events','Gross bp','Net bp','Exploratory 95% interval, net bp'],rows)
    out+=['','Intervals use five-session circular blocks and preserve within-day dependence; they are pointwise exploratory intervals, not multiplicity-adjusted acceptance evidence. Long OR continuation does not display a stable positive net response across periods. Neither favorable individual feature bins nor bearish event responses establish a tradeable edge.',
        '', '### Long-event feature correlations (descriptive only)','']
    out+=table(['Feature','Train r','Validation r','Replication-test r'],[[k]+[fmt(v[p]['pearson'],3) for p in ('train','validation','replication_test')] for k,v in sup['orb_feature_correlations']['LONG'].items()])
    out+=['','## E. Existing-family variants','',
        'One variant per family was defined before phase-two returns. Exact rules and hypotheses are in [PHASE2_RULES.md](PHASE2_RULES.md). Variants only use training/validation periods. They are not replacements for production defaults.', '']
    out+=table(['Variant','Dev+validation trades','Net P&L $','Validation trades','Validation expectancy $'],[[k,v['metrics']['trades'],fmt(v['metrics']['pnl']),v['periods']['validation']['trades'],fmt(v['periods']['validation']['expectancy'])] for s in strategies.values() for k,v in s['sensitivity_development_only'].items() if k.endswith('_variant')])
    out+=['','The small positive totals for some ORB variants consist of very few trades. They do not meet uncertainty, period stability or independent-evidence gates. Removing filters changes the cohort; it does not prove the removed filter was harmful.',
        '', '## F. New families and all-strategy summary','',
        'Added opening drive, failed downside breakout reversal, VWAP reclaim, trend pullback, later consolidation breakout, momentum continuation and exhaustion reversion as independent research modules. No new family is registered for broker execution. All executable replays are long-only. Bearish VWAP loss/OR responses are event diagnostics and do not assert short capability.',
        '“OOS” below means the previously used chronological July–September replication period. **Untouched-test trades and expectancy are unavailable for every strategy.** Dollar P&L is based on the unchanged small risk envelope. Missing statistics are shown as —, not zero.', '']
    rows=[]
    for name,v in strategies.items():
        m=v['metrics'];t=v['periods']['test'];positive=sum(x['pnl']>0 for x in v['walk_forward'].values())
        rows.append([name,m['trades'],fmt(m['win_rate'],3),fmt(m['profit_factor'],3),fmt(m['expectancy']),fmt(m['expectancy_r'],3),fmt(m['max_drawdown']),t['trades'],fmt(t['expectancy']),f'{positive}/6 positive blocks','Not established',v['decision']['status']])
    out+=table(['Strategy','Trades','Win rate','PF','Expectancy $','Expectancy R','Max DD $','OOS trades*','OOS expectancy* $','Walk-forward','Robustness','Classification'],rows)
    out+=['','Full winner/loser counts, average win/loss, P&L, total R, return on initial $50 simulated cash, average/median hold, loss streak, trades per 252 sessions, SPY/QQQ, regime, time, volatility, gap and exit breakdowns are in PHASE2_RESULTS.json. Annualized Sharpe is withheld for these sparse dependent fills. Full-run drawdown is marked; subgroup/period drawdown is a closed-trade lower bound.',
        '', '## G. Exit research','',
        'Each family has independent development-only 1.5R/30-minute, 2R/20-minute and structural-reward comparisons. Structural reward uses prior close, OR midpoint, VWAP or 1.5 ATR as appropriate, then freezes the proposed R ratio and reanchors it to fill. It is not an exact level limit order. Inadequate/unavailable structural reward rejects the setup. Partial exits and trailing execution were not added without execution evidence.',
        'Matched-entry comparisons in PHASE2_SUPPLEMENT.json hold symbol, fill timestamp and size fixed. Hold-20 changes some losses, but no exit alternative establishes robust validation expectancy. Many families have no filled entries under any exit, so exit quality cannot be inferred. Changing exits can change later opportunity and fill selection; aggregate comparisons alone are not a causal exit experiment.',
        '', '## H. Regime analysis','',
        'Research regime v2 adds explicit up/down opening momentum and range/mean-reversion context to causal trend, volatility and gap labels. Each new family declares an eligibility set; UNCERTAIN blocks entries. Existing v1 strategies are replayed as unchanged controls and are not silently retrofitted with the new regime filter. Exact conditions/reasons are documented in PHASE2_RULES.md. Counts are in PHASE2_REGIMES.json. Regime-specific fill samples are too small to validate regime advantage.',
        '', '## I. Strategy interactions','',
        'Daily return and drawdown correlation matrices, exact simultaneous-signal counts, regime overlap and same-symbol position overlap are recorded. The four nonconstant base books give the following descriptive daily-return correlations; the other ten have zero variance and undefined correlations.', '']
    active=[k for k,v in strategies.items() if v['metrics']['trades']]
    out+=table(['Strategy']+active,[[a]+[fmt(interactions['return_correlation'][a][b],3) for b in active] for a in active])
    out+=['','ORB/filtered ORB and VWAP trend/reclaim share exposure; they are not independent edges. Near-zero correlations from a few nonoverlapping fills do not establish diversification. Independent books are not added into a fictitious portfolio. Any eventual combination must retain existing deterministic arbitration, contradictory/equal-priority no-trade behavior, per-symbol ownership and shared atomic risk limits.',
        '', '## J. Execution assumptions','',
        'Base: 1bp spread, 1bp per-side slippage, zero assumed cash fee. Stress: 5bp spread, 2bp slippage, $0.01 per-side fee and one extra minute. Partial-fill scenario: 50% size with extra delay/fees and cancelled remainder. Separate wider-spread, higher-slippage and delayed-entry tests isolate assumptions; a zero-fill scenario verifies failure handling, not realistic queue probability. Zero-cost reruns still retain the production planning buffer.',
        'Completed five-minute signals are observed at minute opens. A reservation precedes a next-minute fill. The 10%-of-stop-distance entry band, friction veto, original reserved-risk cap, $1 minimum, $10 capital, $0.15 planned trade risk, $30 exposure, $1.50 gross-loss allowance, five attempts and $20 reserve are unchanged. Profits never replenish the allowance. Price jumps may exceed a planned loss despite conservative modeling.',
        'Replay chronology was corrected: opening exits, pending fills and reservations precede intrabar exits. A future intrabar exit cannot release cash at that minute’s open. Stop-first ambiguity and unfavorable gaps apply. Barrier time is an interval, realized at its upper endpoint. Gross same-fill attribution now avoids double-counting spread at bid-side barriers. These corrections preserve all primary historical P&L/counts in this study.',
        'Historical bars cannot certify NBBO, quote freshness, microsecond timing, actual latency or queue partial fills. Missing input sessions are excluded; synthetic fresh quotes are declared assumptions. Production freshness checks are unchanged and separately covered by the existing tests.',
        '', '## K. Chronological validation methodology','',
        'Train: 2022-09-30–2023-03-31. Validation: 2023-04-01–2023-06-30. Reused replication test: 2023-07-01–2023-09-29. Endpoints are end-exclusive in the protocol. No shuffle, fitted final-period normalization or parameter selection from the last period. Exact rules, costs and gates were frozen before return calculation.',
        'The family correction was made more conservative before results: 100-hypothesis budget covers 14 bases, 42 exit alternatives, seven old-family variants and 14 threshold alternatives. The existing gates remain: positive validation/test/stress expectancy, positive corrected uncertainty lower bound, best-day independence, drawdown ≤$1.50, at least two-thirds positive forward blocks, ≥75% nonnegative validation sensitivity cases with trades, and ≤50% expectancy degradation. Independent untouched data, live feature parity and shadow evidence remain mandatory for live eligibility.',
        'Twenty active days is only the existing minimum for reporting the gate bootstrap, not a universal adequate sample. No family reaches it here. The 5,000-replicate extreme family-adjusted tail would itself need more numerical precision before relying on a marginal future pass; none is claimed.',
        '', '## L. Walk-forward results','',
        'Six monthly forward blocks April–September use fixed parameters and expanding prior-session history, with no future normalization. Every base family has 0/6 positive net blocks; most blocks contain no trades. This is a fixed-rule forward replication, not a successful adaptive walk-forward strategy. Monthly counts and metrics are retained, so zero observations cannot be mistaken for zero-risk evidence.',
        '', '## M. Robustness and sensitivity','',
        'New-family threshold multipliers 0.8/1.2 and three exit alternatives were evaluated only on development/validation data. No broad stable profitable region is established. All wider-cost/delay/partial scenarios and SPY-only/QQQ-only, earlier/later, volatility and gap breakdowns are retained. The recent sample adds one ORB fill and no other family fills; it cannot establish robustness.', '']
    out+=table(['Family','Base fills','Base net expectancy $','Gross same-fill expectancy $','Zero-cost fills','Zero-cost P&L $'],[[k,v['metrics']['trades'],fmt(v['metrics']['expectancy']),fmt(v['metrics']['gross_expectancy_same_fills']),v['scenarios']['zero_execution_cost']['metrics']['trades'],fmt(v['scenarios']['zero_execution_cost']['metrics']['pnl'])] for k,v in strategies.items()])
    out+=['','Positive zero-cost results for some families vanish or lack fills under realistic assumptions. Different costs also change eligibility/fill cohorts: this establishes cost/selection sensitivity, not a cost-only causal explanation. A single favorable stress trade cannot establish robustness. Parameter fragility, overfitting and favorable regimes remain unproven rather than assigned unsupported labels.',
        '', '## N. Rejections and precise failure attribution','']
    out+=table(['Family','Unique setups','Reserved','Filled','Diagnostic categories','Failed acceptance checks'],[[k,v['unique_setups'],v['reservations'],v['fills'],', '.join(v['classes']),', '.join(strategies[k]['decision']['failures'])] for k,v in sup['failure_attribution'].items()])
    out+=['','LOW_SAMPLE means insufficient executed observations/active days for the declared evidence gates. DATA_LIMITED means reused narrow history, missing quote/tick evidence and uncertified live history parity. EXECUTION_CONSTRAINED means signal-to-reservation-to-fill attrition is directly observed. COST_AND_SELECTION_SENSITIVE means positive zero-cost replay fails to carry over, with cohorts explicitly allowed to differ. FILTER_ATTRITION and CAPABILITY_LIMITED describe measured selectivity and an unsupported early execution window, respectively. None proves a population NEGATIVE_EDGE or OVERFIT diagnosis.',
        '', '## O. Shadow eligibility','',
        'None. No research family is inserted into the production shadow registry or enabled. Existing isolated shadow infrastructure remains available for a genuinely gate-passing implementation. No prospective shadow sample was fabricated.',
        '', '## P. Live eligibility','',
        'None. Every new family is implemented for offline research; none is validated, LIVE_ELIGIBLE or LIVE_ENABLED. No activation command is proposed. The approval boundary remains required before enabling any future qualifying strategy.',
        '', '## Q. Production integration','',
        'Changes are additive research/diagnostic modules, tests and documentation. Existing strategy definitions, strategy registry, broker/reconciliation/recovery, KILL, clock/data freshness checks, SQLite schema, live configuration and risk limits are unchanged. No restart, migration or experimental order was performed. New research IDs are explicitly tested to be rejected by the production registry. Private runtime preservation and final reconciliation evidence are stored only in ignored local reports.',
        '', '## R. Tests','',
        'Baseline: 677 passed. Added: 38 tests. Complete final suite: 715 passed, zero failures. Coverage includes each new family’s positive/negative/timeout/enablement boundary; point-in-time features, DST/early close, train-only bins, uncertainty; reserved-risk/duplicate/no-fill behavior; stop-first ambiguity and interval time; same-minute capital chronology; and deterministic event statistics. Existing broker failure injection, atomic reservations, recovery, shadow isolation and live enablement tests remain in the full suite. No tests submit broker orders.',
        '', '## S. Version and reproducibility','',
        'Branch: `research/strategy-phase-2`. Diagnosis/protocol milestone: `cb9f11539cecd078be17401eab65aac44c830482`. Initial framework milestone: `d923d578490e430b22b8c53f25807a3e4c4c2150`. Final packaging commit is the commit containing this report; planned release tag is `v0.3-research-phase2`. Existing v0.1 and v0.2 tags are preserved. PHASE2_VERSION.json records run provenance, source digests, Python/dependencies and all strategy fingerprints. It identifies research source, not the running production process.',
        '', '```sh',
        '.venv/bin/python -m trader.multistrategy.diagnose',
        '.venv/bin/python -m trader.research2.study',
        '.venv/bin/python -m trader.research2.supplement',
        '.venv/bin/python -m trader.research2.recent',
        '.venv/bin/python -m pytest -q',
        '```','',
        'The recent runner requires its frozen protocol and locally downloaded vendor sample. Original and diagnostic-refined runs are preserved separately. Raw data, full trade/candidate books, run source snapshots and test logs remain local under ignored directories. Committed aggregate research artifacts contain no account data, credentials, private logs or production state.',
        '', '## T. Remaining research questions','',
        '1. Acquire independently licensed, previously uninspected multi-year data with reliable historical NBBO or finer execution observations, then freeze a truly untouched evaluation. The data acquisition/coverage problem is not solved by reusing this sample.',
        '2. Quantify live quote-cadence fill feasibility within the unchanged band and reserved-risk checks. Do not loosen those checks to compensate for minute-bar uncertainty.',
        '3. Examine whether any causal entry response survives costs and independent periods before researching more filters. Exit alternatives cannot rescue nonexistent executable sample evidence.',
        '4. Certify prior-session live history and feature parity before promoting any history-dependent family. Early entries and short execution require separate architecture/safety research and approval; they are not silently added here.',
        '5. Require prospective shadow and execution evidence if a future strategy passes statistical gates. Sparse low correlations and isolated winners are not diversification or edge evidence.',
        '', 'This phase produces testable hypotheses, an auditable diagnosis and corrected offline measurement. It does not establish a profitable strategy.']
    Path('docs/PHASE2_REPORT.md').write_text('\n'.join(out)+'\n')
    for name,filename in [('RESULTS','results.json'),('SUPPLEMENT','supplement.json'),('ORB_DECOMPOSITION','orb-decomposition.json'),('INTERACTIONS','interactions.json'),('REGIMES','regime-counts.json')]:
        Path('docs/PHASE2_'+name+'.json').write_bytes((STUDY/filename).read_bytes())
    write('docs/PHASE2_RECENT.json',recent)
    import importlib.metadata
    write('docs/PHASE2_VERSION.json',dict(research_version='0.3',release_tag='v0.3-research-phase2',
        provenance=read(STUDY/'provenance.json'),run_source_files=read(STUDY/'source-files.json'),python=platform.python_version(),
        dependencies={k:importlib.metadata.version(k) for k in ('numpy','pytest','pandas','pandas-market-calendars','httpx','mcp')},
        strategy_fingerprints={k:v['fingerprint'] for k,v in strategies.items()},
        baseline_tests=677,new_tests=38,total_tests=715,new_live_enabled=False,new_shadow_eligible=[],new_live_eligible=[],
        protocol_sha256=hashlib.sha256(Path('docs/PHASE2_PROTOCOL.json').read_bytes()).hexdigest(),
        source_scope='Offline research; no statement about deployed worker code version'))


if __name__=='__main__':main()
