from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys

from synapse.models.plugin import PluginExecutionMode
from synapse.runtime.plugin_sandbox import configure_process_sandbox
from synapse.runtime.tools import ToolRegistry


async def _run(module_name: str, tool_name: str, payload: str) -> int:
    configure_process_sandbox()
    registry = ToolRegistry(execution_mode=PluginExecutionMode.TRUSTED_LOCAL)
    registry.load_module(module_name)
    arguments = json.loads(payload)
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
        result = await registry.call(tool_name, arguments)
    sys.stdout.write(
        json.dumps(
            {
                "plugin_module": module_name,
                "tool_name": tool_name,
                "result": result,
                "stdout": captured_stdout.getvalue(),
                "stderr": captured_stderr.getvalue(),
            }
        )
    )
    sys.stdout.flush()
    return 0


def main() -> int:
    if len(sys.argv) != 4:
        sys.stderr.write("usage: python -m synapse.runtime.plugin_runner <module> <tool> <json-arguments>\n")
        return 2
    module_name, tool_name, payload = sys.argv[1:4]
    try:
        return asyncio.run(_run(module_name, tool_name, payload))
    except PermissionError as exc:  # pragma: no cover - defensive CLI path
        sys.stderr.write(f"policy_violation:{type(exc).__name__}: {exc}\n")
        return 1
    except Exception as exc:  # pragma: no cover - defensive CLI path
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
