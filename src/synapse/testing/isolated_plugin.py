from __future__ import annotations

import asyncio

from synapse.runtime.tools import ToolRegistry


def register(registry: ToolRegistry) -> None:
    registry.register_plugin(
        name="isolated_plugin",
        module=__name__,
        capabilities=["isolated_testing"],
        endpoint="isolated.echo",
    )

    async def isolated_echo(arguments: dict[str, object]) -> dict[str, object]:
        if arguments.get("sleep"):
            await asyncio.sleep(float(arguments["sleep"]))
        if arguments.get("fail"):
            raise RuntimeError("isolated failure")
        return {"echo": arguments.get("value"), "mode": "isolated"}

    registry.register(
        "isolated.echo",
        isolated_echo,
        description="Test plugin for subprocess isolation flows.",
        plugin_name="isolated_plugin",
    )
