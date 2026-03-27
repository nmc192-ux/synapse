# Operator Runbook

This runbook is for the people supervising restricted alpha workloads.

## When Operators Must Stay In The Loop

Operators are required for:

- CAPTCHA or challenge detection
- approval-required actions
- sensitive action review
- repeated challenge loops
- unexpected worker ownership or recovery events
- browser runs touching customer-controlled workflows

## Standard Run Workflow

1. confirm project context in the dashboard
2. confirm the run belongs to the expected partner project
3. confirm the target domain is allowlisted
4. monitor runtime events and interventions
5. approve or reject only after reviewing run context
6. export traces/checkpoints if the run fails or behaves unexpectedly
7. record the operator decision and any partner-visible impact

## Intervention Workflow

When Synapse enters `waiting_for_operator`:

1. inspect the run context and intervention reason
2. check recent browser traces, network failures, and challenge events
3. decide:
   - approve
   - reject
   - provide input
4. record why the decision was made
5. if challenge repeats, stop the run and escalate

## Common Failure Modes

- `worker.request.recovered`
  - controller or worker disruption happened during dispatch
- `worker.result.replayed`
  - duplicate result arrived and was safely deduplicated
- `worker.ownership.stale`
  - stale session ownership detected, do not force reuse
- `run.dispatch.reconciled`
  - durable dispatch state was recovered after restart
- `browser.captcha.detected`
  - run should remain supervised
- `browser.challenge.detected`
  - likely anti-bot or access barrier

## Recovery Defaults

- if auth expires in the dashboard, re-authenticate before taking any intervention action
- if a worker recovery event appears without a clean result, do not assume the browser action completed
- if the same run re-enters intervention repeatedly, reject and escalate instead of cycling approvals
- if project context is unclear, stop and reload the dashboard with the correct project-scoped session

## Escalate Immediately If

- project context looks wrong
- runtime events from another partner appear in the dashboard
- a run continues after repeated challenge events without a new operator decision
- an untrusted plugin is allowed to run in hosted mode
- duplicate run ownership is suspected

## Safe Operator Defaults

- reject when unsure
- prefer input with narrow guidance over broad approval
- stop challenge loops early
- gather traces before retrying
- never paste secrets into the operator console
- never continue a run after a critical failure unless the new state is understood and reviewed
