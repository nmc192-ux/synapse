from contextlib import asynccontextmanager

from fastapi import FastAPI

from synapse.api.routes import router
from synapse.config import settings
from synapse.runtime.a2a import A2AHub
from synapse.runtime.browser import BrowserRuntime
from synapse.runtime.messaging import AgentMessageBus
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.tools import ToolRegistry
from synapse.transports.websocket_manager import WebSocketManager


browser_runtime = BrowserRuntime()
agent_registry = AgentRegistry()
tool_registry = ToolRegistry()
message_bus = AgentMessageBus()
websocket_manager = WebSocketManager()
a2a_hub = A2AHub(agent_registry)
orchestrator = RuntimeOrchestrator(
    browser=browser_runtime,
    agents=agent_registry,
    tools=tool_registry,
    messages=message_bus,
    a2a=a2a_hub,
    sockets=websocket_manager,
)
a2a_hub.set_task_executor(orchestrator.execute_task)


async def echo_tool(arguments: dict[str, object]) -> dict[str, object]:
    return {"echo": arguments}


tool_registry.register("echo", echo_tool, description="Echo tool arguments for connectivity tests.", plugin_name="core")
tool_registry.load_plugins(
    package_names=settings.plugin_packages,
    module_names=settings.plugin_modules,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await browser_runtime.start()
    try:
        yield
    finally:
        await browser_runtime.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(router, prefix="/api")
