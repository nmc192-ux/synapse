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

- unresolved worker request health is near zero in the current launch window
- stale ownership incidents remain rare, explainable, and non-repeating across deployments
- scheduler recovery rate is stable and not masking unresolved execution drift
- operator queue remains manageable without sustained manual firefighting
- plugin policy violations are understood, documented, and contained to allowed policy boundaries
- customer diagnostics, support exports, and escalation paths are documented and exercised

Rollout scorecard:

1. Continue
   - request health is stable
   - degraded runs recover automatically
   - intervention queue stays below the agreed operating threshold
2. Hold
   - unresolved degradation appears in daily synthetic-alpha reports
   - stale ownership or browser request stalls trend upward
   - latency grows without a matching increase in recovery quality
3. Expand
   - multiple synthetic-alpha windows remain stable
   - request-health-backed reports show low unresolved degradation
   - operator load remains predictable and support bundles are sufficient for triage

Readiness review checklist:

- synthetic-alpha recommendation is not `hold`
- request-health summaries match runtime events closely enough for operator trust
- worker drain and maintenance behavior is verified in the intended topology
- checkpoint and replay flows are acceptable for the partner workflow class
- support team can gather run, event, replay, and worker-request diagnostics without engineering help

Enterprise buyers should be told the truth:

- Synapse is strongest today for supervised browser-runtime workloads
- broader hosted autonomy still requires additional platform hardening
