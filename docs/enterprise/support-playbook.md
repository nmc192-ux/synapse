# Enterprise Support Playbook

When a partner reports a failure, support should collect:

- run id
- project id
- timestamp window
- intervention ids if any
- request health snapshot for the affected run
- run timeline / replay export
- plugin policy decision details if a plugin was involved

First triage questions:

1. Was the run safe but degraded, or was it unsafe?
2. Did the runtime recover automatically?
3. Did operator intervention resolve the run?
4. Was the failure browser-related, scheduler-related, auth-related, or policy-related?

Recommended diagnostics:

- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/events`
- `GET /api/runs/{run_id}/timeline`
- `GET /api/runs/{run_id}/replay`
- `GET /api/runs/{run_id}/worker-requests`
- `GET /api/runs/{run_id}/delegation-summary`

Support response flow:

1. Confirm scope
   - identify project, run, and tenant boundary
   - verify this is not a cross-project visibility issue
2. Classify the failure
   - browser degradation
   - scheduler / ownership issue
   - recovery / replay mismatch
   - plugin policy or isolation failure
   - auth / operator workflow issue
3. Capture durable state
   - export run details, run events, worker request health, and delegation summary
   - capture intervention timeline and any checkpoint or replay artifacts
4. Decide the next action
   - recover automatically
   - operator-assisted continuation
   - hold rollout
   - escalate to engineering

Escalate immediately when:

- unresolved request health remains active
- stale ownership repeats across runs
- plugin policy is bypassed or behaves unexpectedly
- cross-project access is suspected
- recovery behavior contradicts durable state

Bug reports from enterprise partners should include:

- exact wall-clock time and timezone
- tenant, project, and run identifiers
- whether operator intervention occurred
- whether the run eventually recovered
- what the user expected to happen next
- any replay or worker-request snapshots already captured
