# Alpha Failure Taxonomy

This document defines the red-team and chaos scenarios used to harden Synapse before the restricted design-partner alpha.

## Operator Review Format

Use this concise review format for every chaos run:

| Field | Meaning |
| --- | --- |
| `scenario` | Scenario identifier |
| `what_failed` | Primary failure or abuse trigger |
| `safe` | `true` if Synapse failed closed and did not leak/corrupt state |
| `recovered` | `true` if automatic recovery completed safely |
| `manual_intervention_required` | `true` if an operator must inspect/resume/cancel |
| `evidence` | Key runtime events, run IDs, request IDs, or audit records |
| `notes` | Remaining gaps or why the simulation is partial |

## Scenario Matrix

### 1. Redis contention and temporary unavailability
- Setup: simulate durable lease acquisition failure or contention while a scheduler attempts assignment.
- Expected behavior: no lease is persisted on failure; no duplicate run owner is created.
- Failure mode: durable coordination unavailable during assign/retry window.
- Severity: high.
- Recovery criteria: scheduler can retry later without conflicting lease state.

### 2. Controller restart during active runs
- Setup: restart controller with persisted requests and session ownership already present.
- Expected behavior: outstanding dispatch state is recovered, session ownership is restored, reconciliation events are emitted.
- Failure mode: controller process exits while work is still in flight.
- Severity: high.
- Recovery criteria: `worker.request.recovered` and `run.dispatch.reconciled` emitted; no duplicate ownership.

### 3. Worker crash during browser action
- Setup: worker disappears while a durable browser request is in `running` or `dispatched`.
- Expected behavior: request is surfaced as recovered; run does not silently continue.
- Failure mode: browser worker exits before persisting a result.
- Severity: high.
- Recovery criteria: recovered request remains visible for safe retry or operator handling.

### 4. WebSocket disconnect during intervention-required state
- Setup: operator socket disconnects immediately after challenge/captcha intervention is triggered.
- Expected behavior: intervention remains durably queued and can be fetched after reconnect.
- Failure mode: live operator session drops during approval-required state.
- Severity: medium.
- Recovery criteria: intervention remains `pending`; no run auto-resume occurs.

### 5. Repeated lease races
- Setup: multiple schedulers repeatedly try to assign the same run.
- Expected behavior: a single durable lease owner wins; all observers see one fencing token.
- Failure mode: concurrent assignment race.
- Severity: critical.
- Recovery criteria: one lease record, one winning worker, no split ownership.

### 6. Cross-tenant access attempts
- Setup: simultaneous feeds and API calls from different projects while one project emits events.
- Expected behavior: no event, run, session, profile, or checkpoint data crosses project boundaries.
- Failure mode: cross-project read attempt.
- Severity: critical.
- Recovery criteria: unauthorized project sees nothing and receives denial where applicable.

### 7. A2A abuse attempts
- Setup: operator or rogue service token tries to bind to an agent without explicit delegation.
- Expected behavior: connection is denied; no message delivery occurs.
- Failure mode: impersonation or abused A2A routing attempt.
- Severity: critical.
- Recovery criteria: websocket disconnect/denial and no downstream event delivery.

### 8. Browser crash during run
- Setup: browser action fails as if the browser process crashed.
- Expected behavior: failure is surfaced, no silent result corruption, and the run does not continue without explicit recovery.
- Failure mode: browser runtime action crash or hard failure.
- Severity: high.
- Recovery criteria: failure is visible in durable request/result state or intervention flow.

### 9. Plugin escape attempts in hosted mode
- Setup: hosted plugin attempts network access, repo-root reads, or untrusted execution.
- Expected behavior: attempt is denied, audited, and not executed unsafely.
- Failure mode: plugin tries to escape hosted policy boundaries.
- Severity: critical.
- Recovery criteria: denial audit log exists; no unsafe plugin execution occurs.

### 10. Repeated challenge/captcha loops
- Setup: multiple challenge or captcha events hit the same run without operator resolution.
- Expected behavior: run remains `waiting_for_operator`; no autonomous continuation.
- Failure mode: repeated anti-bot barrier loop.
- Severity: high.
- Recovery criteria: durable intervention queue remains pending until explicit operator action.

### 11. Duplicate result delivery
- Setup: the same browser worker result is delivered multiple times.
- Expected behavior: result handling is idempotent; no duplicate artifacts or state corruption.
- Failure mode: duplicate result replay after retry/reconnect.
- Severity: high.
- Recovery criteria: one durable result record and `worker.result.replayed` emitted on duplicate.

### 12. Stale session ownership conflicts
- Setup: a controller sees a session owned by a stale or foreign worker.
- Expected behavior: dispatch is blocked, ownership is marked stale, and recovery is surfaced.
- Failure mode: stale live-session affinity after crash or partition.
- Severity: high.
- Recovery criteria: `worker.ownership.stale` emitted and unsafe reuse blocked.

## Automated Coverage

Automated in `tests/chaos/`:
- redis temporary unavailability
- controller restart recovery
- worker crash during browser action
- websocket disconnect during intervention queueing
- repeated lease races
- cross-tenant event isolation
- A2A abuse denial
- plugin escape denial in hosted mode
- repeated challenge loops
- duplicate result delivery
- stale session ownership conflicts

Partially simulated / still manual:
- true browser process crash under a real browser engine
- Redis network partitions against a real Redis deployment
- multi-controller dispatch across separately running controllers with real remote workers

These remaining cases should be exercised during pre-alpha environment drills even though the suite now covers the corresponding safe-failure semantics in-process.
