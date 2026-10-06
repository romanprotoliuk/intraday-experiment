# Session-scoped deferred operator recovery candidate

This document describes an inactive engineering candidate. Deployment and explicit
operator authorization are separate actions. Importing, starting or deploying the
code never creates a receipt. No pending receipt is shipped with the source.

## Existing architecture and extension

The existing doctor performs broker reads and readiness checks. Its separately
invoked operator transition also checks worker stability, repeats reconciliation,
uses configuration and system-hold locks, and keeps a durable TRANSITION marker.
Structured system recovery distinguishes proven system holds from operator and
unknown holds. There was no durable representation of future operator intent.

The extension stores one receipt under `operator_recovery_authorization` in the
existing ledger's key/value table, with immutable lifecycle events in its audit
table. No schema migration, separate scheduler, broker executor, risk engine or
strategy state is introduced. The worker checks for a receipt within its normal
cycle, after priority position management. No receipt means no recovery action.

The deferred path invokes the same doctor and operator transition as immediate
operator recovery. It does not replace proof with a boolean authorization flag.
It requires the existing persistent LIVE preference to be present and leaves it
unchanged on success. It removes no unrelated control. The existing conservative
operator-transition failure policy remains: durable blocks and disarm for review.

## Authority binding

Only the exact reviewed legacy worker-exit/reconciliation reason is supported.
An authorization binds:

- a unique receipt identity, creation time and current exchange-session date;
- the target file's content hash, device, inode, change time, modification time
  and size, read without following symlinks; unstable or nonregular files fail;
- canonical configuration and strategy fingerprints and configuration-file
  identity, so change-and-restore does not silently preserve authority;
- a salted runtime-only account binding to the existing durable account;
- the creation worker generation for audit and an event-ledger watermark.

A same-text replacement is a new hold and cannot consume the old authorization.
An unrelated structured or explicit operator hold never qualifies. Supported KILL
writers share the existing hold lock. External direct filesystem manipulation is
not a supported control API and cannot be made transactionally atomic by SQLite.

The receipt expires at the exchange-calendar new-entry cutoff, which precedes
session close. It cannot cross into another session. New hard failures, another
failure episode, exhausted recovery budget, changed account/configuration,
uncertain positions/orders or another operator control invalidate it. A controlled
worker restart can preserve it only while the target hold remains unchanged;
current generation and the existing healthy-worker duration must pass again.

Invalidated, expired or consumed receipts are never automatically reissued.
Creation refuses a second receipt in the same session. Missing databases are not
initialized to create an authorization, and corrupt databases are not repaired.

## Proof and execution

Authorization without complete readiness leaves KILL active. Before the entry
window or opening certification, no transition is attempted. The existing host
gate is checked before expensive certification. Failed proof cannot become PASS
because a receipt exists.

The existing operator transition makes three independent read-only broker/doctor
certifications. For a deferred transition, KILL remains throughout all three.
The returned checks must be complete and individually true. Immediately before
commit, the path additionally rechecks current session capacity, generation,
control identity, host/clock evidence, broker age, quote age and the latest
required completed-bar boundary. Expected broker identity is checked against
the durable account; derived VWAP must be present.

The established worker stability and connectivity quiet-period checks remain.
Polling uses the existing recovery cadence and the remaining worker-cycle time
budget; it never widens the watchdog or market-data deadlines. Slow certification
can therefore fail closed. No execution-latency claim is inferred from fake-broker
tests, and no recovery timing guarantees a trade opportunity.

## Interruption and concurrency

Lock order remains configuration lock, then hold lock, then SQLite transaction.
No hold lock is retained across network awaits. Final comparisons occur after
asynchronous reads. The receipt must still exactly match durable PENDING state.

The filesystem marker and SQLite cannot participate in one native transaction.
Instead the protocol retains TRANSITION as an entry barrier, commits CONSUMED and
recovery proof before unlinking the matching KILL, verifies removal, appends the
clear event and removes its own TRANSITION last. Failure retains/reinstates the
reviewed KILL without overwriting a replacement, and retains fail-closed controls.
A receipt committed CONSUMED stays spent even if later unlink fails. An interrupted
transition is not automatically resumed by reusing that receipt; it requires
operator investigation. Temporary readiness failure before transition starts
leaves the receipt pending; hard failures invalidate it.

## Preserved trading behavior

No ORB calculations, sizing, limits, order submission, fill handling, session roll,
cutoff, liquidation or position-exit semantics are changed. Recovery itself never
submits or cancels an order. It cannot rearm MISSED or consumed setups. A setup
that becomes MISSED during certification remains MISSED on normal evaluation.

## Operator surface and validation scope

The `production authorize-recovery` action requires an explicit session,
`--recover-kill` and `--acknowledge-risk`. That action records intent only; it does
not remove KILL or set LIVE. Runtime status exposes receipt status and expiry.
This document is not an instruction to activate production.

Tests use temporary ledgers, synthetic identities/times and a fake MCP connection
with the actual doctor and transition. They cover authorization versus readiness,
expiry, replacement races, hard failures, generation checks, completed-bar and
MISSED semantics, interrupted storage/unlink, retained barriers and single use.
Production readiness and creation of an actual authorization are not established
by these offline tests.
