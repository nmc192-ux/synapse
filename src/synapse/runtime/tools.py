from __future__ import annotations

import asyncio
import importlib
import json
import os
import pkgutil
import sys
from pathlib import Path
from collections.abc import Awaitable, Callable

from synapse.models.plugin import PluginDescriptor, PluginExecutionMode, ToolDescriptor

ToolHandler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class ToolRegistry:
    def __init__(
        self,
        *,
        execution_mode: PluginExecutionMode = PluginExecutionMode.TRUSTED_LOCAL,
        execution_timeout_seconds: float = 10.0,
    ) -> None:
        self._tools: dict[str, ToolHandler] = {}
        self._tool_descriptors: dict[str, ToolDescriptor] = {}
        self._plugins: dict[str, PluginDescriptor] = {}
        self.execution_mode = execution_mode
        self.execution_timeout_seconds = execution_timeout_seconds

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
        )
        if plugin_name:
            if plugin is None:
                plugin = PluginDescriptor(
                    name=plugin_name,
                    module=plugin_name,
                    execution_mode=self.execution_mode,
                    isolation_strategy=self._default_isolation_strategy(self.execution_mode),
                    timeout_seconds=self.execution_timeout_seconds,
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
        )
        self._plugins[name] = descriptor
        return descriptor

    async def call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        descriptor = self._tool_descriptors.get(name)
        if descriptor is None:
            raise KeyError(f"Tool not found: {name}")
        plugin = self._plugins.get(descriptor.plugin) if descriptor.plugin else None
        if plugin is not None and plugin.execution_mode == PluginExecutionMode.ISOLATED_HOSTED:
            return await self._call_isolated(name, arguments, plugin)
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
    ) -> dict[str, object]:
        env = dict(os.environ)
        src_path = str(Path(__file__).resolve().parents[2])
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = src_path if not existing_pythonpath else f"{src_path}:{existing_pythonpath}"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "synapse.runtime.plugin_runner",
            plugin.module,
            name,
            json.dumps(arguments),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path(__file__).resolve().parents[3]),
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=plugin.timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise TimeoutError(
                f"Plugin tool timed out after {plugin.timeout_seconds:.1f}s: {name}"
            ) from None

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip() or "plugin subprocess failed"
            raise RuntimeError(f"Plugin tool failed: {name}: {detail}")
        payload = stdout.decode("utf-8", errors="replace").strip()
        if not payload:
            return {}
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise TypeError(f"Plugin tool returned non-object payload: {name}")
        return result

    @staticmethod
    def _default_isolation_strategy(mode: PluginExecutionMode) -> str:
        if mode == PluginExecutionMode.ISOLATED_HOSTED:
            return "subprocess"
        return "in_process"
