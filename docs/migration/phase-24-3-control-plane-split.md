# Phase 24.3 Migration Notes

## Summary

Synapse now treats orchestration and worker execution as separate planes.

## What Changed

- `BrowserWorker` no longer depends directly on WebSocket transport types.
- `BrowserWorkerPool` now accepts an event publisher callback instead of transport coupling.
- `WorkerExecutionPlane` is now the browser-facing execution-plane facade.
- `ToolService` can route assigned run-scoped tool execution into the execution plane.
- `ControlPlane` names the orchestration boundary explicitly while preserving `RuntimeController` compatibility.

## Compatibility

- Existing HTTP and WebSocket APIs are unchanged.
- Existing SDKs are unchanged.
- `RuntimeOrchestrator` compatibility remains intact through the existing wrapper.
- Tests and local integrations that instantiated `BrowserWorkerPool(..., sockets=...)` should switch to:

```python
pool = BrowserWorkerPool(...)
pool.set_event_publisher(event_bus.publish)
```

or, in tests:

```python
pool.set_event_publisher(websocket_manager.broadcast)
```

## Operational Guidance

- Control-plane startup should start the execution plane first, then the scheduler.
- Tool execution remains control-plane local by default; assigned runs may execute tools on their leased worker.
- Worker lease metadata is stored in run metadata under `assigned_worker_id` and `lease_expires_at`.
