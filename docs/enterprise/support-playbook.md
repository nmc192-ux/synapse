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

Escalate immediately when:

- unresolved request health remains active
- stale ownership repeats across runs
- plugin policy is bypassed or behaves unexpectedly
- cross-project access is suspected
- recovery behavior contradicts durable state
