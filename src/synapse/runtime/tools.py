from __future__ import annotations

import importlib
import pkgutil
import uuid
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from synapse.config import settings
from synapse.models.plugin import PluginDescriptor, PluginExecutionMode, PluginTrustLevel, ToolDescriptor
from synapse.runtime.plugin_isolation import (
    HostedPluginIsolationBackend,
    HostedPluginIsolationUnavailableError,
    PluginExecutionRequest,
)

if TYPE_CHECKING:
    from synapse.runtime.state_store import RuntimeStateStore

ToolHandler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class ToolRegistry:
    def __init__(
        self,
        *,
        execution_mode: PluginExecutionMode = PluginExecutionMode.TRUSTED_LOCAL,
        execution_timeout_seconds: float = 10.0,
        state_store: RuntimeStateStore | None = None,
        isolation_backend: HostedPluginIsolationBackend | None = None,
        hosted_partner_allowlist: list[str] | None = None,
    ) -> None:
        self._tools: dict[str, ToolHandler] = {}
        self._tool_descriptors: dict[str, ToolDescriptor] = {}
        self._plugins: dict[str, PluginDescriptor] = {}
        self._plugin_audit_logs: list[dict[str, object]] = []
        self.execution_mode = execution_mode
        self.execution_timeout_seconds = execution_timeout_seconds
        self.state_store = state_store
        self.isolation_backend = isolation_backend or HostedPluginIsolationBackend(
            backend_name=settings.hosted_plugin_isolation_backend,
            memory_limit_mb=settings.hosted_plugin_memory_limit_mb,
            cpu_limit_seconds=settings.hosted_plugin_cpu_limit_seconds,
            network_allowlist=settings.hosted_plugin_network_allowlist,
        )
        self.hosted_partner_allowlist = set(hosted_partner_allowlist or settings.hosted_plugin_partner_allowlist)
        self._hosted_isolation_strategy = self.isolation_backend.isolation_strategy

    def set_state_store(self, state_store: RuntimeStateStore | None) -> None:
        self.state_store = state_store

    def register(
        self,
        name: str,
        handler: ToolHandler,
        description: str = "",
        plugin_name: str | None = None,
    ) -> None:
        plugin = self._plugins.get(plugin_name) if plugin_name else None
        self._tools[name] = handler
        self._tool_descriptors[name] = ToolDescriptor(
            name=name,
            description=description,
            plugin=plugin_name,
            capabilities=list(plugin.capabilities) if plugin else [],
            endpoint=plugin.endpoint if plugin else None,
            execution_mode=plugin.execution_mode if plugin else self.execution_mode,
            isolation_strategy=plugin.isolation_strategy if plugin else self._default_isolation_strategy(self.execution_mode),
            trust_level=plugin.trust_level if plugin else PluginTrustLevel.TRUSTED_INTERNAL,
        )
        if plugin_name:
            if plugin is None:
                plugin = PluginDescriptor(
                    name=plugin_name,
                    module=plugin_name,
                    execution_mode=self.execution_mode,
                    isolation_strategy=self._default_isolation_strategy(self.execution_mode),
                    timeout_seconds=self.execution_timeout_seconds,
                    trust_level=self._classify_plugin(plugin_name, plugin_name),
                )
                self._plugins[plugin_name] = plugin
            if name not in plugin.tools:
                plugin.tools.append(name)

    def register_plugin(
        self,
        name: str,
        module: str,
        capabilities: list[str],
        endpoint: str,
    ) -> PluginDescriptor:
        descriptor = PluginDescriptor(
            name=name,
            module=module,
            capabilities=capabilities,
            endpoint=endpoint,
            tools=[],
            execution_mode=self.execution_mode,
            isolation_strategy=self._default_isolation_strategy(self.execution_mode),
            timeout_seconds=self.execution_timeout_seconds,
            trust_level=self._classify_plugin(name, module),
        )
        self._plugins[name] = descriptor
        return descriptor

    async def call(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        run_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, object]:
        descriptor = self._tool_descriptors.get(name)
        if descriptor is None:
            raise KeyError(f"Tool not found: {name}")
        plugin = self._plugins.get(descriptor.plugin) if descriptor.plugin else None
        if plugin is not None and plugin.execution_mode == PluginExecutionMode.ISOLATED_HOSTED:
            return await self._call_isolated(name, arguments, plugin, run_id=run_id, project_id=project_id)
        handler = self._tools.get(name)
        if handler is None:
            raise KeyError(f"Tool not found: {name}")
        return await handler(arguments)

    def list_tools(self) -> list[ToolDescriptor]:
        return [self._tool_descriptors[name] for name in sorted(self._tool_descriptors)]

    def describe(self, name: str) -> ToolDescriptor:
        descriptor = self._tool_descriptors.get(name)
        if descriptor is None:
            raise KeyError(f"Tool not found: {name}")
        return descriptor

    def list_plugins(self) -> list[PluginDescriptor]:
        return [self._plugins[name] for name in sorted(self._plugins)]

    def list_plugin_audit_logs(self, limit: int = 100) -> list[dict[str, object]]:
        return [dict(item) for item in self._plugin_audit_logs[-limit:]]

    def load_package(self, package_name: str) -> list[str]:
        package = importlib.import_module(package_name)
        loaded: list[str] = []
        for module_info in pkgutil.iter_modules(package.__path__, prefix=f"{package_name}."):
            loaded.append(self.load_module(module_info.name))
        return loaded

    def load_module(self, module_name: str) -> str:
        plugin_name = module_name.rsplit(".", 1)[-1]
        self._remove_plugin_tools(plugin_name)

        if module_name in importlib.sys.modules:
            module = importlib.reload(importlib.sys.modules[module_name])
        else:
            module = importlib.import_module(module_name)

        register = getattr(module, "register", None)
        if register is None:
            raise ValueError(f"Plugin module does not export register(): {module_name}")

        self._plugins[plugin_name] = PluginDescriptor(
            name=plugin_name,
            module=module_name,
            execution_mode=self.execution_mode,
            isolation_strategy=self._default_isolation_strategy(self.execution_mode),
            timeout_seconds=self.execution_timeout_seconds,
            trust_level=self._classify_plugin(plugin_name, module_name),
        )
        register(self)
        plugin = self._plugins[plugin_name]
        plugin.module = module_name
        plugin.tools = sorted({name for name, descriptor in self._tool_descriptors.items() if descriptor.plugin == plugin_name})
        for tool_name in plugin.tools:
            descriptor = self._tool_descriptors[tool_name]
            descriptor.capabilities = list(plugin.capabilities)
            descriptor.endpoint = plugin.endpoint
            descriptor.execution_mode = plugin.execution_mode
            descriptor.isolation_strategy = plugin.isolation_strategy
            descriptor.trust_level = plugin.trust_level
        return plugin_name

    def load_plugins(self, package_names: list[str] | None = None, module_names: list[str] | None = None) -> list[str]:
        loaded: list[str] = []
        for package_name in package_names or []:
            loaded.extend(self.load_package(package_name))
        for module_name in module_names or []:
            loaded.append(self.load_module(module_name))
        return loaded

    def _remove_plugin_tools(self, plugin_name: str) -> None:
        stale_tools = [
            name
            for name, descriptor in self._tool_descriptors.items()
            if descriptor.plugin == plugin_name
        ]
        for name in stale_tools:
            self._tools.pop(name, None)
            self._tool_descriptors.pop(name, None)

    async def _call_isolated(
        self,
        name: str,
        arguments: dict[str, object],
        plugin: PluginDescriptor,
        *,
        run_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, object]:
        resolved_project_id = project_id or await self._project_id_for_run(run_id)
        if not self._hosted_execution_allowed(plugin):
            now = datetime.now(timezone.utc)
            await self._append_audit_log(
                plugin=plugin,
                tool_name=name,
                arguments=arguments,
                run_id=run_id,
                project_id=resolved_project_id,
                stdout="",
                stderr="hosted policy denied plugin execution",
                exit_status=-1,
                status="denied",
                start_time=now,
                end_time=now,
                isolation_mode=self.isolation_backend.isolation_strategy,
                stdout_ref=None,
                stderr_ref=None,
                timeout=False,
                policy_violations=["hosted_policy_denied", plugin.trust_level.value],
            )
            raise PermissionError(
                f"Hosted plugin execution denied for {plugin.name} ({plugin.trust_level.value})."
            )
        if not self.isolation_backend.is_available():
            await self._append_audit_log(
                plugin=plugin,
                tool_name=name,
                arguments=arguments,
                run_id=run_id,
                project_id=resolved_project_id,
                stdout="",
                stderr="hosted isolation backend unavailable",
                exit_status=-1,
                status="rejected",
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                isolation_mode=self.isolation_backend.isolation_strategy,
                stdout_ref=None,
                stderr_ref=None,
                timeout=False,
                policy_violations=["backend_unavailable"],
            )
            raise HostedPluginIsolationUnavailableError(
                f"Hosted plugin isolation backend unavailable; rejecting plugin '{plugin.name}'."
            )
        started_at = datetime.now(timezone.utc)
        request = PluginExecutionRequest(
            plugin=plugin,
            tool_name=name,
            arguments=arguments,
            timeout_seconds=plugin.timeout_seconds,
            run_id=run_id,
            project_id=resolved_project_id,
        )
        try:
            execution = await self.isolation_backend.execute(request)
        except Exception as exc:
            ended_at = datetime.now(timezone.utc)
            await self._append_audit_log(
                plugin=plugin,
                tool_name=name,
                arguments=arguments,
                run_id=run_id,
                project_id=resolved_project_id,
                stdout="",
                stderr=str(exc),
                exit_status=-1,
                status="failed",
                start_time=started_at,
                end_time=ended_at,
                isolation_mode=self.isolation_backend.isolation_strategy,
                stdout_ref=None,
                stderr_ref=None,
                timeout=isinstance(exc, TimeoutError),
                policy_violations=self._policy_violations_from_error(str(exc)),
            )
            raise
        await self._append_audit_log(
            plugin=plugin,
            tool_name=name,
            arguments=arguments,
            run_id=run_id,
            project_id=resolved_project_id,
            stdout=execution.stdout,
            stderr=execution.stderr,
            exit_status=execution.exit_status,
            status="ok",
            start_time=execution.start_time,
            end_time=execution.end_time,
            isolation_mode=execution.isolation_strategy,
            stdout_ref=execution.stdout_ref,
            stderr_ref=execution.stderr_ref,
            timeout=execution.timeout,
            policy_violations=execution.policy_violations,
        )
        return execution.result

    async def _append_audit_log(
        self,
        *,
        plugin: PluginDescriptor,
        tool_name: str,
        arguments: dict[str, object],
        run_id: str | None,
        project_id: str | None,
        stdout: str,
        stderr: str,
        exit_status: int,
        status: str,
        start_time: datetime,
        end_time: datetime,
        isolation_mode: str,
        stdout_ref: str | None,
        stderr_ref: str | None,
        timeout: bool,
        policy_violations: list[str],
    ) -> None:
        entry = {
            "plugin_name": plugin.name,
            "plugin_module": plugin.module,
            "tool_name": tool_name,
            "run_id": run_id,
            "project_id": project_id,
            "mode": plugin.execution_mode.value,
            "execution_mode": plugin.execution_mode.value,
            "isolation_mode": isolation_mode,
            "isolation_strategy": isolation_mode,
            "trust_level": plugin.trust_level.value,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "status": status,
            "exit_status": exit_status,
            "timeout": timeout,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_ref": stdout_ref,
            "stderr_ref": stderr_ref,
            "policy_violations": list(policy_violations),
            "argument_keys": sorted(arguments.keys()),
        }
        self._plugin_audit_logs.append(entry)
        if len(self._plugin_audit_logs) > 1000:
            self._plugin_audit_logs = self._plugin_audit_logs[-1000:]
        if self.state_store is not None:
            await self.state_store.store_audit_log(
                str(uuid.uuid4()),
                {
                    "actor_id": plugin.name,
                    "actor_type": "plugin",
                    "action": "plugin.execution",
                    "resource_type": "plugin",
                    "resource_id": plugin.name,
                    "project_id": project_id,
                    "timestamp": end_time.isoformat(),
                    "metadata": entry,
                },
            )

    def _default_isolation_strategy(self, mode: PluginExecutionMode) -> str:
        if mode == PluginExecutionMode.ISOLATED_HOSTED:
            return self._hosted_isolation_strategy
        return "in_process"

    def _classify_plugin(self, name: str, module: str) -> PluginTrustLevel:
        normalized = {name, module}
        if any(item in self.hosted_partner_allowlist for item in normalized):
            return PluginTrustLevel.TRUSTED_PARTNER
        if module == "synapse.main" or module.startswith("synapse."):
            return PluginTrustLevel.TRUSTED_INTERNAL
        return PluginTrustLevel.UNTRUSTED_EXTERNAL

    def _hosted_execution_allowed(self, plugin: PluginDescriptor) -> bool:
        if plugin.trust_level in {
            PluginTrustLevel.TRUSTED_INTERNAL,
            PluginTrustLevel.TRUSTED_PARTNER,
        }:
            return True
        return (
            settings.hosted_plugin_allow_untrusted_external
            and self.isolation_backend.supports_untrusted_plugins()
        )

    async def _project_id_for_run(self, run_id: str | None) -> str | None:
        if run_id is None or self.state_store is None:
            return None
        run = await self.state_store.get_run(run_id)
        if run is None:
            return None
        project_id = run.get("project_id")
        return str(project_id) if isinstance(project_id, str) and project_id else None

    @staticmethod
    def _policy_violations_from_error(error: str) -> list[str]:
        lowered = error.lower()
        violations: list[str] = []
        if "network" in lowered:
            violations.append("network")
        if "cannot read" in lowered or "cannot write" in lowered:
            violations.append("filesystem")
        if "subprocess" in lowered or "spawn" in lowered:
            violations.append("process")
        if "timeout" in lowered:
            violations.append("timeout")
        if "backend unavailable" in lowered:
            violations.append("backend_unavailable")
        return violations
