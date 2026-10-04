# Phase-two research report

**No strategy satisfies the existing acceptance gates. No new strategy is shadow-eligible or live-eligible.**
The implementation is an isolated offline research package. No production strategy, risk limit, broker path, recovery policy or enablement configuration was changed. The public repository is outside this work.

## A. Previous-strategy diagnostics

The previous seven families yielded 5, 1, 0, 1, 0, 0 and 0 fills respectively. Diagnosis was completed and committed before new hypothesis definitions. See [the complete diagnosis](PHASE2_PREVIOUS_DIAGNOSTICS.md) and its JSON companion for all prior metrics, breakdowns and sensitivity results.
All were LOW_SAMPLE, DATA_LIMITED and EXECUTION_CONSTRAINED. Negative observed P&L is not enough to establish a negative population edge. Removing estimated costs from the same fills leaves ORB, filtered ORB and VWAP trend negative. Filtered ORB exhibits measurable filter attrition; overfiltering, overfitting and regime advantage are not proven.

## B. Data audit

Historical sample: FirstRate one-minute SPY/QQQ OHLCV, 2022-09-30 through 2023-09-29. There are 251 expected sessions: SPY has 250 complete sessions, QQQ 251; the paired replay uses 250. SPY is missing four minutes on 2023-06-05, so that paired day is excluded. No rows are imputed. Strict import checks reject duplicates, malformed/nonfinite OHLCV, nonpositive volume and ambiguous timestamps; no such failures were found in the included input.
Naive vendor timestamps are explicitly interpreted as Eastern time with DST validation. Calendar-defined regular sessions and early closes are used. Prior-day features require the actual preceding exchange session; missing days reset contiguous daily history. Off-hours observations are not certified for premarket features. Corporate-action adjustment basis is unspecified for the old files, volume is not independently verified, and no historical NBBO or tick sequence exists. SPY/QQQ selection avoids a changing constituent-universe reconstruction but remains a chosen two-asset universe and one market year.
The [vendor’s current sample page](https://firstratedata.com/free-intraday-data) supplies a separate two-week sample. The recent check covers 2026-09-21–2026-10-02: ten complete paired sessions, zero exclusions. Vendor metadata describes Eastern-time split-adjusted bars. Originals and hashes are preserved locally; conversion only adds a column header. The period overlaps dates previously observed operationally, so it is **not claimed untouched**. Ten sessions cannot warm up a 14-day daily ATR. No old 2023 history is bridged across the multi-year gap.
The [alternative download service](https://hfdatalibrary.com/pages/download) requires account registration; no account was created and no personal details were transmitted. Raw licensed data, local runtime files and credentials are not committed.

## C. Feature analysis

Research features add prior-day range/high/low distances, candle wicks, short-range scale, SMA3/6/12 and SMA6 slope, prior 6/12-bar consolidation ranges, actual one/three-minute momentum and calendar descriptors. Prior ranges exclude the current candle. Existing VWAP remains an OHLCV typical-price approximation. Research minute features do not imply that the live five-minute feed supplies them.
Feature associations use training-only terciles plus descriptive period-by-period Pearson correlations. Correlations are exploratory, dependent and multiply examined; none becomes an entry filter or validation success. Premarket range remains unavailable. The full feature table, including missing counts, is in PHASE2_SUPPLEMENT.json and PHASE2_ORB_DECOMPOSITION.json.

## D. ORB decomposition

The guarded control reproduces the original five fills and P&L exactly. Unique setup/reservation/fill counts are 500/86/5. Of the 81 failed reservations: 30 are above the band at delayed fill, 25 below trigger, 14 expired and 12 exceed originally reserved risk. Waiting below a trigger is not counted as evidence of a missed breakout. Initial rejection categories overlap across observations and are not disjoint event probabilities.
A separate completed-close OR-crossing study has 831 long and 804 bearish events, with causal first/later crossing identities. Later crossings do not rearm the production ORB. Responses are next-minute mid-open to 30-minute mid-open, with an illustrative spread/slippage deduction; they ignore trigger fills, overlapping exposure and reservations and therefore are not executable profits.

| Direction | Period | Events | Gross bp | Net bp | Exploratory 95% interval, net bp |
|---|---|---|---|---|---|
| LONG | train | 405 | 2.366 | -0.635 | [-4.499, 3.269] |
| LONG | validation | 195 | 2.146 | -0.854 | [-3.868, 2.304] |
| LONG | replication_test | 231 | -0.844 | -3.844 | [-6.679, -0.883] |
| SHORT_DIAGNOSTIC_ONLY | train | 392 | -1.085 | -4.085 | [-9.653, 1.307] |
| SHORT_DIAGNOSTIC_ONLY | validation | 188 | 0.288 | -2.712 | [-6.567, 1.490] |
| SHORT_DIAGNOSTIC_ONLY | replication_test | 224 | 1.222 | -1.778 | [-5.271, 2.263] |

Intervals use five-session circular blocks and preserve within-day dependence; they are pointwise exploratory intervals, not multiplicity-adjusted acceptance evidence. Long OR continuation does not display a stable positive net response across periods. Neither favorable individual feature bins nor bearish event responses establish a tradeable edge.

### Long-event feature correlations (descriptive only)

| Feature | Train r | Validation r | Replication-test r |
|---|---|---|---|
| or15_width | 0.019 | 0.010 | -0.002 |
| or15_prior_atr | 0.030 | 0.006 | -0.011 |
| or15_width_atr | -0.028 | 0.090 | -0.079 |
| or15_breakout_strength | 0.116 | -0.198 | -0.171 |
| body_fraction | -0.064 | 0.100 | -0.064 |
| upper_wick_fraction | 0.037 | -0.049 | 0.034 |
| volume | 0.169 | -0.159 | -0.240 |
| rvol_tod | 0.234 | -0.165 | -0.274 |
| vwap_distance | 0.048 | 0.008 | -0.172 |
| vwap_slope_15 | 0.085 | -0.035 | 0.061 |
| gap_atr | 0.026 | -0.003 | 0.077 |
| premarket_range | — | — | — |
| previous_range | -0.137 | 0.017 | -0.141 |
| previous_high_distance | 0.089 | 0.061 | 0.122 |
| previous_low_distance | -0.007 | 0.070 | 0.023 |
| previous_close | -0.012 | 0.025 | -0.024 |
| atr_14 | 0.100 | -0.161 | 0.111 |
| realized_volatility | 0.060 | -0.027 | -0.029 |
| opening_volatility | 0.055 | 0.044 | 0.074 |
| both_above_vwap | -0.050 | 0.049 | -0.081 |
| consecutive_directional_bars | -0.076 | 0.045 | 0.001 |
| breakout_number | 0.006 | -0.051 | 0.070 |
| minutes_since_open | 0.089 | -0.043 | -0.030 |
| day_of_week | 0.006 | 0.143 | -0.023 |
| time_of_month | 0.017 | 0.096 | 0.035 |
| previous_close_distance | 0.004 | 0.021 | 0.052 |
| vwap_relationship | -0.022 | 0.026 | -0.083 |
| or15_distance | 0.134 | -0.072 | -0.164 |
| lower_wick_fraction | 0.048 | -0.071 | 0.049 |
| prior_daily_atr | 0.024 | 0.002 | 0.016 |
| cross_relative_momentum | 0.019 | -0.134 | 0.049 |

## E. Existing-family variants

One variant per family was defined before phase-two returns. Exact rules and hypotheses are in [PHASE2_RULES.md](PHASE2_RULES.md). Variants only use training/validation periods. They are not replacements for production defaults.

| Variant | Dev+validation trades | Net P&L $ | Validation trades | Validation expectancy $ |
|---|---|---|---|---|
| orb_variant | 3 | 0.002649 | 1 | -0.018498 |
| filtered_orb_variant | 1 | 0.013458 | 0 | — |
| orb_retest_variant | 0 | 0.000000 | 0 | — |
| vwap_trend_variant | 0 | 0.000000 | 0 | — |
| vwap_reversion_variant | 0 | 0.000000 | 0 | — |
| gap_continuation_variant | 0 | 0.000000 | 0 | — |
| gap_fade_variant | 0 | 0.000000 | 0 | — |

The small positive totals for some ORB variants consist of very few trades. They do not meet uncertainty, period stability or independent-evidence gates. Removing filters changes the cohort; it does not prove the removed filter was harmful.

## F. New families and all-strategy summary

Added opening drive, failed downside breakout reversal, VWAP reclaim, trend pullback, later consolidation breakout, momentum continuation and exhaustion reversion as independent research modules. No new family is registered for broker execution. All executable replays are long-only. Bearish VWAP loss/OR responses are event diagnostics and do not assert short capability.
“OOS” below means the previously used chronological July–September replication period. **Untouched-test trades and expectancy are unavailable for every strategy.** Dollar P&L is based on the unchanged small risk envelope. Missing statistics are shown as —, not zero.

| Strategy | Trades | Win rate | PF | Expectancy $ | Expectancy R | Max DD $ | OOS trades* | OOS expectancy* $ | Walk-forward | Robustness | Classification |
|---|---|---|---|---|---|---|---|---|---|---|---|
| orb | 5 | 0.600 | 0.451 | -0.006105 | -0.096 | 0.075408 | 1 | -0.037070 | 0/6 positive blocks | Not established | REJECTED |
| filtered_orb | 1 | 0.000 | 0.000 | -0.037070 | -0.534 | 0.037070 | 1 | -0.037070 | 0/6 positive blocks | Not established | REJECTED |
| orb_retest | 0 | — | — | — | — | 0.000000 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |
| vwap_trend | 1 | 0.000 | 0.000 | -0.068154 | -0.609 | 0.075034 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |
| vwap_reversion | 0 | — | — | — | — | 0.000000 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |
| gap_continuation | 0 | — | — | — | — | 0.000000 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |
| gap_fade | 0 | — | — | — | — | 0.000000 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |
| opening_drive | 0 | — | — | — | — | 0.000000 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |
| failed_breakout | 0 | — | — | — | — | 0.000000 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |
| vwap_reclaim | 3 | 0.000 | 0.000 | -0.030022 | -0.315 | 0.113323 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |
| trend_pullback | 0 | — | — | — | — | 0.000000 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |
| range_breakout | 0 | — | — | — | — | 0.000000 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |
| momentum_continuation | 0 | — | — | — | — | 0.000000 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |
| exhaustion_reversion | 0 | — | — | — | — | 0.000000 | 0 | — | 0/6 positive blocks | Not established | RESEARCH_ONLY |

Full winner/loser counts, average win/loss, P&L, total R, return on initial $50 simulated cash, average/median hold, loss streak, trades per 252 sessions, SPY/QQQ, regime, time, volatility, gap and exit breakdowns are in PHASE2_RESULTS.json. Annualized Sharpe is withheld for these sparse dependent fills. Full-run drawdown is marked; subgroup/period drawdown is a closed-trade lower bound.

## G. Exit research

Each family has independent development-only 1.5R/30-minute, 2R/20-minute and structural-reward comparisons. Structural reward uses prior close, OR midpoint, VWAP or 1.5 ATR as appropriate, then freezes the proposed R ratio and reanchors it to fill. It is not an exact level limit order. Inadequate/unavailable structural reward rejects the setup. Partial exits and trailing execution were not added without execution evidence.
Matched-entry comparisons in PHASE2_SUPPLEMENT.json hold symbol, fill timestamp and size fixed. Hold-20 changes some losses, but no exit alternative establishes robust validation expectancy. Many families have no filled entries under any exit, so exit quality cannot be inferred. Changing exits can change later opportunity and fill selection; aggregate comparisons alone are not a causal exit experiment.

## H. Regime analysis

Research regime v2 adds explicit up/down opening momentum and range/mean-reversion context to causal trend, volatility and gap labels. Each new family declares an eligibility set; UNCERTAIN blocks entries. Existing v1 strategies are replayed as unchanged controls and are not silently retrofitted with the new regime filter. Exact conditions/reasons are documented in PHASE2_RULES.md. Counts are in PHASE2_REGIMES.json. Regime-specific fill samples are too small to validate regime advantage.

## I. Strategy interactions

Daily return and drawdown correlation matrices, exact simultaneous-signal counts, regime overlap and same-symbol position overlap are recorded. The four nonconstant base books give the following descriptive daily-return correlations; the other ten have zero variance and undefined correlations.

| Strategy | orb | filtered_orb | vwap_trend | vwap_reclaim |
|---|---|---|---|---|
| orb | 1.000 | 0.835 | -0.003 | -0.004 |
| filtered_orb | 0.835 | 1.000 | -0.004 | -0.005 |
| vwap_trend | -0.003 | -0.004 | 1.000 | 0.975 |
| vwap_reclaim | -0.004 | -0.005 | 0.975 | 1.000 |

ORB/filtered ORB and VWAP trend/reclaim share exposure; they are not independent edges. Near-zero correlations from a few nonoverlapping fills do not establish diversification. Independent books are not added into a fictitious portfolio. Any eventual combination must retain existing deterministic arbitration, contradictory/equal-priority no-trade behavior, per-symbol ownership and shared atomic risk limits.

## J. Execution assumptions

Base: 1bp spread, 1bp per-side slippage, zero assumed cash fee. Stress: 5bp spread, 2bp slippage, $0.01 per-side fee and one extra minute. Partial-fill scenario: 50% size with extra delay/fees and cancelled remainder. Separate wider-spread, higher-slippage and delayed-entry tests isolate assumptions; a zero-fill scenario verifies failure handling, not realistic queue probability. Zero-cost reruns still retain the production planning buffer.
Completed five-minute signals are observed at minute opens. A reservation precedes a next-minute fill. The 10%-of-stop-distance entry band, friction veto, original reserved-risk cap, $1 minimum, $10 capital, $0.15 planned trade risk, $30 exposure, $1.50 gross-loss allowance, five attempts and $20 reserve are unchanged. Profits never replenish the allowance. Price jumps may exceed a planned loss despite conservative modeling.
Replay chronology was corrected: opening exits, pending fills and reservations precede intrabar exits. A future intrabar exit cannot release cash at that minute’s open. Stop-first ambiguity and unfavorable gaps apply. Barrier time is an interval, realized at its upper endpoint. Gross same-fill attribution now avoids double-counting spread at bid-side barriers. These corrections preserve all primary historical P&L/counts in this study.
Historical bars cannot certify NBBO, quote freshness, microsecond timing, actual latency or queue partial fills. Missing input sessions are excluded; synthetic fresh quotes are declared assumptions. Production freshness checks are unchanged and separately covered by the existing tests.

## K. Chronological validation methodology

Train: 2022-09-30–2023-03-31. Validation: 2023-04-01–2023-06-30. Reused replication test: 2023-07-01–2023-09-29. Endpoints are end-exclusive in the protocol. No shuffle, fitted final-period normalization or parameter selection from the last period. Exact rules, costs and gates were frozen before return calculation.
The family correction was made more conservative before results: 100-hypothesis budget covers 14 bases, 42 exit alternatives, seven old-family variants and 14 threshold alternatives. The existing gates remain: positive validation/test/stress expectancy, positive corrected uncertainty lower bound, best-day independence, drawdown ≤$1.50, at least two-thirds positive forward blocks, ≥75% nonnegative validation sensitivity cases with trades, and ≤50% expectancy degradation. Independent untouched data, live feature parity and shadow evidence remain mandatory for live eligibility.
Twenty active days is only the existing minimum for reporting the gate bootstrap, not a universal adequate sample. No family reaches it here. The 5,000-replicate extreme family-adjusted tail would itself need more numerical precision before relying on a marginal future pass; none is claimed.

## L. Walk-forward results

Six monthly forward blocks April–September use fixed parameters and expanding prior-session history, with no future normalization. Every base family has 0/6 positive net blocks; most blocks contain no trades. This is a fixed-rule forward replication, not a successful adaptive walk-forward strategy. Monthly counts and metrics are retained, so zero observations cannot be mistaken for zero-risk evidence.

## M. Robustness and sensitivity

New-family threshold multipliers 0.8/1.2 and three exit alternatives were evaluated only on development/validation data. No broad stable profitable region is established. All wider-cost/delay/partial scenarios and SPY-only/QQQ-only, earlier/later, volatility and gap breakdowns are retained. The recent sample adds one ORB fill and no other family fills; it cannot establish robustness.

| Family | Base fills | Base net expectancy $ | Gross same-fill expectancy $ | Zero-cost fills | Zero-cost P&L $ |
|---|---|---|---|---|---|
| orb | 5 | -0.006105 | -0.003115 | 10 | -0.034740 |
| filtered_orb | 1 | -0.037070 | -0.034085 | 2 | -0.050823 |
| orb_retest | 0 | — | — | 0 | 0.000000 |
| vwap_trend | 1 | -0.068154 | -0.065173 | 6 | 0.094200 |
| vwap_reversion | 0 | — | — | 5 | 0.040075 |
| gap_continuation | 0 | — | — | 1 | -0.017357 |
| gap_fade | 0 | — | — | 0 | 0.000000 |
| opening_drive | 0 | — | — | 0 | 0.000000 |
| failed_breakout | 0 | — | — | 4 | -0.058802 |
| vwap_reclaim | 3 | -0.030022 | -0.027036 | 15 | 0.134282 |
| trend_pullback | 0 | — | — | 8 | -0.009550 |
| range_breakout | 0 | — | — | 8 | 0.063352 |
| momentum_continuation | 0 | — | — | 0 | 0.000000 |
| exhaustion_reversion | 0 | — | — | 1 | 0.012201 |

Positive zero-cost results for some families vanish or lack fills under realistic assumptions. Different costs also change eligibility/fill cohorts: this establishes cost/selection sensitivity, not a cost-only causal explanation. A single favorable stress trade cannot establish robustness. Parameter fragility, overfitting and favorable regimes remain unproven rather than assigned unsupported labels.

## N. Rejections and precise failure attribution

| Family | Unique setups | Reserved | Filled | Diagnostic categories | Failed acceptance checks |
|---|---|---|---|---|---|
| orb | 500 | 86 | 5 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| filtered_orb | 30 | 5 | 1 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED, FILTER_ATTRITION | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| orb_retest | 98 | 2 | 0 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| vwap_trend | 140 | 4 | 1 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED, COST_AND_SELECTION_SENSITIVE | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| vwap_reversion | 431 | 1 | 0 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED, COST_AND_SELECTION_SENSITIVE | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| gap_continuation | 295 | 12 | 0 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| gap_fade | 51 | 3 | 0 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| opening_drive | 6 | 0 | 0 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED, CAPABILITY_LIMITED | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| failed_breakout | 110 | 10 | 0 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| vwap_reclaim | 320 | 41 | 3 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED, COST_AND_SELECTION_SENSITIVE | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| trend_pullback | 339 | 3 | 0 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| range_breakout | 193 | 10 | 0 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED, COST_AND_SELECTION_SENSITIVE | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| momentum_continuation | 17 | 0 | 0 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |
| exhaustion_reversion | 61 | 0 | 0 | LOW_SAMPLE, DATA_LIMITED, EXECUTION_CONSTRAINED, COST_AND_SELECTION_SENSITIVE | positive_validation, positive_test, positive_stress, uncertainty_lower_positive, outlier_independence, period_stability, parameter_stability, degradation, untouched_test, live_feature_parity, shadow_execution_evidence |

LOW_SAMPLE means insufficient executed observations/active days for the declared evidence gates. DATA_LIMITED means reused narrow history, missing quote/tick evidence and uncertified live history parity. EXECUTION_CONSTRAINED means signal-to-reservation-to-fill attrition is directly observed. COST_AND_SELECTION_SENSITIVE means positive zero-cost replay fails to carry over, with cohorts explicitly allowed to differ. FILTER_ATTRITION and CAPABILITY_LIMITED describe measured selectivity and an unsupported early execution window, respectively. None proves a population NEGATIVE_EDGE or OVERFIT diagnosis.

## O. Shadow eligibility

None. No research family is inserted into the production shadow registry or enabled. Existing isolated shadow infrastructure remains available for a genuinely gate-passing implementation. No prospective shadow sample was fabricated.

## P. Live eligibility

None. Every new family is implemented for offline research; none is validated, LIVE_ELIGIBLE or LIVE_ENABLED. No activation command is proposed. The approval boundary remains required before enabling any future qualifying strategy.

## Q. Production integration

Changes are additive research/diagnostic modules, tests and documentation. Existing strategy definitions, strategy registry, broker/reconciliation/recovery, KILL, clock/data freshness checks, SQLite schema, live configuration and risk limits are unchanged. No restart, migration or experimental order was performed. New research IDs are explicitly tested to be rejected by the production registry. Private runtime preservation and final reconciliation evidence are stored only in ignored local reports.

## R. Tests

Baseline: 677 passed. Added: 38 tests. Complete final suite: 715 passed, zero failures. Coverage includes each new family’s positive/negative/timeout/enablement boundary; point-in-time features, DST/early close, train-only bins, uncertainty; reserved-risk/duplicate/no-fill behavior; stop-first ambiguity and interval time; same-minute capital chronology; and deterministic event statistics. Existing broker failure injection, atomic reservations, recovery, shadow isolation and live enablement tests remain in the full suite. No tests submit broker orders.

## S. Version and reproducibility

Branch: `research/strategy-phase-2`. Diagnosis/protocol milestone: `cb9f11539cecd078be17401eab65aac44c830482`. Initial framework milestone: `d923d578490e430b22b8c53f25807a3e4c4c2150`. Final packaging commit is the commit containing this report; planned release tag is `v0.3-research-phase2`. Existing v0.1 and v0.2 tags are preserved. PHASE2_VERSION.json records run provenance, source digests, Python/dependencies and all strategy fingerprints. It identifies research source, not the running production process.

```sh
.venv/bin/python -m trader.multistrategy.diagnose
.venv/bin/python -m trader.research2.study
.venv/bin/python -m trader.research2.supplement
.venv/bin/python -m trader.research2.recent
.venv/bin/python -m pytest -q
```

The recent runner requires its frozen protocol and locally downloaded vendor sample. Original and diagnostic-refined runs are preserved separately. Raw data, full trade/candidate books, run source snapshots and test logs remain local under ignored directories. Committed aggregate research artifacts contain no account data, credentials, private logs or production state.

## T. Remaining research questions

1. Acquire independently licensed, previously uninspected multi-year data with reliable historical NBBO or finer execution observations, then freeze a truly untouched evaluation. The data acquisition/coverage problem is not solved by reusing this sample.
2. Quantify live quote-cadence fill feasibility within the unchanged band and reserved-risk checks. Do not loosen those checks to compensate for minute-bar uncertainty.
3. Examine whether any causal entry response survives costs and independent periods before researching more filters. Exit alternatives cannot rescue nonexistent executable sample evidence.
4. Certify prior-session live history and feature parity before promoting any history-dependent family. Early entries and short execution require separate architecture/safety research and approval; they are not silently added here.
5. Require prospective shadow and execution evidence if a future strategy passes statistical gates. Sparse low correlations and isolated winners are not diversification or edge evidence.

This phase produces testable hypotheses, an auditable diagnosis and corrected offline measurement. It does not establish a profitable strategy.
