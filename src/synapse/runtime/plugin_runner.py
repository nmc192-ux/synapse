from __future__ import annotations

import asyncio
import json
import sys

from synapse.models.plugin import PluginExecutionMode
from synapse.runtime.tools import ToolRegistry


async def _run(module_name: str, tool_name: str, payload: str) -> int:
    registry = ToolRegistry(execution_mode=PluginExecutionMode.TRUSTED_LOCAL)
    registry.load_module(module_name)
    arguments = json.loads(payload)
    result = await registry.call(tool_name, arguments)
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    return 0


def main() -> int:
    if len(sys.argv) != 4:
        sys.stderr.write("usage: python -m synapse.runtime.plugin_runner <module> <tool> <json-arguments>\n")
        return 2
    module_name, tool_name, payload = sys.argv[1:4]
    try:
        return asyncio.run(_run(module_name, tool_name, payload))
    except Exception as exc:  # pragma: no cover - defensive CLI path
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
