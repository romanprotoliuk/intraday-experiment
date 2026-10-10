# Investigated legacy-review successors

This control-plane path is explicit, outside-session, flat-only investigation.
It is never invoked by the worker, a timer, startup, readiness, or deployment.
Creating a receipt does not acknowledge a hold, remove KILL, enable LIVE, or
change a session, setup, position, order, strategy, or risk allowance.

## Read timeout classification

The pinned MCP SDK raises `McpError(408)` from its local `TimeoutError` handler.
The classifier recognizes the SDK exception with that retained implicit context
on a read operation. A remote JSON-RPC 408 without local timeout context and a
mixed unknown exception group remain reconciliation failures. Buy/cancel tool
classification still takes precedence and remains mutation ambiguity. No
mutation retry is added. The existing default position recovery policy still
seeks verified risk reduction; optional bounded recovery still requires all
position evidence and preserves the original deadline.

The session coordinator may conservatively elevate a repeated read failure to
prolonged connectivity loss. Complete broker reconciliation does not substitute
for data/opening certification or clear a connectivity gate by itself.

## Receipt lifecycle

The original `legacy_incident_review` is retained byte-for-byte when a successor
is created. Immutable successor receipts are stored under separate unique keys;
a head identifies the current receipt. Transactional lifecycle events reference
the predecessor, successor, and complete intervening ledger digest. A separate
status record records acknowledgment without overwriting a successor receipt.

A successor requires all of the following:

- Explicit new investigation attestation and exact predecessor ID.
- Same bindable legacy KILL instance, original incident evidence, account,
  configuration, and strategy; no consumed predecessor. An expired predecessor
  is archival evidence only and requires a complete new investigation.
- Every ledger row from the original review watermark through the final locked
  transaction is present, chronological, and included in the digest.
- Every safety event is explained. The narrow supported cases are positively
  identified broker reads with local SDK deadline or known HTTP transport
  provenance. Original event classifications are retained in the findings.
- Complete flat broker snapshots immediately before and after each outage;
  unchanged terminal order history and retained account binding. New terminal
  activity is not accepted merely because the account is flat again.
- Two independent fresh broker/control snapshots, current local flatness, no
  unresolved intent, healthy schema/DB, expected account, stable worker ownership,
  heartbeat, watchdog, host/AC/sleep/clock, matching config/strategy, and no
  conflicting control. Fresh order history must match the investigated interval.
- A clean committed build, bound to the receipt. A code change invalidates it.
- Each new receipt expires at the next opening after its own complete investigation.
  The predecessor expiry and immutable contents are never edited or reused as authority.

Unknown events, mutations, mismatches, positions, working/partial orders, changed
hold/account/config/strategy, database failures, worker restarts or unknown worker
failures, host-sleep/clock incidents, explicit operator holds, missing evidence,
and exhausted recovery budget require a different investigation. They cannot
be explained away by a subsequent flat snapshot. This path intentionally does
not adjudicate held-position incidents.

## Bounds

The pre-failure reconciliation must be no older than the configured idle polling
interval plus watchdog cycle deadline. The subsequent full reconciliation must
arrive within the existing connectivity halt horizon. Three failures (the
configured watchdog restart limit) within the configured connectivity stability
window reject this narrow successor path. This is an additional conservative
review constraint; it does not reset or replace runtime outage/restart budgets.
The existing control proof's worker-stability and quiet-period requirements apply.
No timeout, freshness, clock, host, or risk threshold is increased.

## Supported workflow

After separate authorized deployment of a clean tested build, an authorized
operator can explicitly reinvestigate an invalid or expired, unconsumed predecessor:

```
python -m trader legacy-incident successor --review-id PREDECESSOR_ID --investigation-complete
```

That command performs read-only broker calls and appends a receipt and lifecycle
events only if all requirements pass. It does not clear KILL. Any acknowledgment
is a separate `legacy-incident acknowledge` operation requiring explicit
operator authorization, exact current receipt ID, and fresh proof. No command
in this document grants authorization to execute it.

Expired receipts cannot authorize acknowledgment. Incomplete history and unsupported incidents fail closed;
there is no automatic renewal. Retained snapshots cannot prove continuously
observed broker truth between polls. The full ledger, stable order history, fresh
reads, and explicit investigation are the evidence boundary, not a claim that
unobserved external activity is impossible.


## Exhausted episodes and authority boundaries

Both the stored cumulative counter and preserved-generation ledger records veto
review/acknowledgment. A missing newer counter does not mean zero historical
failures. The larger count is used without summing overlapping representations,
and an unresolved preserved failure or malformed counter fails closed. No
counter, event, hold or receipt is reset. Normal readiness uses the same veto.

Generic `production activate --recover-kill` cannot adjudicate legacy text or
replace the dedicated incident workflow. Explicit activation is not forensic
review authority. It also cannot release hard/operator-required or mismatched
structured state, and rechecks budget/provenance immediately before a permitted
transition. Ordinary activation's broker/data/risk/host prerequisites remain.

There is **no implemented disposition/reset workflow for an exhausted legacy
incident with missing historical evidence**. Documentation saying "operator
investigation required" is not such an API. Designing a new prospective episode
would require independently reviewed policy, immutable disposition evidence,
explicit authorization and fresh proof; it cannot manufacture a pre-outage
snapshot, remove the old incident or simply reset the counter. This implementation
adds no such path and does not make an ineligible incident eligible.
