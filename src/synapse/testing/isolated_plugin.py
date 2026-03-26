from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

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
        if arguments.get("print_stdout"):
            print("plugin stdout message")
        if arguments.get("print_stderr"):
            print("plugin stderr message", file=sys.stderr)
        if arguments.get("env_key"):
            return {"value": os.environ.get(str(arguments["env_key"]))}
        if arguments.get("touch_path"):
            Path(str(arguments["touch_path"])).write_text("sandbox")
            return {"touched": str(arguments["touch_path"])}
        if arguments.get("read_path"):
            return {"content": Path(str(arguments["read_path"])).read_text()}
        if arguments.get("network"):
            sock = socket.create_connection(("example.com", 80), timeout=0.1)
            sock.close()
            return {"network": "opened"}
        return {"echo": arguments.get("value"), "mode": "isolated"}

    registry.register(
        "isolated.echo",
        isolated_echo,
        description="Test plugin for subprocess isolation flows.",
        plugin_name="isolated_plugin",
    )
