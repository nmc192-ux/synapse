# Hosted Operating Model

Synapse hosted operation should assume:

- Redis-backed durable runtime state
- PostgreSQL-backed platform/task state
- explicit controller/worker topology
- project-scoped auth for every operator and API key
- plugin execution under the hosted isolation backend

Recommended hosted controls:

- isolate plugin execution from the repo root and host filesystem
- treat browser workers as replaceable execution slots
- prefer draining workers before maintenance or deploys
- use synthetic-alpha metrics as a canary before widening tenant scope
- preserve audit logs for plugin policy denials, scheduler recovery, and operator interventions

Operationally important signals:

- browser request health
- stale ownership incidents
- run recovery frequency
- intervention backlog
- plugin denial frequency
- project failure rate and average latency

Hosted rollout should stop or slow down when hold-level alpha gate signals appear in the synthetic-alpha reports.
