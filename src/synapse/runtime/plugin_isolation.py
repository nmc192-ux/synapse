from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from synapse.models.plugin import PluginDescriptor
from synapse.runtime.plugin_sandbox import PluginSandboxConfig, build_sandbox_env


class HostedPluginIsolationUnavailableError(RuntimeError):
    pass


@dataclass(slots=True)
class PluginExecutionRequest:
    plugin: PluginDescriptor
    tool_name: str
    arguments: dict[str, object]
    timeout_seconds: float
    run_id: str | None = None
    project_id: str | None = None


@dataclass(slots=True)
class PluginExecutionResult:
    result: dict[str, object]
    start_time: datetime
    end_time: datetime
    exit_status: int
    stdout_ref: str
    stderr_ref: str
    stdout: str = ""
    stderr: str = ""
    policy_violations: list[str] = field(default_factory=list)
    isolation_strategy: str = "jailed_runner"


class HostedPluginIsolationBackend:
    def __init__(
        self,
        *,
        memory_limit_mb: int = 256,
        cpu_limit_seconds: int = 2,
        network_allowlist: tuple[str, ...] = (),
    ) -> None:
        self.memory_limit_mb = memory_limit_mb
        self.cpu_limit_seconds = cpu_limit_seconds
        self.network_allowlist = tuple(host for host in network_allowlist if host)

    @staticmethod
    def is_available() -> bool:
        return os.name == "posix" and shutil.which(Path(sys.executable).name) is not None

    async def execute(self, request: PluginExecutionRequest) -> PluginExecutionResult:
        if not self.is_available():
            raise HostedPluginIsolationUnavailableError("Hosted plugin isolation backend is not available.")
        src_path = str(Path(__file__).resolve().parents[2])
        sandbox_root = tempfile.mkdtemp(prefix="synapse-plugin-hosted-")
        stdout_ref = str(Path(sandbox_root) / "plugin.stdout.log")
        stderr_ref = str(Path(sandbox_root) / "plugin.stderr.log")
        config = PluginSandboxConfig(
            plugin_module=request.plugin.module,
            tool_name=request.tool_name,
            timeout_seconds=request.timeout_seconds,
            memory_limit_mb=self.memory_limit_mb,
            cpu_limit_seconds=self.cpu_limit_seconds,
            allowed_network_hosts=self.network_allowlist,
            allowed_read_roots=(src_path,),
            allowed_write_roots=(sandbox_root,),
            sandbox_root=sandbox_root,
        )
        env = build_sandbox_env({}, config)
        env["PYTHONPATH"] = src_path
        env["TMPDIR"] = sandbox_root
        env["TEMP"] = sandbox_root
        env["TMP"] = sandbox_root

        start_time = datetime.now(timezone.utc)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-m",
            "synapse.runtime.plugin_runner",
            request.plugin.module,
            request.tool_name,
            json.dumps(request.arguments),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=sandbox_root,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=request.timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            Path(stdout_ref).write_text("", encoding="utf-8")
            Path(stderr_ref).write_text("timeout", encoding="utf-8")
            raise TimeoutError(f"Plugin tool timed out after {request.timeout_seconds:.1f}s: {request.tool_name}") from None

        end_time = datetime.now(timezone.utc)
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        Path(stdout_ref).write_text(stdout_text, encoding="utf-8")
        Path(stderr_ref).write_text(stderr_text, encoding="utf-8")

        if process.returncode != 0:
            violations = self._extract_policy_violations(stdout_text, stderr_text)
            detail = stderr_text.strip() or "plugin runner failed"
            raise RuntimeError(detail if not violations else f"{detail} | violations: {', '.join(violations)}")

        envelope = json.loads(stdout_text.strip() or "{}")
        result = envelope.get("result", {})
        if not isinstance(result, dict):
            raise TypeError(f"Plugin tool returned non-object payload: {request.tool_name}")
        violations = self._extract_policy_violations(
            str(envelope.get("stdout", "")),
            str(envelope.get("stderr", "")),
        )
        return PluginExecutionResult(
            result=result,
            start_time=start_time,
            end_time=end_time,
            exit_status=process.returncode or 0,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
            stdout=str(envelope.get("stdout", "")),
            stderr=str(envelope.get("stderr", "")),
            policy_violations=violations,
        )

    @staticmethod
    def _extract_policy_violations(stdout: str, stderr: str) -> list[str]:
        text = f"{stdout}\n{stderr}".lower()
        violations: list[str] = []
        if "cannot read" in text or "cannot write" in text:
            violations.append("filesystem")
        if "cannot open network connections" in text or "cannot connect" in text:
            violations.append("network")
        if "cannot spawn" in text or "cannot execute subprocesses" in text:
            violations.append("process")
        return sorted(set(violations))
