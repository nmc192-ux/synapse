# Synapse Development Plan

This document is the execution plan for moving Synapse from a restricted,
supervised design-partner alpha toward a broader external alpha and eventually a
public hosted platform.

It is intentionally phase-based and product-driven. The goal is not to add
broad new features first. The goal is to make the current platform more
trustworthy, observable, recoverable, and operable.

## Planning Assumptions

- Current release status:
  - Internal beta: supported
  - Restricted design-partner alpha: supported
  - Public hosted platform: not supported
- Synthetic alpha on the Mac Mini is the primary external validation harness.
- Repo-first development remains the default:
  - implement in the repository
  - validate in tests
  - validate against the synthetic-alpha harness
- Use the strongest Codex model for architecture, distributed systems, security,
  recovery, and reliability work.

## Model Guidance

Use the best available Codex model for:

- distributed worker execution
- browser reliability and recovery
- scheduler and lease semantics
- tenant/auth/security hardening
- plugin isolation and hosted execution
- operator workflow and recovery semantics
- release-gate and observability design

Use smaller/faster models only for bounded side work after the design is fixed:

- mechanical tests
- docs updates
- localized UI text or layout polish
- small refactors with narrow blast radius

## Phase 1: Browser Reliability Control Plane

### Goal

Reduce browser issue volume, stale ownership confusion, and average run latency.

### Why This Is First

This is the highest-signal product issue in live synthetic-alpha operation.
Browser reliability now matters more than adding new surface area.

### Primary Files

- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/browser_workers.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/browser_workers.py)
- [`/Users/jahanzebhussain/Synapse/src/synapse/workers/browser_worker.py`](/Users/jahanzebhussain/Synapse/src/synapse/workers/browser_worker.py)
- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/browser_service.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/browser_service.py)
- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/run_store.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/run_store.py)

### Subphases

#### 1.1 Request Health Model

- formalize durable request lifecycle state:
  - `queued`
  - `dispatched`
  - `running`
  - `slow`
  - `stuck`
  - `recovered`
  - `completed`
  - `failed`
- persist:
  - dispatch time
  - execution start time
  - last progress time
  - completion time
  - recovery time
  - status reason

#### 1.2 Progress and Stall Semantics

- distinguish:
  - long-running but healthy
  - worker alive but request not progressing
  - worker crashed mid-request
  - ownership conflict
  - late result replay
- align runtime events with those states

#### 1.3 Operator and Harness Visibility

- expose request health clearly through runtime events and persisted state
- give operators enough information to understand:
  - what is stuck
  - why it is stuck
  - whether it recovered
  - whether manual action is required

### Exit Criteria

- synthetic-alpha browser issue rate drops materially
- average run latency improves
- stale ownership is explainable from request state
- operators can distinguish slow, stalled, recovered, and failed requests

## Phase 2: State-Native Synthetic Alpha

### Goal

Make synthetic-alpha the real release gate using first-class runtime state rather
than mixed heuristics.

### Primary Files

- [`/Users/jahanzebhussain/Synapse/examples/synthetic_alpha_swarm/common.py`](/Users/jahanzebhussain/Synapse/examples/synthetic_alpha_swarm/common.py)
- [`/Users/jahanzebhussain/Synapse/examples/synthetic_alpha_swarm/reporter.py`](/Users/jahanzebhussain/Synapse/examples/synthetic_alpha_swarm/reporter.py)
- [`/Users/jahanzebhussain/Synapse/examples/synthetic_alpha_swarm/auditor.py`](/Users/jahanzebhussain/Synapse/examples/synthetic_alpha_swarm/auditor.py)
- [`/Users/jahanzebhussain/Synapse/docs/alpha/failure-taxonomy.md`](/Users/jahanzebhussain/Synapse/docs/alpha/failure-taxonomy.md)

### Subphases

#### 2.1 Runtime Signal First

- prefer runtime events over string-based log inference
- keep websocket feed and run-event backfill aligned
- classify safe recovery separately from real platform failure

#### 2.2 Alpha Gate Metrics

- define rollout thresholds for:
  - stuck request rate
  - stale ownership rate
  - intervention rate
  - scheduler recovery rate
  - average latency
  - browser issue rate

#### 2.3 Release Recommendations

- have reports produce:
  - `hold`
  - `continue`
  - `expand`
- base the recommendation on measured thresholds, not manual interpretation

### Exit Criteria

- synthetic-alpha reports can drive go/no-go decisions
- signal quality is high enough that regressions are obvious
- the harness distinguishes degradation from unsafe failure

## Phase 3: Distributed Worker Execution Maturity

### Goal

Remove controller-local execution assumptions and make worker dispatch more
generally distributed.

### Primary Files

- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/scheduler.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/scheduler.py)
- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/browser_workers.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/browser_workers.py)
- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/state_store.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/state_store.py)
- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/queues.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/queues.py)

### Subphases

#### 3.1 Controller Ownership Model

- make controller ownership explicit and inspectable
- model worker draining, maintenance, and unavailable states

#### 3.2 Remote Execution Transport

- decouple dispatch from local process ownership
- support remote result routing without assuming the scheduler owns the worker

#### 3.3 Durable Multi-Controller Reconciliation

- tighten in-flight request recovery across controller restarts
- reduce topology-specific assumptions in run assignment and result handling

### Exit Criteria

- dispatch is not effectively controller-local
- stale ownership becomes rare and bounded
- multi-controller execution behaves predictably under contention

## Phase 4: Deterministic Recovery and Replay

### Goal

Make recovery paths predictable enough for broader alpha and stronger autonomy.

### Primary Files

- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/checkpoint_service.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/checkpoint_service.py)
- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/planning.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/planning.py)
- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/task_runtime.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/task_runtime.py)
- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/runtime_controller.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/runtime_controller.py)

### Subphases

#### 4.1 Replayable Checkpoints

- improve replay metadata for browser actions and run state
- separate replayable from non-replayable failure classes

#### 4.2 Deterministic Resume

- make operator approval/input map onto explicit resume semantics
- reduce best-effort resume behavior

#### 4.3 Browser Crash Recovery

- tighten crash-aware recovery logic and replay fidelity
- classify non-recoverable failure modes cleanly

### Exit Criteria

- operator intervention changes resumed execution predictably
- browser crash recovery is more deterministic than heuristic
- repeated retry loops decline

## Phase 5: Operator Product Maturity

### Goal

Turn the operator console into a credible external-alpha operations surface.

### Primary Files

- [`/Users/jahanzebhussain/Synapse/ui/components/dashboard.tsx`](/Users/jahanzebhussain/Synapse/ui/components/dashboard.tsx)
- [`/Users/jahanzebhussain/Synapse/ui/hooks/use-synapse-feed.ts`](/Users/jahanzebhussain/Synapse/ui/hooks/use-synapse-feed.ts)
- related UI auth/session code

### Subphases

#### 5.1 Operator Session Model

- real login/session flow
- token refresh
- clearer auth error recovery

#### 5.2 Project-Safe Workflow UX

- stronger project context visibility
- safer context switching
- intervention queue triage improvements

#### 5.3 Request Health and Diagnostics UX

- expose run/request health directly in the dashboard
- enable easy export of run diagnostics

### Exit Criteria

- operator flows are usable without env-token hacks
- intervention handling is quick and low-friction
- project mistakes and auth confusion decline

## Phase 6: Hosted Platform Hardening

### Goal

Move from restricted hosted alpha posture toward broader hosted readiness.

### Primary Files

- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/plugin_isolation.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/plugin_isolation.py)
- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/tools.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/tools.py)
- [`/Users/jahanzebhussain/Synapse/src/synapse/runtime/platform_service.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/platform_service.py)

### Subphases

#### 6.1 Hosted Isolation Maturity

- strengthen hosted plugin execution architecture
- improve portability and operational diagnostics for isolation backends

#### 6.2 Key and Credential Hardening

- improve API key hashing and lifecycle controls
- better secret rotation and auditability

#### 6.3 Hosted Ops Surfaces

- tenant-aware observability
- stronger hosted admin tooling
- clearer health and failure diagnosis

### Exit Criteria

- hosted assumptions are explicit and supportable
- plugin execution is safer and easier to reason about operationally
- key management is stronger than current alpha level

## Phase 7: Broader External Alpha Readiness

### Goal

Expand beyond the current restricted design-partner alpha.

### Deliverables

- clearer rollout thresholds driven by synthetic-alpha metrics
- stronger partner-safe defaults
- support playbooks for common failure classes
- environment-level chaos drills against real external systems

### Exit Criteria

- broader partner variability does not overwhelm operations
- operator load stays manageable
- release expansion is data-driven

## Phase 8: Public Hosted Readiness

### Goal

Prepare for a true public hosted platform.

### Deliverables

- generalized distributed worker fleet
- stronger hosted isolation architecture
- hardened operator/admin/IAM stack
- public-hosted packaging and dependency discipline
- broader abuse, recovery, and chaos validation

### Exit Criteria

- public-hosted deployment is operationally credible
- failure modes are bounded and well understood
- the platform degrades safely under hostile or messy real-world conditions

## Cross-Phase Standards

These apply to every phase:

- implement repo-first, validate harness-second
- add meaningful tests for each major behavior change
- use synthetic-alpha metrics as the final success measure
- preserve stable contracts where possible:
  - run model
  - runtime event schema
  - worker lease schema
  - request/result routing schema
  - intervention API shape
  - plugin trust classes
  - SDK method names

## Recommended Execution Order

1. Phase 1: Browser Reliability Control Plane
2. Phase 2: State-Native Synthetic Alpha
3. Phase 3: Distributed Worker Execution Maturity
4. Phase 4: Deterministic Recovery and Replay
5. Phase 5: Operator Product Maturity
6. Phase 6: Hosted Platform Hardening
7. Phase 7: Broader External Alpha Readiness
8. Phase 8: Public Hosted Readiness

## Immediate Next Execution Target

Start with Phase 1.

The first concrete slice should formalize browser request health in
[`/Users/jahanzebhussain/Synapse/src/synapse/runtime/browser_workers.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/browser_workers.py),
make that state visible to operators and synthetic-alpha reporting, and use the
Mac Mini synthetic-alpha harness as the final validation gate.
