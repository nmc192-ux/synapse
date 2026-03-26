from __future__ import annotations

import asyncio
from types import SimpleNamespace

from synapse.models.a2a import AgentRegistrationRequest
from synapse.models.agent import AgentDefinition, AgentKind
from synapse.models.runtime_event import EventType, RuntimeEvent
from synapse.runtime.a2a import A2AHub
from synapse.runtime.budget import AgentBudgetManager
from synapse.runtime.event_bus import EventBus
from synapse.runtime.orchestrator import RuntimeOrchestrator
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from synapse.transports.websocket_manager import WebSocketManager
from tests.test_run_state import _StubBrowserService, _StubMemoryManager, _StubSafety, _StubTaskManager


def test_event_bus_blocks_external_events_without_tenant_context() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)

        async with sockets.subscribe("blocked-events") as queue:
            await bus.publish(
                RuntimeEvent(
                    event_type=EventType.TASK_UPDATED,
                    run_id="run-1",
                    source="test",
                    payload={"blocked": True},
                )
            )
            try:
                await asyncio.wait_for(queue.get(), timeout=0.05)
            except TimeoutError:
                pass
            else:
                raise AssertionError("expected tenant-less runtime event to be blocked")

        assert await store.get_runtime_events(run_id="run-1") == []

    asyncio.run(scenario())


def test_a2a_originated_events_are_published_with_tenant_context() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        sockets = WebSocketManager(state_store=store)
        bus = EventBus(sockets)
        registry = AgentRegistry(state_store=store)
        hub = A2AHub(registry, state_store=store, sockets=sockets, event_publisher=bus.publish)
        hub.register_agent(
            AgentRegistrationRequest(
                agent_id="agent-a",
                name="Agent A",
                organization_id="org-1",
                project_id="project-1",
            )
        )

        async with sockets.subscribe("a2a-events", organization_id="org-1", project_id="project-1") as queue:
            await hub.heartbeat("agent-a")
            received = [await queue.get(), await queue.get()]

        assert {event.event_type for event in received} == {
            EventType.AGENT_STATUS_UPDATED,
            EventType.CONNECTION_HEARTBEAT,
        }
        assert all(event.organization_id == "org-1" for event in received)
        assert all(event.project_id == "project-1" for event in received)

    asyncio.run(scenario())


def test_operator_intervention_events_are_project_scoped() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        agents = AgentRegistry(state_store=store)
        agent = agents.register(
            AgentDefinition(
                agent_id="agent-1",
                kind=AgentKind.CUSTOM,
                name="Agent One",
                organization_id="org-1",
                project_id="project-1",
            )
        )
        await agents.save_to_store(agent)
        orchestrator = RuntimeOrchestrator(
            browser=_StubBrowserService(),
            agents=agents,
            tools=SimpleNamespace(),
            messages=SimpleNamespace(),
            a2a=A2AHub(agents, state_store=store, sockets=WebSocketManager(state_store=store)),
            memory_manager=_StubMemoryManager(),
            task_manager=_StubTaskManager(),
            sockets=WebSocketManager(state_store=store),
            sandbox=SimpleNamespace(set_state_store=lambda store: None),
            safety=_StubSafety(),
            budget_manager=AgentBudgetManager(),
            state_store=store,
            llm=None,
        )
        run = await orchestrator.run_store.create_run(
            task_id="task-1",
            agent_id="agent-1",
            project_id="project-1",
            correlation_id="corr-1",
        )

        async with orchestrator.sockets.subscribe("interventions", organization_id="org-1", project_id="project-1") as queue:
            await orchestrator.event_bus.emit(
                EventType.APPROVAL_REQUIRED,
                organization_id="org-1",
                project_id="project-1",
                run_id=run.run_id,
                agent_id=run.agent_id,
                task_id=run.task_id,
                source="test",
                payload={"reason": "captcha"},
                correlation_id=run.correlation_id,
            )
            emitted = [await queue.get(), await queue.get(), await queue.get()]

        assert emitted[0].event_type == EventType.APPROVAL_REQUIRED
        assert EventType.CHECKPOINT_SAVED in {event.event_type for event in emitted}
        queued = next(event for event in emitted if event.event_type == EventType.INTERVENTION_QUEUED)
        assert queued.project_id == "project-1"

    asyncio.run(scenario())
