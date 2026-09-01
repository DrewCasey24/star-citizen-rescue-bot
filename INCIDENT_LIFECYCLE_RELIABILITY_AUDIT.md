# Incident Lifecycle Reliability Audit

This audit covers the current Discord + PostgreSQL incident lifecycle: Create → Respond → Join → Arrive → Backup → Priority → Leave/Handoff → Close.

## Severity: High

### 1. Discord responder state transitions are not atomic

`bot.py` updates Respond, Arrived, Backup, Close, and Priority with unconditional SQL `UPDATE` statements. `run_bot.py` reads state before calling those methods and reads it again afterward to decide whether to append ledger events. Those reads and the update are separate database operations/connections.

**Race:** two responders can press Respond nearly simultaneously. Both can observe the old primary, then both updates can succeed. The last database write wins, while Discord interaction responses/ledger behavior can reflect a different responder.

**Required hardening:** move Discord state transitions into a transaction using `SELECT ... FOR UPDATE`, validate the current state while holding the row lock, perform the state change and ledger insert in the same transaction, and return a structured result to the Discord interaction.

### 2. Backup is repeatable and produces duplicate pages/events

The Discord `Need Backup` button always updates `backup_requested_at=NOW()`, always records a `backup_requested` ledger event, and always pages responder roles. Unlike the web action path, there is no idempotency guard.

**Required hardening:** reject Backup when `backup_requested_at IS NOT NULL`. The state check, timestamp update, and ledger event must be atomic. Only page roles when the transition was newly committed.

### 3. Closed incidents can be mutated by stale Discord controls

The core update methods do not include `WHERE status <> 'closed'` or equivalent locked-state validation. A stale/persistent interaction can therefore attempt Respond, Arrived, Backup, or Priority after closure if the interaction-level permission checks allow it.

**Required hardening:** every mutation must validate that the incident exists and is not closed inside the transaction. Closed is terminal.

## Severity: Medium

### 4. Arrived is database-idempotent but interaction side effects are not fully idempotent

The timestamp uses `COALESCE(arrived_at,NOW())`, and the ledger wrapper only records the first observed transition. However, the Discord card can still be edited and the dispatch board refreshed on repeated button use. This is weaker than the web path, which rejects repeat Arrived actions.

**Required hardening:** return a conflict/already-completed result for repeat Arrived presses before Discord side effects.

### 5. Priority changes are vulnerable to stale/concurrent decisions

Priority updates are unconditional. The ledger wrapper reads the previous priority separately from the update. Concurrent priority controls can therefore create misleading old→new audit descriptions.

**Required hardening:** lock the incident row, derive the prior priority under lock, reject no-op/boundary changes, update priority, and insert the exact old→new ledger event in one transaction.

### 6. Ledger writes are not transactional with most Discord-side database mutations

`record_incident_event` is normally called after the state update on another connection. A process interruption or database error between those operations can leave correct incident state with a missing audit event. The inverse can also become possible as code evolves.

**Required hardening:** state mutation and immutable ledger insertion should share one transaction. Discord API work should happen only after commit.

### 7. Incident creation can orphan a Discord channel

Creation currently creates the Discord channel before inserting the database incident record. If the database insert fails, the channel remains but the incident is absent from PostgreSQL. The wrapper then attempts a ledger lookup that finds no incident.

**Required hardening:** creation needs explicit compensation. If the database record cannot be persisted after channel creation, delete/archive the just-created channel and tell the requester creation failed. Also make `create_incident_record` return success/failure rather than swallowing persistence errors.

### 8. Incident creation does not immediately persist the incident-card message ID

The incident card message is sent, but its message ID is not saved at creation. Dashboard persistence can self-heal later by scanning Discord, but there is a window where synchronization depends on message discovery.

**Required hardening:** capture the return value of `channel.send(...)` and persist `incident_message_id` immediately.

## Severity: Low / Design Consistency

### 9. Primary withdrawal preserves `responded_at`

This is intentional in the current design: when the primary leaves, the incident returns to `awaiting_responder` but keeps the first response timestamp. A later primary assignment therefore measures first claim rather than latest-primary claim. Keep this behavior documented because it affects performance metrics.

### 10. Support responder membership and primary assignment are separate concepts

This is also intentional. Primary withdrawal does not auto-promote support responders. The incident returns to Awaiting Responder and requires an explicit Respond action. Keep this rule while hardening concurrency.

## Recommended implementation order

1. Introduce one atomic Discord incident-transition function using `SELECT ... FOR UPDATE`.
2. Move Respond / Arrived / Backup / Close state changes and ledger inserts into it.
3. Make Discord buttons perform side effects only when that function reports a newly committed transition.
4. Make priority transitions atomic with exact old/new ledger values.
5. Make Join atomic enough to guarantee one responder event per new membership.
6. Keep Leave/Handoff's existing row lock, but move its ledger insert into the same transaction.
7. Add incident-creation compensation and immediate `incident_message_id` persistence.
8. Add regression tests for double-click/concurrent Respond, Backup, Arrived, Close, Join, Leave, and priority changes.

## Invariants to enforce

- Closed is terminal.
- Only one primary responder exists at a time.
- A repeated completed action does not create another ledger event or responder page.
- Every committed state transition has exactly one corresponding ledger event.
- Discord notifications occur after database commit and never determine database truth.
- Retry/reconciliation operations are idempotent.
- A failed database creation does not leave a live orphan incident channel.
