# Support And Diagnostics

This is the partner-facing diagnostics workflow for restricted alpha.

## What To Collect For A Failure

Always include:

- project ID
- run ID
- agent ID
- approximate time of failure
- what the agent was trying to do
- whether the run was supervised live
- whether operator intervention occurred

## Run Export Workflow

When debugging a run, gather:

1. run timeline and runtime events
2. browser trace entries
3. browser network failures
4. checkpoints
5. intervention records
6. plugin audit logs if tools/plugins were involved

Relevant APIs:

- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/events`
- `GET /api/runs/{run_id}/trace`
- `GET /api/runs/{run_id}/network`
- `GET /api/checkpoints`
- `GET /api/interventions?run_id=...`

Recommended operator export bundle:

1. `run.json` from `GET /api/runs/{run_id}`
2. `events.json` from `GET /api/runs/{run_id}/events`
3. `trace.json` from `GET /api/runs/{run_id}/trace`
4. `network.json` from `GET /api/runs/{run_id}/network`
5. `checkpoints.json` from `GET /api/checkpoints?run_id=...`
6. `interventions.json` from `GET /api/interventions?run_id=...`
7. `plugin-audit.json` when plugins participated

Keep exports project-scoped and redact any partner-specific private data before sharing outside the operating team.

## Failure Report Format

Partners should send:

- what failed
- whether Synapse stopped safely
- whether it recovered automatically
- whether manual intervention was required
- screenshots or copied operator notes if relevant
- the exact config diff if the issue started after changing allowlists, plugin policy, or auth setup

## When To Include Checkpoints

Include checkpoint references when:

- a run paused for operator intervention
- a run resumed incorrectly
- browser/session behavior after restart looked inconsistent

## When To Include Plugin Audit Logs

Include plugin audit records when:

- a hosted plugin was denied
- a hosted plugin timed out
- a tool call appeared to fail before the browser step ran

## Escalation Guidance

Escalate to the Synapse team immediately if any of these occur:

- possible cross-project data leakage
- possible duplicate run ownership
- untrusted plugin execution in hosted mode
- a run continues after a critical failure without a new operator decision
- unexplained repeated CAPTCHA/challenge loops

## Support Templates

Use the partner-facing issue templates in:

- [`/Users/jahanzebhussain/Synapse/.github/ISSUE_TEMPLATE/bug_report.md`](/Users/jahanzebhussain/Synapse/.github/ISSUE_TEMPLATE/bug_report.md)
- [`/Users/jahanzebhussain/Synapse/.github/ISSUE_TEMPLATE/reproducible_run_failure.md`](/Users/jahanzebhussain/Synapse/.github/ISSUE_TEMPLATE/reproducible_run_failure.md)
- [`/Users/jahanzebhussain/Synapse/.github/ISSUE_TEMPLATE/plugin_request.md`](/Users/jahanzebhussain/Synapse/.github/ISSUE_TEMPLATE/plugin_request.md)
- [`/Users/jahanzebhussain/Synapse/.github/ISSUE_TEMPLATE/operator_intervention_issue.md`](/Users/jahanzebhussain/Synapse/.github/ISSUE_TEMPLATE/operator_intervention_issue.md)
- [`/Users/jahanzebhussain/Synapse/.github/ISSUE_TEMPLATE/alpha_feedback.md`](/Users/jahanzebhussain/Synapse/.github/ISSUE_TEMPLATE/alpha_feedback.md)

## Related References

- [`/Users/jahanzebhussain/Synapse/docs/alpha/operator-runbook.md`](/Users/jahanzebhussain/Synapse/docs/alpha/operator-runbook.md)
- [`/Users/jahanzebhussain/Synapse/docs/alpha/failure-taxonomy.md`](/Users/jahanzebhussain/Synapse/docs/alpha/failure-taxonomy.md)
