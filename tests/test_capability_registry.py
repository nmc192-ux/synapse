import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapse.api.routes import get_authenticator, get_orchestrator, router
from synapse.config import Settings
from synapse.models.capability import CapabilityAdvertisementRequest, CapabilityRecord
from synapse.runtime.capabilities import CapabilityRegistry
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.security.auth import Authenticator
from synapse.security.policies import PrincipalType, Scope


def test_capability_registry_persists_and_ranks_results() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        registry = AgentRegistry(state_store=store)
        capabilities = CapabilityRegistry(registry)

        await capabilities.advertise(
            CapabilityAdvertisementRequest(
                agent_id="fast-online",
                capabilities=["web_scraping"],
                description="Fast scraper",
                endpoint="ws://fast",
                latency=20,
                availability=True,
                reputation=0.6,
            )
        )
        await capabilities.advertise(
            CapabilityAdvertisementRequest(
                agent_id="high-reputation",
                capabilities=["web_scraping"],
                description="Trusted scraper",
                endpoint="ws://trusted",
                latency=120,
                availability=False,
                reputation=0.95,
            )
        )

        rows = await registry.list_persisted_agents()
        assert {row["agent_id"] for row in rows} == {"fast-online", "high-reputation"}

        ranked = await capabilities.find("web_scraping")
        assert [entry.id for entry in ranked] == ["high-reputation", "fast-online"]
        assert ranked[0].availability is False
        assert ranked[1].availability is True

    asyncio.run(scenario())


class _CapabilityOrchestrator:
    def __init__(self) -> None:
        self.records: list[CapabilityRecord] = []

    async def advertise_capabilities(self, request: CapabilityAdvertisementRequest) -> CapabilityRecord:
        record = CapabilityRecord(
            agent_id=request.agent_id,
            capabilities=request.capabilities,
            description=request.description,
            endpoint=request.endpoint,
            latency=request.latency,
            availability=request.availability,
            reputation=request.reputation,
            metadata=request.metadata,
        )
        self.records.append(record)
        return record

    async def list_capabilities(self) -> list[CapabilityRecord]:
        return self.records

    async def find_agents(self, capability: str) -> list[dict[str, object]]:
        assert capability == "web_scraping"
        return []


def test_capability_api_routes() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    settings = Settings(
        auth_required=True,
        jwt_secret="test-secret",
        jwt_issuer="synapse-test",
        jwt_audience="synapse-test-api",
    )
    authenticator = Authenticator(settings)
    orchestrator = _CapabilityOrchestrator()
    app.dependency_overrides[get_authenticator] = lambda: authenticator
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    client = TestClient(app)

    token = authenticator.issue_token(
        subject="operator-1",
        principal_type=PrincipalType.OPERATOR,
        scopes=[Scope.ADMIN.value, Scope.TASKS_READ.value],
    )
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/api/agents/capabilities",
        json={
            "agent_id": "agent-1",
            "capabilities": ["web_scraping"],
            "description": "Can scrape websites",
            "endpoint": "ws://agent-1",
            "latency": 12,
            "availability": True,
            "reputation": 0.8,
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["agent_id"] == "agent-1"

    list_response = client.get("/api/agents/capabilities", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()[0]["capabilities"] == ["web_scraping"]
