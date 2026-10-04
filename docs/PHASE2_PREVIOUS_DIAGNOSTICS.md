# Previous-strategy diagnosis (rules unchanged)

These are sparse realized replay samples, not population edge estimates. All seven are LOW_SAMPLE, DATA_LIMITED and EXECUTION_CONSTRAINED. No strategy passes the original gates.

| Strategy | Trades | Net expectancy $ | Gross same-fill expectancy $ | Primary finding |
|---|---:|---:|---:|---|
| orb | 5 | -0.00610485 | -0.00311481 | Only five fills; negative aggregate but positive training. Entry-band and pending-price checks dominate. Negative population edge is not established. |
| filtered_orb | 1 | -0.03707014 | -0.03408480 | One losing fill, shared with ORB; signal evaluations fall from 1018 to 36. Filter attrition is measurable; superiority or excessive filtering is not statistically established. |
| orb_retest | 0 | unavailable | unavailable | 102 signal evaluations but zero fills. Neither retest expectancy nor exit quality can be estimated. |
| vwap_trend | 1 | -0.06815401 | -0.06517332 | 140 signal evaluations and one losing training fill. Intraday ATR warm-up delays availability until 70 minutes after open. |
| vwap_reversion | 0 | unavailable | unavailable | 431 signal evaluations but zero fills. Close-standard-deviation warm-up requires 100 minutes; entry-band/friction rejection and expiry prevent a return sample. |
| gap_continuation | 0 | unavailable | unavailable | 295 signal evaluations but zero fills. Positive gap/history/volume/OR requirements plus entry-band and delayed-fill rejection prevent a return sample. |
| gap_fade | 0 | unavailable | unavailable | 51 signal evaluations but zero fills. Negative gap, reclaim, volume and remaining 2R gap room create a sparse joint condition; no exit inference is possible. |

## Interpretation

Negative realized P&L does not prove NEGATIVE_EDGE. Zero fills does not prove no signal edge. COST_SENSITIVE, REGIME_DEPENDENT, OVERFIT and PARAMETER_FRAGILE need more observations than these results provide. Missing or zero-trade subgroups remain unavailable.

The JSON companion contains all requested estimable statistics, period/symbol/regime/time/volatility/gap/exit breakdowns, prior sensitivity results, counts and provenance. Sharpe is withheld for sparse dependent outcomes. Untouched test performance is unavailable.

## Implementation limitations

- Five-minute finalized signals observed at minute opens can miss a narrow 10%-of-stop-distance entry band.
- After initial reservation, a mandatory next-minute fill faces expiry and unchanged reserved-risk veto.
- VWAP exit is correctly disabled for mean-reversion and gap-fade signals; the common sizing function does not impose an above-VWAP entry requirement.
- Original independent replay logs regimes but does not apply live regime eligibility.
- Original estimated exit costs undo spread for bid-side barrier fills; corrected same-fill attribution is reported here without rewriting old results.
- Intrabar barrier exits were timestamped at bar start; exact exit time is unidentified within that minute.
- No previously unused local multi-year minute dataset was found; one inspected year cannot provide independent final-test evidence.
