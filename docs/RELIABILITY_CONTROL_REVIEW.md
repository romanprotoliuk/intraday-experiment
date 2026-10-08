# Reliability/control review

This review concerns infrastructure and control transitions. It changes no ORB,
calendar, sizing, exit, broker-mutation, account-pinning or clock threshold.
An engineering build is not deployment authorization and readiness is not entry
permission. All tests use synthetic brokers/data and temporary ledgers.

## States and transitions

The implementation uses several orthogonal states, not a single global enum.

| Actual state/control | Entered by | Supported exit/evidence | Sticky / persistence |
|---|---|---|---|
| STARTING / CONNECTING | New worker | Protected OAuth load/refresh, initialization, expected account pin | New generation; durable intents survive |
| POLLING / HEALTHY / ENTRIES_BLOCKED heartbeat | Normal worker loop | Next observed cycle; entry status is independently recomputed | Heartbeat expires; restart cannot reuse old ownership |
| DISCONNECTED / BACKOFF | Read/transport failure | Bounded delay, new MCP context, complete broker read and reconciliation | Unavailability blocks entry; no mutation replay |
| recovery_active | Startup, any health/data failure | Existing observation count and stability window with valid data | New session begins true; closed-market data cannot clear it |
| connectivity_halt | Repeated/prolonged session outage | Existing longer observation/time proof | Session scoped; observed recovery never erases failures |
| opening_healthy=false | New session/restart | Completed current-session range, fresh data/VWAP, host and ordinary recovery | Never imported across sessions/generations |
| structured RECOVERY_PENDING | Proven system incident while flat | Fresh full proof, distinct stable worker, healthy host/clock/data, active entry window | Durable; no position uncertainty or hard intervening failure |
| structured RECOVERY_PROVEN | All proof checks true | Audited matching-instance transition | Never itself supplies a signal or resets MISSED |
| OPERATOR_REQUIRED / hard KILL | Unknown crash, ambiguity, account/ownership failure, exhausted budget, explicit hold | Separate investigated, explicitly authorized operator workflow | Sticky across restart/rollover; connectivity alone cannot clear |
| investigated legacy Class C | Exact legacy hold plus valid explicit forensic receipt | Outside-session flat control proof and separate explicit acknowledgment | Review never clears KILL; unknown legacy remains Class A |
| invalid/expired Class C receipt | New event, identity/build change, expiry or consumption | New append-only investigation covering the original anchor through now | Old receipt immutable; unsupported/hard events prevent successor |
| MAINTENANCE / STOP / TRANSITION / LIQUIDATE / READ_ONLY | Supported operator/deployment/recovery controls | Corresponding supported workflow, never implicit entry recovery | Persistent controls; interrupted transition is intentionally sticky |
| entry_cutoff_latched / liquidation_due / closed | Exchange-calendar boundary checkpoint | New session only | Cannot be undone by clock rollback; old session remains immutable history |
| loss_latched / exhausted attempts | Actual gross losses / reserved attempts | New session with original policy | Profit, restart or config increase cannot replenish allowance |
| position REBUILDING / NORMAL_MANAGEMENT / VERIFIED_RISK_REDUCTION | Position recovery policy and current evidence | Complete ownership/quantity/market proof or verified protective action | Entry recovery cannot override position uncertainty; original deadlines retained |

Host sleep invalidates heartbeat/host/clock continuity; the watchdog may kill a
stalled worker. OAuth refresh changes credentials, not ownership proof: the
worker must reconnect and repin. Read failure invalidates broker truth; mutation
failure remains ambiguous until exact broker identifiers/history resolve it.
Neither refresh, host recovery, session rollover nor an expired receipt clears
an operator hold.

## Defects and conservative fixes

1. Preserving a legacy/operator KILL previously returned before recording its
   worker generation or enforcing cumulative restart exhaustion. The marker
   remains byte-for-byte unchanged, but preserved failure events now retain
   provenance. Read-only reconstruction includes earlier preserved stall/exit
   pairs and counts distinct worker generations, not the resulting SIGKILL twice.
   Midnight and supervisor restart cannot erase this budget. At the existing
   limit, flat restart remains operator-required. Existing position/order
   monitoring retains priority. Intentional disarmed READ_ONLY diagnosis remains
   the existing operator option; there is no new reset command or automatic
   acknowledgment. A completed independently authorized hold transition does not
   make a no-hold startup inherit an active historical failure episode.
2. One known transport leaf could hide an unrelated software exception in an
   ExceptionGroup. Every leaf/cause must now be recognized for read recovery;
   mixed unknown groups remain failed reconciliation. Direct DNS errors are
   recognized read-unavailability evidence; mutations always remain ambiguous.
3. Receipt expiration was incorrectly also a prohibition on *new investigation*.
   A predecessor may now be used as immutable archival evidence after expiry,
   never as current clearance authority. A new explicit investigation repeats the
   complete interval audit, two fresh flat control proofs and atomic identity
   checks, and appends a new receipt. No automatic renewal exists.
4. Controlled deployment changed the worker but had no admissible successor
   lifecycle. A complete proven flat stop/new supervisor/restart/new generation/
   prompt reconciliation sequence is now auditable. Missing steps, old generation,
   unexpected exposure, crashes, stalls and unknown events still reject it. A
   market-data read uses the same complete before/after broker proof as an account
   read; this cannot skip intent/mutation events. A worker lifecycle change
   invalidates the old review, including a legacy v1
   receipt. Current worker generation must match the reviewed startup.
5. One failed host proof prematurely stopped readiness collection, hiding other
   blockers. A host veto remains a veto while broker/control evidence continues
   to be collected. Future session validation now also checks net P&L, budget,
   completed count, fingerprints and stale certification counters.

## Expiration and event invalidation

The next market open remains the receipt's acknowledgment boundary: supported
legacy acknowledgment is outside regular session only and requires two fresh
current proofs. Historical facts do not become false at the open. Expiration
means the old receipt is not current authority, not that history is deleted or a
new investigation is forbidden. New receipts receive a new next-open deadline
only after full independent investigation. They cannot cover gaps, ignore new
safety events, change account/config/strategy bindings, or excuse budget
exhaustion. During market hours the workflow refuses creation/acknowledgment.
This retains the deliberate maintenance window without the expired-parent dead
end. All hard/new unexplained incidents still need operator investigation.

Disarmed held diagnostic monitoring keeps the existing supported sleep assertions
active overnight. It does not rely on LIVE permission to keep the host awake;
AC/lid/assertion/clock certification remains mandatory.

## Remaining deliberate boundaries and operational limitations

- Clock certification and query-only diagnostics use the same strict combined
  bound: equality with the configured maximum fails. This corrects a boundary
  comparison; it does not increase the configured threshold or change the source,
  sampling interval, freshness requirement or synchronization mechanism.
- macOS caffeinate assertions cannot make a battery-powered closed-lid computer
  a reliable unattended host. AC, open lid, owned assertions, fresh host evidence
  and combined clock bound strictly below 90 ms remain required.
- Sleep or network loss with a real position is not a guarantee of remote
  protection while the host is suspended. The host must remain awake; known
  position management/reconciliation takes priority on return.
- Repeated failures beyond the existing budget intentionally require an operator.
  A previous legacy hold does not make later failures harmless. There is no
  supported Class C shortcut for an unexplained crash or an exhausted episode.
- Missing/revoked refresh credentials, ambiguous refresh outcome and repeated
  401 remain sticky. A valid supported refresh is noninteractive; a revoked grant
  cannot be repaired by inventing a token or blindly replaying a refresh POST.
- The session outage label includes entry-data recertification. A prolonged
  label alone is not evidence of a prolonged broker outage. Retained before/after
  reconciliations establish measured broker recovery duration. Entry/data proof
  remains unchanged; no timeout was increased and no speculative connection
  recycling added.
- A failed/partially committed operator transition intentionally retains its
  barrier. Do not remove it by editing SQLite or files.

## Deterministic read-only readiness

`python -m trader readiness next-session` reports separate engineering,
deployment, infrastructure, monitoring, control, next-session initialization and
unattended-live states. Normal future market gates are listed separately. SESSION
READY concerns initialization, not opening certification or entry permission.
CONTROL READY is false with an active KILL, even when a structured recovery is
potentially automatic. No readiness flag is used to authorize broker mutations.

Engineering evidence is an operator test-run report at ignored
`reports/production-verification.json` (owner-only permissions), bound to the
exact clean commit/tree, private remote ref, complete JUnit artifact and passing
secret/runtime/history audit artifact hashes. Missing, altered or mismatched
artifacts fail engineering/deployment readiness. The report must be written only
after actual full tests and audits; no command synthesizes a PASS. This is local
operator evidence, not a cryptographic protection against a malicious machine
owner or a replacement for production health proof.
