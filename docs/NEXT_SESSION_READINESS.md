# Next-session control and authentication candidate

This document describes software behavior, not a production deployment or a live
enablement decision. A passing test suite does not establish broker execution
evidence. Deployment, credential verification in the service context, and any
operator acknowledgment are separate operational acts.

## Readiness is not entry permission

`python -m trader readiness next-session` performs broker reads plus local
inspection. `--no-broker` skips broker access and cannot return live-ready. It
does not create a missing runtime or database, set LIVE, remove controls, reset a
session, or submit/cancel an order. Supported OAuth maintenance may update the
protected credential store while establishing the read-only connection.

The output separates `INFRASTRUCTURE_READY`, `MONITORING_READY`, and
`UNATTENDED_LIVE_READY`. Monitoring requires functioning infrastructure and fresh
worker authentication plus independent expected-account broker reconciliation.
Live readiness additionally requires flat broker/local state, coherent settings,
stable ownership, a quiet recovery period, existing LIVE permission, no unresolved
operator controls, valid next-session initial state, and demonstrated unattended
refresh for the stored credential. A refresh token's existence alone is not
proof. A current explicit/legacy hold keeps live readiness false.

All three values describe a snapshot. They are not promises about future network,
broker, token-revocation or host conditions. Entry permission still comes only
from the existing runtime gates at the actual decision timestamp. Outside the
entry window it remains false even if next-session readiness is true.

The production calendar selects the next future opening (today if still before
open), including holidays and early closes. No session is created or reset by
this command. Existing `Ledger.ensure_session` supplies the fresh loss budget,
attempt counter, opening/data/recovery state and latches, preserving prior rows.

## Hold classes and control-plane separation

| Class | Interpretation | Clearance |
|---|---|---|
| A | Operator, unknown/malformed legacy, corruption, identity/exposure/mutation uncertainty, exhausted recovery budget | Operator investigation; never inferred benign from text |
| B | Exact ledger-matched structured system hold meeting the existing automatic-candidate predicates | Existing bounded positive recovery proof; no broadened automatic classes |
| C | Explicitly investigated legacy worker incident bound to immutable ledger evidence and the exact current hold instance | Separate outside-session operator acknowledgment with fresh flat-account proof |

The earlier deferred-authorization candidate preserved all entry-time checks but
kept historical incident acknowledgment tied to the opening window and introduced
a pending authorization lifecycle. This candidate branches from the deployed
baseline instead. It does not install the deferred evaluator or carry an operator
authorization into market hours.

Class C is never assigned by matching the reason string alone. `legacy-incident
inspect` returns the current file identity. `legacy-incident review` requires an
explicit completed-investigation attestation, specific retained event IDs, and
that inspected identity. The resulting protected-runtime record binds inode,
timestamps, content hash, evidence hash, salted account identity, strategy/config
fingerprints, event watermark and expiration at the next market open. Review
alone changes no control. This is a human investigation attestation, not an
automatic forensic classifier or cryptographic proof of historical causation.

`legacy-incident acknowledge` requires that exact review ID and explicit operator
acknowledgment, outside regular market hours. It preserves existing LIVE
permission (including false), session state, candidates, loss/attempt accounting,
strategy and risk parameters. It requires two independent, read-only full broker
snapshots, exact account identity, flat broker/local state, intact existing schema,
current config/strategy identity, five-minute worker and quiet-period stability,
fresh worker authentication, actual process ownership, LaunchAgent, heartbeat,
host/clock/AC/sleep certification, and no conflicting operator controls.

The read-only inspection interfaces are available without enabling this path:

```
python -m trader legacy-incident inspect
python -m trader readiness next-session --no-broker
python -m trader session-forensics --date YYYY-MM-DD
```

Operator review/acknowledgment arguments are exposed by `legacy-incident --help`.
Do not reuse an old inspected identity. No review is created during installation,
startup or ordinary recovery. No acknowledgment is scheduled or performed by the
worker. Unknown legacy provenance remains class A until explicitly reviewed.

Both snapshots run with KILL still present. A durable TRANSITION entry barrier is
installed first; short hold locks serialize against supported KILL writers. The
matching acknowledgment/proof is committed before removing only the matching
hold. TRANSITION is removed last. A crash or failure leaves either KILL or
TRANSITION blocking entries. Consumed reviews cannot replay. Filesystem and
SQLite changes are not claimed to be one atomic transaction: safety depends on
the durable barrier and ordered writes. Interrupted transitions require review.

The recovery opener uses `Ledger.existing`, which never initializes tables,
defaults, or missing files. It rejects missing schema and state instead of
fabricating a clean database. Config changes, new failure episodes, hard health
failures, same-text hold replacement, and review expiry invalidate the binding.

## Authentication

The installed MCP SDK loads persisted tokens without restoring the absolute
expiry, and its 401 path can enter interactive authorization. The candidate
noninteractive adapter uses the pinned MCP 1.30.0 discovery, resource/issuer
validation and refresh-request construction. The project metadata now pins the
same version as the existing lock file; future SDK upgrades require rerunning
the adapter integration tests.

Protected token storage atomically persists the absolute expiry together with
the token and a digest binding. A process restart never restarts a relative token
lifetime. Legacy storage without trustworthy absolute timing triggers a supported
refresh; it is not silently assumed unexpired. A 60-second pre-expiry margin
exceeds the default initialization timeout plus two bounded broker requests.
Unknown lifetime never becomes a readiness PASS merely because a JWT can be
decoded. Refresh endpoint selection uses validated metadata and HTTPS.

Refresh is serialized across processes with a bounded lock. A durable in-flight
record is saved before sending the refresh POST, because the server may rotate a
credential even if the response is lost. Unknown outcome, invalid response,
rejected refresh, or a second 401 blocks further refresh until credential repair.
No transparent auth-endpoint redirects or unbounded 401 loops are allowed. If a
successful response omits a replacement refresh token, the existing one is
retained, consistent with the OAuth refresh response semantics.

After refresh, only a fresh MCP initialization can continue. An established
connection is closed so the normal Coordinator account-pinning/reconciliation
sequence runs again. Broker mutations are never replayed after 401; mutation
ambiguity continues through the existing durable order reconciliation path.
Interactive authorization remains an explicitly invoked command and is never
opened by the service. No response body, credential or auth header is logged.

These choices retain issuer/resource validation and confidential token handling
required by the [MCP authorization specification](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-11-25/basic/authorization.mdx).

## Evidence and limitations

`session-forensics` reads a consistent SQLite snapshot, records a ledger event
watermark and content digest, and labels an ongoing day INTERIM. Candidate events
record the first encountered rejection, not every independent risk gate. A sized
candidate rejected at KILL proves sizing passed; it does not prove later review,
execution feasibility, a fill, or P&L. Observation timestamps do not establish exact
exchange crossing times. No counterfactual backtest or parameter tuning is run.

Host continuity, AC requirement, actual owned macOS sleep assertions, clock limits,
ORB, sizing, ordinary risk/entry/exits, session rollover, structured recovery and
position recovery remain unchanged. No code guarantees a trade or availability.

The following coverage includes existing regressions plus the new tests:

| Failure/behavior | Evidence |
|---|---|
| Host suspension, clock jumps, stale/future evidence, AC removal, missing/wrong-owner assertions | `test_host_continuity.py`, `test_host_health.py` |
| Watchdog/read transport/HTTP2/DNS failures, unknown crash, positions, ambiguity, budgets, generation changes, cutoff/EOD | `test_system_recovery.py`, `test_position_recovery.py` |
| Outside-session legacy acknowledgment, exact replacement races, clock/host/auth/schema/account failures, other controls | `test_next_session_readiness.py` |
| Restart without entry permission, next-session counters, preserved history, trigger/invalidation remains MISSED | `test_next_session_readiness.py`, `test_persistent.py` |
| 401/expiry/refresh, invalid response, secure storage, concurrency, issuer mismatch, no mutation replay | `test_oauth_refresh.py` |
| Real SDK initialized twice after synthetic refresh, tool discovery and clean transport shutdown | `test_connection.py` with mocked HTTP only |
| Partial fills, ambiguous buy/sell/cancel, per-symbol concurrency, risk limits and protective management | existing persistent/position recovery suites |

Actual production refresh, LaunchAgent restart, host sleep, Terminal closure and
Codex closure are not performed by deterministic unit tests. Those must not be
reported as passed live experiments. The installed service's existing independence
can be inspected read-only; it does not establish deployment of this candidate.
