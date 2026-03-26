import sys
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.models.agent import AgentBudgetUsage, AgentDefinition, AgentExecutionLimits, AgentKind
from synapse.runtime.budget import AgentBudgetLimitExceeded, AgentBudgetManager


def test_step_limit_exceeded() -> None:
    agent = AgentDefinition(
        agent_id="agent-step",
        kind=AgentKind.CUSTOM,
        name="Step Agent",
        limits=AgentExecutionLimits(max_steps=1),
    )
    manager = AgentBudgetManager(default_limits=AgentExecutionLimits())
    manager.increment_step(agent)

    try:
        manager.check_limits(agent)
    except AgentBudgetLimitExceeded as exc:
        assert str(exc) == "Agent terminated: step limit exceeded."
    else:
        raise AssertionError("Expected hard step limit to terminate execution.")


def test_runtime_timeout() -> None:
    agent = AgentDefinition(
        agent_id="agent-timeout",
        kind=AgentKind.CUSTOM,
        name="Timeout Agent",
        limits=AgentExecutionLimits(max_runtime_seconds=1),
    )
    manager = AgentBudgetManager(default_limits=AgentExecutionLimits())
    budget = manager.get_or_create(agent)
    budget.start_time -= 5

    try:
        manager.check_limits(agent)
    except AgentBudgetLimitExceeded as exc:
        assert str(exc) == "Agent terminated: runtime limit exceeded."
    else:
        raise AssertionError("Expected runtime limit to terminate execution.")


def test_soft_warning_triggers() -> None:
    agent = AgentDefinition(
        agent_id="agent-soft",
        kind=AgentKind.CUSTOM,
        name="Soft Agent",
        limits=AgentExecutionLimits(max_pages=10),
    )
    manager = AgentBudgetManager(default_limits=AgentExecutionLimits())
    for _ in range(8):
        manager.increment_page(agent)

    _, warnings = manager.check_limits(agent)

    assert warnings == ["Agent budget warning: 80% of max_pages reached."]


def test_agent_override_limits() -> None:
    default_limits = AgentExecutionLimits(max_steps=60, max_pages=25, max_runtime_seconds=180)
    agent = AgentDefinition(
        agent_id="agent-override",
        kind=AgentKind.CUSTOM,
        name="Override Agent",
        limits=AgentExecutionLimits(max_steps=120, max_pages=50, max_runtime_seconds=300),
    )
    manager = AgentBudgetManager(default_limits=default_limits)

    resolved = manager.resolve_limits(agent)

    assert resolved.max_steps == 120
    assert resolved.max_pages == 50
    assert resolved.max_runtime_seconds == 300


def test_dashboard_budget_endpoint() -> None:
    async_api = types.ModuleType("playwright.async_api")
    async_api.Browser = object
    async_api.BrowserContext = object
    async_api.Page = object
    async_api.Playwright = object

    async def async_playwright() -> None:
        return None

    async_api.async_playwright = async_playwright
    playwright = types.ModuleType("playwright")
    playwright.async_api = async_api
    sys.modules.setdefault("playwright", playwright)
    sys.modules.setdefault("playwright.async_api", async_api)

    from synapse.api.routes import get_authenticator, get_orchestrator, router
    from synapse.config import Settings
    from synapse.security.auth import Authenticator

    class StubOrchestrator:
        async def get_agent_budget(self, agent_id: str) -> AgentBudgetUsage:
            assert agent_id == "budget-agent"
            return AgentBudgetUsage(
                steps_used=12,
                pages_opened=4,
                tool_calls=6,
                tokens_used=3500,
                memory_writes=3,
                runtime_seconds=42,
            )

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_orchestrator] = lambda: StubOrchestrator()
    app.dependency_overrides[get_authenticator] = lambda: Authenticator(Settings(auth_required=False))
    client = TestClient(app)

    response = client.get("/api/agents/budget-agent/budget")

    assert response.status_code == 200
    assert response.json()["steps_used"] == 12
    assert response.json()["runtime_seconds"] == 42
