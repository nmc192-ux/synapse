from __future__ import annotations

import abc
import asyncio
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

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
    timeout: bool = False
    policy_violations: list[str] = field(default_factory=list)
    isolation_strategy: str = "jailed_runner"


class PluginIsolationBackend(abc.ABC):
    isolation_strategy = "hosted_backend"

    @classmethod
    @abc.abstractmethod
    def is_available(cls) -> bool:
        raise NotImplementedError

    @classmethod
    def supports_untrusted_plugins(cls) -> bool:
        return True

    @abc.abstractmethod
    async def execute(self, request: PluginExecutionRequest) -> PluginExecutionResult:
        raise NotImplementedError


class ProcessGuardIsolationBackend(PluginIsolationBackend):
    isolation_strategy = "process_guard_runner"

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

    @classmethod
    def is_available(cls) -> bool:
        return os.name == "posix" and shutil.which(Path(sys.executable).name) is not None

    @classmethod
    def supports_untrusted_plugins(cls) -> bool:
        return False

    async def execute(self, request: PluginExecutionRequest) -> PluginExecutionResult:
        if not self.is_available():
            raise HostedPluginIsolationUnavailableError("Hosted plugin isolation backend is not available.")
        repo_root = Path(__file__).resolve().parents[3]
        src_path = str(repo_root / "src")
        agent_limits_path = str(repo_root / "config" / "agent_limits.yaml")
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
            allowed_read_roots=(src_path, str(repo_root / "config")),
            allowed_write_roots=(sandbox_root,),
            sandbox_root=sandbox_root,
        )
        env = build_sandbox_env({}, config)
        env["PYTHONPATH"] = src_path
        env["SYNAPSE_AGENT_LIMITS_CONFIG_PATH"] = agent_limits_path
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
            isolation_strategy=self.isolation_strategy,
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


class SandboxExecIsolationBackend(PluginIsolationBackend):
    isolation_strategy = "sandbox_exec"

    def __init__(
        self,
        *,
        memory_limit_mb: int = 256,
        cpu_limit_seconds: int = 2,
        network_allowlist: Sequence[str] = (),
    ) -> None:
        self.memory_limit_mb = memory_limit_mb
        self.cpu_limit_seconds = cpu_limit_seconds
        self.network_allowlist = tuple(str(host) for host in network_allowlist if host)
        if self.network_allowlist:
            raise HostedPluginIsolationUnavailableError(
                "sandbox-exec backend does not support network allowlists; hosted plugins run with no network access."
            )

    @classmethod
    def is_available(cls) -> bool:
        return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None

    async def execute(self, request: PluginExecutionRequest) -> PluginExecutionResult:
        sandbox_root, config, env, stdout_ref, stderr_ref = _build_isolation_environment(
            request,
            memory_limit_mb=self.memory_limit_mb,
            cpu_limit_seconds=self.cpu_limit_seconds,
            network_allowlist=self.network_allowlist,
        )
        profile_path = Path(sandbox_root) / "sandbox.sb"
        profile_path.write_text(_sandbox_exec_profile(sandbox_root), encoding="utf-8")
        return await _run_isolated_process(
            request,
            [
                "sandbox-exec",
                "-f",
                str(profile_path),
                sys.executable,
                "-u",
                "-m",
                "synapse.runtime.plugin_runner",
                request.plugin.module,
                request.tool_name,
                json.dumps(request.arguments),
            ],
            env=env,
            cwd=sandbox_root,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
            isolation_strategy=self.isolation_strategy,
        )


class BubblewrapIsolationBackend(PluginIsolationBackend):
    isolation_strategy = "bubblewrap"

    def __init__(
        self,
        *,
        memory_limit_mb: int = 256,
        cpu_limit_seconds: int = 2,
        network_allowlist: Sequence[str] = (),
    ) -> None:
        self.memory_limit_mb = memory_limit_mb
        self.cpu_limit_seconds = cpu_limit_seconds
        self.network_allowlist = tuple(str(host) for host in network_allowlist if host)
        if self.network_allowlist:
            raise HostedPluginIsolationUnavailableError(
                "bubblewrap backend only supports no-network hosted execution in this phase."
            )

    @classmethod
    def is_available(cls) -> bool:
        return sys.platform.startswith("linux") and shutil.which("bwrap") is not None

    async def execute(self, request: PluginExecutionRequest) -> PluginExecutionResult:
        sandbox_root, config, env, stdout_ref, stderr_ref = _build_isolation_environment(
            request,
            memory_limit_mb=self.memory_limit_mb,
            cpu_limit_seconds=self.cpu_limit_seconds,
            network_allowlist=self.network_allowlist,
        )
        command = [
            "bwrap",
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--unshare-net",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--ro-bind",
            sys.prefix,
            sys.prefix,
            "--ro-bind",
            str(Path(__file__).resolve().parents[3] / "src"),
            str(Path(__file__).resolve().parents[3] / "src"),
            "--dir",
            sandbox_root,
            "--chdir",
            sandbox_root,
            "--setenv",
            "PYTHONPATH",
            str(Path(__file__).resolve().parents[3] / "src"),
            "--setenv",
            "TMPDIR",
            sandbox_root,
            sys.executable,
            "-u",
            "-m",
            "synapse.runtime.plugin_runner",
            request.plugin.module,
            request.tool_name,
            json.dumps(request.arguments),
        ]
        return await _run_isolated_process(
            request,
            command,
            env=env,
            cwd=sandbox_root,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
            isolation_strategy=self.isolation_strategy,
        )


class HostedPluginIsolationBackend:
    def __init__(
        self,
        *,
        backend_name: str | None = None,
        memory_limit_mb: int = 256,
        cpu_limit_seconds: int = 2,
        network_allowlist: Sequence[str] = (),
    ) -> None:
        self.backend_name = backend_name or "auto"
        self.memory_limit_mb = memory_limit_mb
        self.cpu_limit_seconds = cpu_limit_seconds
        self.network_allowlist = tuple(str(host) for host in network_allowlist if host)
        self._backend = self._select_backend()

    @property
    def isolation_strategy(self) -> str:
        if self._backend is None:
            return "unavailable"
        return self._backend.isolation_strategy

    def _select_backend(self) -> PluginIsolationBackend | None:
        candidates: list[type[PluginIsolationBackend]]
        if self.backend_name == "auto":
            candidates = [SandboxExecIsolationBackend, BubblewrapIsolationBackend]
        elif self.backend_name == "sandbox_exec":
            candidates = [SandboxExecIsolationBackend]
        elif self.backend_name == "bubblewrap":
            candidates = [BubblewrapIsolationBackend]
        else:
            candidates = []
        for candidate in candidates:
            if candidate.is_available():
                return candidate(
                    memory_limit_mb=self.memory_limit_mb,
                    cpu_limit_seconds=self.cpu_limit_seconds,
                    network_allowlist=self.network_allowlist,
                )
        return None

    def is_available(self) -> bool:
        return self._backend is not None

    def supports_untrusted_plugins(self) -> bool:
        if self._backend is None:
            return False
        return self._backend.supports_untrusted_plugins()

    async def execute(self, request: PluginExecutionRequest) -> PluginExecutionResult:
        if self._backend is None:
            raise HostedPluginIsolationUnavailableError("Hosted plugin isolation backend is not available.")
        return await self._backend.execute(request)


def _build_isolation_environment(
    request: PluginExecutionRequest,
    *,
    memory_limit_mb: int,
    cpu_limit_seconds: int,
    network_allowlist: Sequence[str],
) -> tuple[str, PluginSandboxConfig, dict[str, str], str, str]:
    repo_root = Path(__file__).resolve().parents[3]
    src_path = str(repo_root / "src")
    sandbox_root = tempfile.mkdtemp(prefix="synapse-plugin-hosted-")
    stdout_ref = str(Path(sandbox_root) / "plugin.stdout.log")
    stderr_ref = str(Path(sandbox_root) / "plugin.stderr.log")
    config = PluginSandboxConfig(
        plugin_module=request.plugin.module,
        tool_name=request.tool_name,
        timeout_seconds=request.timeout_seconds,
        memory_limit_mb=memory_limit_mb,
        cpu_limit_seconds=cpu_limit_seconds,
        allowed_network_hosts=tuple(network_allowlist),
        allowed_read_roots=(src_path,),
        allowed_write_roots=(sandbox_root,),
        sandbox_root=sandbox_root,
    )
    env = build_sandbox_env({}, config)
    env["PYTHONPATH"] = src_path
    env["TMPDIR"] = sandbox_root
    env["TEMP"] = sandbox_root
    env["TMP"] = sandbox_root
    return sandbox_root, config, env, stdout_ref, stderr_ref


async def _run_isolated_process(
    request: PluginExecutionRequest,
    command: list[str],
    *,
    env: dict[str, str],
    cwd: str,
    stdout_ref: str,
    stderr_ref: str,
    isolation_strategy: str,
) -> PluginExecutionResult:
    start_time = datetime.now(timezone.utc)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
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
        violations = ProcessGuardIsolationBackend._extract_policy_violations(stdout_text, stderr_text)
        detail = stderr_text.strip() or "plugin runner failed"
        raise RuntimeError(detail if not violations else f"{detail} | violations: {', '.join(violations)}")

    envelope = json.loads(stdout_text.strip() or "{}")
    result = envelope.get("result", {})
    if not isinstance(result, dict):
        raise TypeError(f"Plugin tool returned non-object payload: {request.tool_name}")
    violations = ProcessGuardIsolationBackend._extract_policy_violations(
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
        timeout=False,
        policy_violations=violations,
        isolation_strategy=isolation_strategy,
    )


def _sandbox_exec_profile(sandbox_root: str) -> str:
    src_root = str(Path(__file__).resolve().parents[3] / "src")
    base_prefix = sys.base_prefix
    executable = sys.executable
    return "\n".join(
        [
            "(version 1)",
            "(deny default)",
            '(import "system.sb")',
            f'(allow process-exec (literal "{executable}"))',
            "(allow process-fork)",
            f'(allow file-read* (subpath "/System") (subpath "/usr") (subpath "{base_prefix}") (subpath "{src_root}") (subpath "{sandbox_root}"))',
            f'(allow file-write* (subpath "{sandbox_root}"))',
            f'(allow file-read-metadata (subpath "{sandbox_root}"))',
            "(deny network*)",
        ]
    )
