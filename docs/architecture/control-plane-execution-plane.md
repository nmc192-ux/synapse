# Control Plane / Execution Plane Split

Synapse now uses an explicit two-plane architecture.

## Control Plane

Primary responsibilities:

- authentication and authorization
- run creation and lifecycle management
- run scheduling and worker leases
- state persistence in Redis/PostgreSQL
- checkpoints and resume orchestration
- runtime event aggregation and WebSocket publication
- API and SDK interaction surfaces

Primary modules:

- `src/synapse/runtime/control_plane.py`
- `src/synapse/runtime/runtime_controller.py`
- `src/synapse/runtime/scheduler.py`
- `src/synapse/runtime/checkpoint_service.py`
- `src/synapse/runtime/run_store.py`
- `src/synapse/runtime/event_bus.py`

## Execution Plane

Primary responsibilities:

- browser session lifecycle
- browser action execution
- locally assigned tool execution
- worker heartbeats and worker status
- action result reporting back to the control plane

Primary modules:

- `src/synapse/runtime/execution_plane.py`
- `src/synapse/runtime/browser_workers.py`
- `src/synapse/runtime/queues.py`
- `src/synapse/workers/browser_worker.py`
- `src/synapse/runtime/browser/`

## Boundary Rules

- The execution plane emits generic `RuntimeEvent` objects and does not publish directly to API or UI transports.
- The control plane owns all auth, run assignment, and durable state mutation.
- Browser workers should not depend on FastAPI route handlers or SDK request models.
- Control-plane services may dispatch browser and assigned tool work into the execution plane, but worker-local session state stays inside the execution plane.

## Current Implementation Notes

- `WorkerExecutionPlane` is the queued execution-plane facade used by the control plane.
- `ExecutionPlaneRuntime` is the worker-local runtime that composes `BrowserRuntime` and optional assigned tool execution.
- `RuntimeController` remains the compatibility entry point, while `ControlPlane` names the orchestration role explicitly.
