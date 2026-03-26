from contextlib import asynccontextmanager

from fastapi import FastAPI

from synapse.api.routes import router
from synapse.config import settings
from synapse.runtime.a2a import A2AHub
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.browser import BrowserRuntime
from synapse.runtime.browser_workers import BrowserWorkerPool
from synapse.runtime.compression.base import create_compression_provider
from synapse.runtime.control_plane import ControlPlane
from synapse.runtime.execution_plane import ExecutionPlaneRuntime
from synapse.runtime.llm import create_llm_provider
from synapse.runtime.memory import AgentMemoryManager
from synapse.runtime.messaging import AgentMessageBus
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.security import AgentSecuritySandbox
from synapse.runtime.session_profiles import SessionProfileManager
from synapse.runtime.safety import AgentSafetyLayer
from synapse.runtime.state_store import InMemoryRuntimeStateStore, create_runtime_state_store
from synapse.runtime.task_manager import TaskExecutionManager
from synapse.runtime.tools import ToolRegistry
from synapse.security.auth import Authenticator
from synapse.transports.websocket_manager import WebSocketManager


runtime_state_store = InMemoryRuntimeStateStore()
authenticator = Authenticator(settings)
compression_provider = create_compression_provider(settings)
agent_registry = AgentRegistry(state_store=runtime_state_store)
tool_registry = ToolRegistry()
message_bus = AgentMessageBus()
websocket_manager = WebSocketManager(state_store=runtime_state_store, compression_provider=compression_provider)
session_profile_manager = SessionProfileManager(state_store=runtime_state_store)
browser_runtime = BrowserWorkerPool(
    state_store=runtime_state_store,
    runtime_factory=lambda: ExecutionPlaneRuntime(
        browser_runtime=BrowserRuntime(state_store=runtime_state_store, profile_manager=session_profile_manager),
        tool_registry=tool_registry,
    ),
)
sandbox = AgentSecuritySandbox(agent_registry, state_store=runtime_state_store)
a2a_hub = A2AHub(
    agent_registry,
    state_store=runtime_state_store,
    sockets=websocket_manager,
    compression_provider=compression_provider,
    sandbox=sandbox,
)
memory_manager = AgentMemoryManager()
task_manager = TaskExecutionManager()
safety = AgentSafetyLayer()
budget_manager = AgentBudgetManager()
llm_provider = create_llm_provider(settings)
orchestrator = ControlPlane(
    browser=browser_runtime,
    agents=agent_registry,
    tools=tool_registry,
    messages=message_bus,
    a2a=a2a_hub,
    memory_manager=memory_manager,
    task_manager=task_manager,
    sockets=websocket_manager,
    sandbox=sandbox,
    safety=safety,
    budget_manager=budget_manager,
    state_store=runtime_state_store,
    session_profiles=session_profile_manager,
    llm=llm_provider,
    compression_provider=compression_provider,
    authenticator=authenticator,
)
a2a_hub.set_task_executor(orchestrator.execute_task)


async def echo_tool(arguments: dict[str, object]) -> dict[str, object]:
    return {"echo": arguments}


tool_registry.register_plugin(
    name="core",
    module="synapse.main",
    capabilities=["echo"],
    endpoint="echo",
)
tool_registry.register("echo", echo_tool, description="Echo tool arguments for connectivity tests.", plugin_name="core")
tool_registry.load_plugins(
    package_names=settings.plugin_packages,
    module_names=settings.plugin_modules,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store = await create_runtime_state_store()
    websocket_manager.set_state_store(store)
    browser_runtime.set_state_store(store)
    agent_registry.set_state_store(store)
    a2a_hub.set_state_store(store)
    a2a_hub.set_sandbox(sandbox)
    orchestrator.state_store = store
    sandbox.set_state_store(store)
    await agent_registry.load_from_store()
    await a2a_hub.cleanup_stale_connections()
    await browser_runtime.start()
    await orchestrator.scheduler.start()
    await memory_manager.start()
    await task_manager.start()
    try:
        yield
    finally:
        await orchestrator.scheduler.stop()
        await store.stop()
        await task_manager.stop()
        await memory_manager.stop()
        await browser_runtime.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(router, prefix="/api")
