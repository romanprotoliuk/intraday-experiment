# Phase-two hypotheses and boundaries

Frozen before phase-two return calculations. These are research questions, not
validated improvements. Original seven strategy classes and parameters remain
unchanged. Each default new family uses a completed five-minute signal candle,
high + $0.01 trigger, low − $0.01 stop, 2R target and 30-minute hold. Production
entry-band, friction, sizing, reservation and loss limits apply in guarded replay.
Regime v2 eligibility is part of each new hypothesis, not an after-the-fact filter.

| Family | Hypothesis and exact extra conditions |
|---|---|
| Opening drive | At minute 10 or 15, displacement from open ≥ mean last six available candle ranges; TOD RVOL ≥1.2; range expansion ≥1; both assets above VWAP; candle body ≥60%. One daily anchor. Minute-10 opportunities remain incompatible with the current production entry window. |
| Failed breakout | Long failed downside OR break: first completed close below OR low minus offset, then within six bars a positive close back above OR low but below OR high; retest low ≤ OR low +0.2 short-range scale. One anchor per first failed breakout. No below-VWAP exit. |
| VWAP reclaim | Completed reclaim from below to above VWAP, positive five-minute momentum, body ≥50%, TOD RVOL ≥1. Bearish VWAP loss is measured separately as a non-executable event response. |
| Trend pullback | Positive six-bar moving-average slope; low touches SMA6 +0.1 ATR and close finishes above it with positive candle; extension above VWAP ≤1.5 ATR; current/previous volume ≥1. |
| Later range breakout | At/after minute 90, previous six-bar high−low ≤60% of previous twelve-bar range; current close above previous six-bar high; TOD RVOL ≥1.2 and body ≥60%. Both ranges exclude the current signal candle. |
| Momentum continuation | Three positive candles; 15-minute return × price / ATR ≥1; range expansion ≥1.2; TOD RVOL ≥1.2; extension above VWAP ≤2 ATR. |
| Exhaustion reversion | Range/mean-reversion regime; price ≤−2 trailing-close standard deviations from VWAP; positive momentum acceleration, declining volume, positive candle and lower wick ≥25%; VWAP must permit 2R. No below-VWAP exit. |

One-at-a-time threshold sensitivity uses 0.8 and 1.2 times the specified
displacement, retest distance, body, pullback tolerance, compression, normalized
momentum or deviation threshold respectively. No combinations are searched.

Old-family variants: ORB OR-width/prior-daily-ATR in [0.1,0.5]; filtered ORB keeps
only above-VWAP, positive slope and RVOL ≥1; retest adds above-VWAP and declining
volume; VWAP trend caps extension at one ATR; mean reversion tightens deviation
to −2.5 standard deviations; gap continuation permits at most 25% fill; gap fade
requires body ≥50%. These evaluate one reasoned question each. Filter removal is
an ablation, not permission to weaken any execution or acceptance gate.

Independent exit comparisons: 1.5R/30m; 2R/20m; structural reward. Structural
reward uses prior close for gap fade, OR midpoint for failed breakout, VWAP for
reversion, otherwise 1.5 current ATR. Unavailable or <1R structural room rejects
the signal. The proposed structural reward becomes a frozen R ratio; the replay
reanchors that ratio to the eventual fill like the existing engine. It is not an
exact immutable VWAP/level limit order. Entry eligibility and exits may alter the
filled population, so comparisons are not automatically matched-entry effects.
No trailing or partial-exit execution is introduced without supporting evidence
and certified execution semantics.

New regimes require positive evidence of trend, range, opening momentum or gap
behavior; volatility alone cannot establish eligibility. Trend requires agreement
of price/VWAP, 15-minute VWAP slope and momentum. Range requires ≥2 VWAP crosses
and absolute slope ≤0.25 ATR without directional agreement. Opening momentum
requires minutes 10–30, price/VWAP and five-minute momentum agreement and ≥50%
body. Material gap is ≥0.1 prior ATR; continuing gaps remain open and extend;
failure means at least 50% filled. High/low volatility uses prior comparable-time
80th/20th percentile. UNCERTAIN always blocks new research-family candidates.
The exact eligibility sets and reasons are in `trader/research2/regimes.py`.

Signal event responses use next-minute mid opens at 5/15/30-minute horizons.
They ignore trigger fills, reservations and overlapping exposure, and therefore
cannot count as executable returns or gate evidence. Both directions can be
studied without suggesting short broker support. OR crossings include a causal
first/later count. Feature terciles use training observations only, never later
periods. Prior-day range requires the actual immediately preceding session.
Premarket range remains unavailable: off-hours sample coverage is not certified.

Replay v2 orders completed-data evaluation, opening exits, pending fills, new
reservations, intrabar protective exits and close marks. An intrabar exit cannot
release capital retrospectively at that minute's open. Unknown barrier time is
recorded as a minute interval. Stop-first ambiguity, worse stop-gap fills and
declared synthetic costs remain conservative assumptions, not historical quotes.

All results remain replication on previously inspected data. No existing data
split is relabeled untouched. All variants/exit selection exclude the last
chronological period. No strategy can become live eligible without independent
new evidence, live feature parity, prospective shadow observations and approval.
