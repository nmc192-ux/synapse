from fastapi.testclient import TestClient

from synapse.main import app
from synapse.models.a2a import A2AEnvelope, A2AMessageType
from synapse.models.browser import BrowserState, PageData
from synapse.models.loop import AgentAction, AgentActionType
from synapse.models.plugin import ToolDescriptor
from synapse.models.task import TaskRequest


def test_healthcheck() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_browser_state_serialization() -> None:
    state = BrowserState(
        session_id="session-1",
        page=PageData(
            url="https://example.com",
            title="Example",
            text_excerpt="Structured page summary",
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


def test_tools_endpoint_returns_descriptors() -> None:
    client = TestClient(app)
    response = client.get("/api/tools")
    assert response.status_code == 200
    tools = [ToolDescriptor.model_validate(item) for item in response.json()]
    assert any(tool.name == "github.search" for tool in tools)
