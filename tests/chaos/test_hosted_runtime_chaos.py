from __future__ import annotations

import asyncio
from pathlib import Path

from synapse.models.plugin import PluginExecutionMode
from synapse.runtime.tools import ToolRegistry
from synapse.runtime.state_store import InMemoryRuntimeStateStore
from tests.chaos.helpers import ChaosScenarioReport


def test_plugin_escape_attempts_are_blocked_in_hosted_mode() -> None:
    async def scenario() -> None:
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=1.0,
        )
        tools.load_module("synapse.testing.isolated_plugin")
        repo_file = Path(__file__).resolve().parents[2] / "README.md"

        blocked = 0
        for arguments in ({"network": True}, {"read_path": str(repo_file)}):
            try:
                await tools.call("isolated.echo", arguments)
            except RuntimeError:
                blocked += 1
        assert blocked == 2

        report = ChaosScenarioReport(
            scenario="hosted-plugin-escape-attempt",
            severity="critical",
            failure_mode="plugin tries network or filesystem escape",
            safe=True,
            recovered=True,
            manual_intervention_required=False,
            expected_behavior="hosted execution must fail closed and deny escape attempts",
            evidence={"blocked_attempts": blocked},
        )
        assert report.as_dict()["blocked_attempts"] if "blocked_attempts" in report.as_dict() else True

    asyncio.run(scenario())


def test_hosted_untrusted_plugin_denial_is_audited() -> None:
    async def scenario() -> None:
        store = InMemoryRuntimeStateStore()
        tools = ToolRegistry(
            execution_mode=PluginExecutionMode.ISOLATED_HOSTED,
            execution_timeout_seconds=1.0,
            state_store=store,
        )
        tools.register_plugin(
            name="external-tooling",
            module="external.plugin",
            capabilities=["echo"],
            endpoint="external",
        )
        tools.register("external.echo", lambda arguments: asyncio.sleep(0, result={"echo": arguments}), plugin_name="external-tooling")
        try:
            await tools.call("external.echo", {"value": "blocked"}, run_id="run-1", project_id="project-1")
        except PermissionError:
            pass
        else:
            raise AssertionError("expected hosted policy denial")
        audit_logs = await store.list_audit_logs(project_id="project-1", limit=10)
        assert any(
            entry.get("action") == "plugin.execution"
            and "hosted_policy_denied" in entry.get("metadata", {}).get("policy_violations", [])
            for entry in audit_logs
        )

    asyncio.run(scenario())
