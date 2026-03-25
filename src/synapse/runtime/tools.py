import importlib
import pkgutil
from collections.abc import Awaitable, Callable

from synapse.models.plugin import PluginDescriptor, ToolDescriptor

ToolHandler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolHandler] = {}
        self._tool_descriptors: dict[str, ToolDescriptor] = {}
        self._plugins: dict[str, PluginDescriptor] = {}

    def register(
        self,
        name: str,
        handler: ToolHandler,
        description: str = "",
        plugin_name: str | None = None,
    ) -> None:
        self._tools[name] = handler
        self._tool_descriptors[name] = ToolDescriptor(
            name=name,
            description=description,
            plugin=plugin_name,
        )
        if plugin_name:
            plugin = self._plugins.get(plugin_name)
            if plugin is None:
                plugin = PluginDescriptor(name=plugin_name, module=plugin_name)
                self._plugins[plugin_name] = plugin
            if name not in plugin.tools:
                plugin.tools.append(name)

    async def call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        handler = self._tools.get(name)
        if handler is None:
            raise KeyError(f"Tool not found: {name}")
        return await handler(arguments)

    def list_tools(self) -> list[ToolDescriptor]:
        return [self._tool_descriptors[name] for name in sorted(self._tool_descriptors)]

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

        self._plugins[plugin_name] = PluginDescriptor(name=plugin_name, module=module_name)
        register(self)
        plugin = self._plugins[plugin_name]
        plugin.module = module_name
        plugin.tools = sorted({name for name, descriptor in self._tool_descriptors.items() if descriptor.plugin == plugin_name})
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
