# Enterprise Rollout Readiness

Synapse is not yet a public hosted platform. Enterprise rollout should progress in controlled stages:

1. Restricted design-partner alpha
   - trusted operators
   - supervised runs
   - restricted domain allowlists
   - trusted plugins only
2. Broader external alpha
   - stronger distributed worker routing
   - deterministic recovery improvements
   - operator workflow maturity
3. Hosted production readiness
   - stronger tenant partitioning
   - hardened plugin isolation fleet
   - enterprise auth/admin controls

Readiness gates should be driven by synthetic-alpha telemetry and runtime health, not release cadence.

Minimum rollout criteria:

- unresolved worker request health is near zero
- stale ownership incidents remain rare and explainable
- scheduler recovery rate is stable
- operator queue remains manageable
- plugin policy violations are understood and contained
- customer diagnostics and export paths are documented

Enterprise buyers should be told the truth:

- Synapse is strongest today for supervised browser-runtime workloads
- broader hosted autonomy still requires additional platform hardening
