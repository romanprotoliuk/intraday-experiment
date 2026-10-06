# Host certification and unattended operation

This is an engineering candidate, not evidence of deployment or authorization.
No strategy or risk configuration is changed. A host certificate blocks entries;
it does not grant permission or disable existing protective exit management.

## Timestamp race

The old doctor captured a UTC wall-clock timestamp before calendar/database work,
then used that timestamp to validate a file published asynchronously by the host
monitor. An observation created after the initial timestamp was rejected as
future-dated even when it was current at the time of reading. The same ordering
existed in persistent host-gate callers and status reporting.

Read the evidence first, then sample the validation clock. Injected engine clocks
are passed as callables, preserving deterministic tests. A genuinely future or
expired timestamp still fails; no negative-age allowance is introduced. The
explicit-datetime API remains available for deterministic snapshot tests.
UTC-aware timestamp subtraction, not local timezone display, determines age.
The timestamp race is reproduced by a synthetic file-publication regression;
that does not establish the exact age of a historical overwritten observation.

## Suspension and clock continuity

Each collector maintains a wall/monotonic pair and continuity generation.
A gap exceeding the existing host freshness deadline, negative elapsed time, or
wall-versus-monotonic discrepancy exceeding the existing clock bound invalidates
cached clock evidence. The next observation requires a new read-only NTP sample.
Collection interrupted by a discontinuity cannot publish a healthy certificate.
Generation matching prevents a sample from before a discontinuity being reused.

The host timestamp is the collection **start**, so command latency cannot make
old evidence appear younger. NTP timestamps likewise represent request start.
Power/lid/assertion commands are bounded by the existing subprocess timeout;
NTP retains its existing command timeout. A collector restart begins with no
clock sample. Supervisor identity still must match. Readers additionally compare
wall and monotonic elapsed time to reject cached evidence after a clock change.
Neither a freshness threshold nor the configured combined clock bound increases.

Finite sampling cannot guarantee continuous physical availability between
observations. Monotonic behavior across sleep is platform-dependent; the wall-gap
check is also retained. No attempt is made to suppress lid, thermal, forced-sleep,
shutdown, reboot, logout, power-loss or other macOS safety behavior.

## Supported power requirements

The supported mechanism remains `/usr/bin/caffeinate -is -w <supervisor-pid>`.
The installed macOS `caffeinate(8)` manual documents that `-i` prevents idle sleep
and `-s` prevents system sleep only on AC power. A running process alone does not
prove either assertion is effective. Battery charge is not an AC substitute.

When sleep prevention is required, readiness now requires AC power even if the
separate legacy `require_ac_power` setting is false. This strengthens the host
gate without changing the configuration file or financial risk envelope. The
existing minimum battery and lid-open requirements remain independently enforced.

The supervisor publishes its inhibitor PID. The collector reads `pmset -g
assertions` and requires both PreventSystemSleep and PreventUserIdleSystemSleep
from that exact caffeinate PID, with the expected supervisor as owner. Assertions
from unrelated applications and system-wide totals are insufficient. Missing,
unparseable or stale evidence fails closed. Existing legacy supervisor evidence
without inhibitor identity cannot satisfy this new proof.

Leave the computer on AC, lid open, logged in, with reliable networking. The
LaunchAgent owns the supervisor, worker, collector and inhibitor independently
of a shell or coding-agent session. No permanent macOS power settings or unsupported
sleep override are introduced. Apple's supported power settings are documented at
https://support.apple.com/guide/mac-help/mchle41a6ccd/mac.

## Operator and session boundaries

Unknown and legacy KILL records remain operator-controlled. The existing operator
transition requires the regular entry window, current opening certification,
fresh production authentication/reconciliation, clock/host/data health and its
other existing gates. It cannot legitimately be used after close or before the
opening range merely because the account is flat. This change adds no after-hours
rearm bypass and never removes KILL.

The normal calendar/session-roll implementation remains unchanged: a new session
gets its own loss budget, attempt count, recovery and opening-health state. Prior
session MISSED candidates, events, orders and fills are not reset or rewritten.
After-hours data health is pending a new regular session, never fabricated.

## Tests and deployment boundary

Synthetic tests cover publication races, true stale/future evidence, timezone
equivalence, supervisor restart identity, wall-clock steps, sleep before/during
collection, cached-clock invalidation, failed fresh NTP, assertion ownership,
missing assertions, AC requirements and recovery after a new measurement.
Existing entry/exit, operator-hold, ambiguity, setup and risk regressions still
apply. Hardware sleep behavior itself is not induced during production operation.

Controlled deployment and operator activation are separate steps. A passing
suite does not establish current broker authentication, actual deployment,
next-session opening health or continuous unattended availability.
