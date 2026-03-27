import asyncio

from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.browser import CompactStructuredPageModel, OpenRequest
from synapse.models.runtime_event import EventType
from synapse.runtime.browser_service import BrowserService
from synapse.runtime.budget import AgentBudgetLimitExceeded, AgentBudgetManager
from synapse.runtime.budget_service import BudgetService
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.safety import AgentSafetyLayer
from synapse.runtime.security import AgentSecuritySandbox, SandboxRateLimitError


class _StubEvents:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def emit(self, event_type, **kwargs):
        self.events.append({"event_type": event_type, **kwargs})


class _StubBrowser:
    async def open(self, session_id: str, url: str):
        page = type(
            "Page",
            (),
            {
                "title": "Example",
                "url": url,
                "sections": [],
                "buttons": [],
                "inputs": [],
                "forms": [],
                "tables": [],
                "links": [],
                "compact_spm": CompactStructuredPageModel(title="Example", url=url, page_summary="compact"),
                "model_dump": lambda self, mode="json": {"title": "Example", "url": url},
            },
        )()
        return type(
            "State",
            (),
            {
                "session_id": session_id,
                "page": page,
                "metadata": {},
                "model_dump": lambda self, mode="json": {"session_id": session_id, "page": page.model_dump(), "metadata": {}},
            },
        )()

    def current_url(self, session_id: str) -> str:
        return "https://example.com"


def _build_browser_service(events: _StubEvents) -> BrowserService:
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            agent_id="agent-1",
            kind=AgentKind.CUSTOM,
            name="Agent 1",
            security={"allowed_domains": ["example.com"], "allowed_tools": []},
        )
    )
    budget = BudgetService(AgentBudgetManager(), registry, events)  # type: ignore[arg-type]
    return BrowserService(_StubBrowser(), AgentSecuritySandbox(registry), AgentSafetyLayer(), events, budget)  # type: ignore[arg-type]


def test_browser_service_emits_structured_rate_limit_context() -> None:
    async def scenario() -> None:
        events = _StubEvents()
        browser_service = _build_browser_service(events)

        def raise_limit(agent_id: str | None) -> None:
            raise SandboxRateLimitError("Agent 'agent-1' exceeded the browser rate limit of 30 actions per minute.")

        browser_service.sandbox.consume_browser_action = raise_limit

        try:
            await browser_service.open(OpenRequest(session_id="session-1", agent_id="agent-1", url="https://example.com"))
        except SandboxRateLimitError:
            pass

        browser_error = next(event for event in events.events if event["event_type"] == EventType.BROWSER_ERROR)
        assert browser_error["payload"]["error_category"] == "rate_limit"
        assert browser_error["payload"]["threshold_exceeded"] == "Agent 'agent-1' exceeded the browser rate limit of 30 actions per minute."
        assert browser_error["payload"]["budget_limits"]["max_pages"] == 25

    asyncio.run(scenario())


def test_browser_service_emits_structured_budget_limit_context() -> None:
    async def scenario() -> None:
        events = _StubEvents()
        browser_service = _build_browser_service(events)

        async def fail_increment(*args, **kwargs):
            raise AgentBudgetLimitExceeded("Agent terminated: runtime limit exceeded.")

        browser_service.budget_service.increment_page = fail_increment  # type: ignore[method-assign]

        try:
            await browser_service.open(OpenRequest(session_id="session-1", agent_id="agent-1", url="https://example.com"))
        except AgentBudgetLimitExceeded:
            pass

        browser_error = next(event for event in events.events if event["event_type"] == EventType.BROWSER_ERROR)
        assert browser_error["payload"]["error_category"] == "budget_limit"
        assert browser_error["payload"]["threshold_exceeded"] == "Agent terminated: runtime limit exceeded."
        assert browser_error["payload"]["budget_limits"]["max_pages"] == 25

    asyncio.run(scenario())
