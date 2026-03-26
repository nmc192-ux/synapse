"""Core runtime services for Synapse."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "CompressionProvider": "synapse.runtime.compression.base",
    "create_compression_provider": "synapse.runtime.compression.base",
    "AnthropicProvider": "synapse.runtime.llm",
    "LLMProvider": "synapse.runtime.llm",
    "LocalModelProvider": "synapse.runtime.llm",
    "OpenAIProvider": "synapse.runtime.llm",
    "create_llm_provider": "synapse.runtime.llm",
    "ControlPlane": "synapse.runtime.control_plane",
    "EventBus": "synapse.runtime.event_bus",
    "BrowserService": "synapse.runtime.browser_service",
    "BrowserWorkerPool": "synapse.runtime.browser_workers",
    "BenchmarkSuite": "synapse.runtime.benchmarking",
    "BudgetService": "synapse.runtime.budget_service",
    "CapabilityRegistry": "synapse.runtime.capabilities",
    "CheckpointService": "synapse.runtime.checkpoint_service",
    "ExecutionPlaneRuntime": "synapse.runtime.execution_plane",
    "MemoryService": "synapse.runtime.memory_service",
    "PlatformService": "synapse.runtime.platform_service",
    "RuntimeController": "synapse.runtime.runtime_controller",
    "RunScheduler": "synapse.runtime.scheduler",
    "SessionProfileManager": "synapse.runtime.session_profiles",
    "InMemoryRuntimeStateStore": "synapse.runtime.state_store",
    "RedisRuntimeStateStore": "synapse.runtime.state_store",
    "RuntimeStateStore": "synapse.runtime.state_store",
    "create_runtime_state_store": "synapse.runtime.state_store",
    "TaskRuntime": "synapse.runtime.task_runtime",
    "ToolService": "synapse.runtime.tool_service",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value
