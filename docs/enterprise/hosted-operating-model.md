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
- keep project-scoped operator and API-key credentials separate from internal admin credentials
- require explicit domain allowlists for every externally facing deployment
- treat browser request health as a first-class operational signal, not a debugging afterthought

Operationally important signals:

- browser request health
- stale ownership incidents
- run recovery frequency
- intervention backlog
- plugin denial frequency
- project failure rate and average latency

Hosted rollout should stop or slow down when hold-level alpha gate signals appear in the synthetic-alpha reports.

Recommended operating cadence:

1. Before deploy
   - verify synthetic-alpha recommendation is `continue` or `expand`
   - verify no unresolved hold-level blockers are active
2. During deploy
   - drain workers before maintenance where possible
   - watch request health and scheduler recovery signals for drift
3. After deploy
   - compare stale ownership, latency, and unresolved degradation against the pre-deploy window
   - export a support bundle for any hold-level regression before widening traffic

Hosted assumptions that remain explicit:

- Synapse is strongest in supervised, operator-observable environments
- broader public-hosted browser autonomy still requires more distributed-runtime and isolation hardening
- optional plugins and partner integrations should be treated as controlled rollout surfaces, not default-on features
