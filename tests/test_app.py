from synapse.connectors.codex import CodexConnector
from fastapi.testclient import TestClient

from synapse.main import app
from synapse.models.a2a import A2AEnvelope, A2AMessageType, AgentWireMessage
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.browser import BrowserState, PageButton, PageLink, PageSection, StructuredPageModel
from synapse.models.loop import AgentAction, AgentActionType
from synapse.models.memory import MemorySearchRequest, MemoryStoreRequest, MemoryType
from synapse.models.plugin import ToolDescriptor
from synapse.models.task import TaskCreateRequest, TaskRequest, TaskStatus
from synapse.runtime.registry import AgentRegistry
from synapse.sdk import SynapseClient


def test_healthcheck() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_browser_state_serialization() -> None:
    state = BrowserState(
        session_id="session-1",
        page=StructuredPageModel(
            url="https://example.com",
            title="Example",
            sections=[PageSection(heading="Overview", text="Structured page summary")],
            buttons=[PageButton(text="Continue", selector_hint="button")],
            links=[PageLink(text="Docs", href="https://example.com/docs", selector_hint="a")],
        ),
    )
    assert state.model_dump()["page"]["title"] == "Example"


def test_task_request_supports_agent_actions() -> None:
    task = TaskRequest(
        task_id="task-1",
        agent_id="agent-1",
        goal="Capture a screenshot",
        actions=[AgentAction(action_id="a1", type=AgentActionType.SCREENSHOT)],
    )
    assert task.actions[0].type == AgentActionType.SCREENSHOT


def test_a2a_envelope_serialization() -> None:
    envelope = A2AEnvelope(
        type=A2AMessageType.REQUEST,
        sender_agent_id="agent-a",
        recipient_agent_id="agent-b",
        payload={"method": "ping"},
    )
    dumped = envelope.model_dump()
    assert dumped["type"] == "request"
    assert dumped["payload"]["method"] == "ping"


def test_agent_wire_message_serialization() -> None:
    message = AgentWireMessage(
        type=A2AMessageType.REQUEST_TASK,
        agent="research_agent",
        target_agent="analysis_agent",
        payload={"task": {"task_id": "t-1", "agent_id": "analysis_agent", "goal": "Analyze findings"}},
    )
    dumped = message.model_dump()
    assert dumped["type"] == "REQUEST_TASK"
    assert dumped["target_agent"] == "analysis_agent"


def test_tools_endpoint_returns_descriptors() -> None:
    client = TestClient(app)
    response = client.get("/api/tools")
    assert response.status_code == 200
    tools = [ToolDescriptor.model_validate(item) for item in response.json()]
    assert any(tool.name == "github.search" for tool in tools)
    assert any(tool.endpoint == "pdf.read" for tool in tools)


def test_sdk_client_exposes_browser() -> None:
    client = SynapseClient("http://127.0.0.1:8000")
    assert client.browser is not None
    assert client.browser.list_tools() is not None
    client.close()


def test_structured_page_model_has_sections_and_buttons() -> None:
    page = StructuredPageModel(
        title="Dashboard",
        url="https://example.com",
        sections=[PageSection(heading="Hero", text="Welcome")],
        buttons=[PageButton(text="Launch", selector_hint="button.launch")],
    )
    assert page.sections[0].heading == "Hero"
    assert page.buttons[0].text == "Launch"


def test_task_create_request_defaults() -> None:
    task = TaskCreateRequest(goal="Review active runtime tasks")
    assert task.goal == "Review active runtime tasks"
    assert task.constraints == {}
    assert task.assigned_agent is None
    assert TaskStatus.CLAIMED == "claimed"


def test_memory_requests_validate() -> None:
    store_request = MemoryStoreRequest(
        agent_id="agent-1",
        memory_type=MemoryType.SHORT_TERM,
        content="Stored page summary",
        embedding=[0.1, 0.2],
    )
    search_request = MemorySearchRequest(agent_id="agent-1", embedding=[0.1, 0.2], limit=3)
    assert store_request.memory_type == MemoryType.SHORT_TERM
    assert search_request.limit == 3


def test_sdk_client_exposes_memory() -> None:
    client = SynapseClient("http://127.0.0.1:8000")
    assert client.memory is not None
    client.close()


def test_codex_connector_normalizes_plan() -> None:
    connector = CodexConnector(SynapseClient("http://127.0.0.1:8000"))
    normalized = connector.normalize_task(
        {
            "goal": "Inspect homepage",
            "plan": {
                "actions": [{"type": "click", "selector": "button"}],
                "extract": [{"selector": "h1"}],
                "screenshot": True,
            },
        }
    )
    assert normalized["goal"] == "Inspect homepage"
    assert normalized["actions"][0]["type"] == "click"
    connector.client.close()


def test_agent_registry_finds_and_ranks_agents() -> None:
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            agent_id="high-reputation",
            kind=AgentKind.CUSTOM,
            name="High Reputation",
            endpoint="ws://agent-high",
            capability_tags=["web_scraping"],
            reputation=0.95,
            latency=120,
        )
    )
    registry.register(
        AgentDefinition(
            agent_id="fast-online",
            kind=AgentKind.CUSTOM,
            name="Fast Online",
            endpoint="ws://agent-fast",
            capability_tags=["web_scraping"],
            reputation=0.6,
            latency=20,
        )
    )
    registry.register(
        AgentDefinition(
            agent_id="other-capability",
            kind=AgentKind.CUSTOM,
            name="Other Capability",
            endpoint="ws://agent-other",
            capability_tags=["analysis"],
            reputation=0.99,
            latency=1,
        )
    )

    matches = registry.find("web_scraping", available_agent_ids={"fast-online"})

    assert [match.id for match in matches] == ["fast-online", "high-reputation"]
    assert matches[0].availability is True
    assert matches[1].availability is False
