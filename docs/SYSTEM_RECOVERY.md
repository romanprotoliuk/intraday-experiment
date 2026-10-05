# Structured system-hold recovery candidate

This engineering change requires controlled deployment and operational review.
Passing the isolated suite does not certify live execution. No strategy, capital,
loss limit, quote age, clock bound, heartbeat interval or position exit policy is
changed. Existing legacy KILL markers are deliberately not migrated or removed.

## Failure model

A remote read failure normally enters the existing bounded reconnect/backoff path;
it is not evidence that the Python worker exited. A supervisor-observed stale
heartbeat can lead to SIGKILL. A host suspension can age both the heartbeat and
the supervisor loop together, so a stale heartbeat alone cannot establish an
uncaught exception. Record heartbeat age, supervisor loop gap, worker generation,
exit code and any available sanitized exception chain. Host suspension itself
cannot be prevented or made harmless by application recovery.

The old design writes free-text KILL for a watchdog stall, then overwrites it with
worker-exit text. Healthy broker/data recovery never removes KILL. Its operator
transition requires the entry window and opening certification; flat premarket
reconciliation alone is insufficient. Rolling restart limits allow repeated
failures outside the rolling window to continue indefinitely.

## Proposed state transitions

Known system event -> RECOVERY_PENDING -> fresh full reconciliation and complete
certification -> RECOVERY_PROVEN -> remove only the matching system hold.
Any independent entry veto remains authoritative after hold removal.

A new worker has a UUID generation shared with the supervising process. PID alone
is insufficient. Structured KILL is paired with its durable ledger record. Legacy,
malformed, unpaired, operator and unknown provenance remain blocked. Writers use a
short KILL lock; no network operation occurs while it is held. Supported operator
STOP/entry-disable controls remain separate and are never removed automatically.

| Failure | Classification / disposition |
|---|---|
| Known read transport error | A: reconnect using existing backoff; no blind mutation retry |
| Structured watchdog stall while locally flat, no pending intent | B: potentially recoverable after complete fresh flat proof |
| Proven read-transport exit while flat | B: potentially recoverable; generic exit cannot infer this provenance |
| Operator KILL, STOP or entry disable | C: never auto-clear |
| Unknown/legacy KILL, unexplained exit, software exception | E/D: manual investigation; never inferred benign from text |
| Mutation ambiguity, ownership mismatch, unexpected position/order | D: sticky operator-required state |
| Authentication failure during system-hold certification | C: sticky operator-required; restored transport alone is insufficient |
| Database failure or missing schema | D: fail closed; never manufacture state |
| Market-data or clock failure | A only after fresh existing certificates pass; not waived |
| Worker loss with any local position/pending intent | C: retain hold and existing protective position recovery |
| Cycle deadline exceeded | C: performance investigation; not automatically deemed a transport read |
| Restart budget exhausted | C: no automatic entry recovery |
| Controlled shutdown | No automatic permission; normal supervisor shutdown does not invent a benign recovery proof |

A/B/C/D/E distinguish transient, proof-recoverable, operator-required,
investigation-required and unknown respectively. They do not confer order rights.

## Required proof

- Structured origin/episode exactly matches the ledger; no mutation, position or
  unresolved order at failure; still inside the cumulative failure budget.
- A new matching worker/supervisor generation; fresh heartbeat and supervisor
  evidence; healthy worker for at least the existing recovery duration.
- SQLite quick-check, canonical/applied configuration agreement and unchanged
  persistent LIVE preference. No activation or risk-setting mutation occurs here.
- No STOP, TRANSITION, LIQUIDATE, maintenance, hard read-only or entry-disable hold.
- Fresh full broker snapshot, not cached observation; expected authenticated
  account; positions exactly empty; all broker orders terminal; local positions
  and pending intents empty; existing ownership reconciliation succeeds.
- Fresh production clock/host certificate and fresh valid quotes for both symbols.
- Known current exchange session inside the existing entry window; current-session
  opening certification and cleared existing connectivity gates.
- Valid loss/attempt/cutoff state with remaining capacity. No budget is replenished.
- Completed bars and VWAP reconstructed through the existing ORB validator for
  both symbols. ARMED and MISSED are both valid *observations*, but only ARMED can
  subsequently reach normal strategy/risk checks.
- No intervening hard reconciliation/authentication/mutation failure in the durable
  episode history. This veto is sticky after later apparently healthy reads.
- All evidence rechecked after asynchronous reads and before matching-hold removal.

The clear operation writes proof before unlinking KILL. A crash before unlink
retains the hold; a crash afterward retains the proof and subsequent worker exit
is handled afresh. It never erases sessions, fills, orders, intents or setup history.

## Bounds and escalation

Use the existing five-minute healthy-worker/recovery duration and thirty-second
ordinary-recovery window for probe cadence. The current configuration remains the
source of these values; no new relaxed timing is introduced. Existing clock and
quote bounds apply unchanged.

Use the existing restart count as a *cumulative* automatic-recovery failure budget
as well as retaining the existing rolling watchdog throttle. The third distinct
failure under the default count escalates; elapsed time, midnight during an active
episode, and process restart cannot reset it. A flat exhausted account's supervisor
pauses worker starts. A deliberate operator disarm (LIVE false plus hard READ_ONLY)
permits observation-only diagnosis without erasing the hold/budget. Actual position
or intent recovery retains existing throttled monitoring attempts: stopping those
would abandon protective management. It never makes entries eligible automatically.

The matching hold can wait for regular-session evidence; lack of evidence does not
consume a synthetic retry count or authorize a clear. A cutoff/EOD or loss latch
prevents clearing for entry. A hard evidence failure escalates instead of retrying
into a benign classification.

## Preserved protections and limitations

Position management still precedes entry recovery and retains conservative verified
exit behavior. No shorting, averaging down, new strategy, retrospective ORB entry,
capital increase or new broker mutation path is added. Fully reconstructed MISSED
setups remain MISSED; consumed intent identity is unchanged.

Known read failures preserve diagnostic transport subtypes (GOAWAY/termination,
DNS when an actual resolver exception exists, timeout, HTTP status, protocol,
network, authentication, unknown). No claim of complete internet failure is made
from one broker error. The outage classifier now measures duration within the
current episode rather than inheriting an old completed episode's start time.

Synthetic tests cover safety gates, operator races, storage failures, both symbols,
partial/pending/ambiguous intents, held positions, cutoff/EOD, repeated restart,
missed/invalidation behavior and existing deterministic risk/exit paths. They do
not establish live reliability during host suspension or actual broker outages.

Operational rollout is separate: no running worker should load this candidate
until a qualified operator has reviewed the change, preserved the legacy hold,
verified broker/local state, and followed the existing controlled deployment
procedure. Unknown legacy provenance is not automatically converted into a new
recoverable record. The engineering branch neither releases that hold nor resets
an exhausted budget.
