from __future__ import annotations

import builtins
import io
import json
import os
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class PluginSandboxConfig:
    plugin_module: str
    tool_name: str
    timeout_seconds: float
    memory_limit_mb: int = 256
    cpu_limit_seconds: int = 2
    allowed_network_hosts: tuple[str, ...] = ()
    allowed_env_keys: tuple[str, ...] = ("PATH", "PYTHONPATH", "LANG", "LC_ALL")
    allowed_read_roots: tuple[str, ...] = ()
    allowed_write_roots: tuple[str, ...] = ()
    sandbox_root: str | None = None

    def to_env(self) -> dict[str, str]:
        return {
            "SYNAPSE_SANDBOX_CONFIG": json.dumps(
                {
                    "plugin_module": self.plugin_module,
                    "tool_name": self.tool_name,
                    "timeout_seconds": self.timeout_seconds,
                    "memory_limit_mb": self.memory_limit_mb,
                    "cpu_limit_seconds": self.cpu_limit_seconds,
                    "allowed_network_hosts": list(self.allowed_network_hosts),
                    "allowed_read_roots": list(self.allowed_read_roots),
                    "allowed_write_roots": list(self.allowed_write_roots),
                    "sandbox_root": self.sandbox_root,
                }
            )
        }

    @classmethod
    def from_env(cls) -> "PluginSandboxConfig | None":
        payload = os.environ.get("SYNAPSE_SANDBOX_CONFIG")
        if not payload:
            return None
        data = json.loads(payload)
        return cls(
            plugin_module=str(data["plugin_module"]),
            tool_name=str(data["tool_name"]),
            timeout_seconds=float(data["timeout_seconds"]),
            memory_limit_mb=int(data.get("memory_limit_mb", 256)),
            cpu_limit_seconds=int(data.get("cpu_limit_seconds", 2)),
            allowed_network_hosts=tuple(str(item) for item in data.get("allowed_network_hosts", [])),
            allowed_read_roots=tuple(str(item) for item in data.get("allowed_read_roots", [])),
            allowed_write_roots=tuple(str(item) for item in data.get("allowed_write_roots", [])),
            sandbox_root=str(data["sandbox_root"]) if data.get("sandbox_root") else None,
        )


@dataclass(slots=True)
class PluginSandboxResult:
    payload: dict[str, Any]
    stdout: str
    stderr: str
    returncode: int


def build_sandbox_env(base_env: dict[str, str], config: PluginSandboxConfig) -> dict[str, str]:
    env = {key: value for key, value in base_env.items() if key in config.allowed_env_keys}
    env.update(config.to_env())
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["SYNAPSE_PLUGIN_DEFENSE_IN_DEPTH"] = "1"
    return env


def sandbox_workdir() -> str:
    return tempfile.mkdtemp(prefix="synapse-plugin-")


def configure_process_sandbox() -> None:
    if os.environ.get("SYNAPSE_PLUGIN_DEFENSE_IN_DEPTH") != "1":
        return
    config = PluginSandboxConfig.from_env()
    if config is None:
        return
    # Defense in depth for isolated runners. The primary hosted boundary must
    # come from an OS-backed isolation backend.
    _apply_memory_limit(config.memory_limit_mb)
    _apply_cpu_limit(config.cpu_limit_seconds)
    _install_network_guard(config.allowed_network_hosts)
    _install_filesystem_guard(config.allowed_read_roots, config.allowed_write_roots)
    _install_process_guard()
    _install_workdir_guard(config.sandbox_root)


def _apply_memory_limit(memory_limit_mb: int) -> None:
    try:
        import resource
    except Exception:
        return
    limit_bytes = max(64, memory_limit_mb) * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    except Exception:
        return


def _apply_cpu_limit(cpu_limit_seconds: int) -> None:
    try:
        import resource
    except Exception:
        return
    limit = max(1, int(cpu_limit_seconds))
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (limit, limit))
    except Exception:
        return


def _install_network_guard(allowed_network_hosts: tuple[str, ...]) -> None:
    allowlist = {host.lower() for host in allowed_network_hosts}

    original_socket = socket.socket

    class DeniedSocket(original_socket):  # type: ignore[misc, valid-type]
        def connect(self, *args: Any, **kwargs: Any) -> Any:
            _assert_allowed_network_target(args[0] if args else None, allowlist)
            return super().connect(*args, **kwargs)

        def connect_ex(self, *args: Any, **kwargs: Any) -> Any:
            _assert_allowed_network_target(args[0] if args else None, allowlist)
            return super().connect_ex(*args, **kwargs)

    socket.socket = DeniedSocket  # type: ignore[assignment]
    socket.create_connection = lambda *args, **kwargs: _guarded_create_connection(allowlist, *args, **kwargs)  # type: ignore[assignment]


def _guarded_create_connection(allowlist: set[str], *args: Any, **kwargs: Any) -> Any:
    _assert_allowed_network_target(args[0] if args else None, allowlist)
    return _original_create_connection(*args, **kwargs)


_original_create_connection = socket.create_connection


def _assert_allowed_network_target(target: Any, allowlist: set[str]) -> None:
    host = None
    if isinstance(target, tuple) and target:
        host = target[0]
    elif isinstance(target, str):
        host = target
    if not host or str(host).lower() not in allowlist:
        raise PermissionError("Sandboxed plugins cannot open network connections.")


def _install_filesystem_guard(
    allowed_read_roots: tuple[str, ...],
    allowed_write_roots: tuple[str, ...],
) -> None:
    read_roots = tuple(Path(root).resolve() for root in allowed_read_roots if root)
    write_roots = tuple(Path(root).resolve() for root in allowed_write_roots if root)
    original_open = builtins.open

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):  # type: ignore[override]
        path = Path(file).resolve() if not isinstance(file, int) else None
        if path is not None:
            requires_write = any(flag in mode for flag in ("w", "a", "+", "x"))
            if requires_write:
                _assert_allowed(path, write_roots, "write")
            else:
                _assert_allowed(path, read_roots or write_roots, "read")
        return original_open(file, mode, *args, **kwargs)

    builtins.open = guarded_open  # type: ignore[assignment]
    Path.open = lambda self, *args, **kwargs: guarded_open(self, *args, **kwargs)  # type: ignore[assignment]
    io.open = guarded_open  # type: ignore[assignment]


def _install_process_guard() -> None:
    def _deny_process(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Sandboxed plugins cannot execute subprocesses.")

    subprocess.Popen = _deny_process  # type: ignore[assignment]
    subprocess.run = _deny_process  # type: ignore[assignment]
    subprocess.call = _deny_process  # type: ignore[assignment]
    os.system = _deny_process  # type: ignore[assignment]
    if hasattr(os, "popen"):
        os.popen = _deny_process  # type: ignore[assignment]


def _install_workdir_guard(sandbox_root: str | None) -> None:
    if not sandbox_root:
        return
    os.chdir(sandbox_root)


def _assert_allowed(path: Path, roots: tuple[Path, ...], action: str) -> None:
    if not roots:
        raise PermissionError(f"Sandboxed plugins cannot {action} files outside the sandbox.")
    for root in roots:
        try:
            path.relative_to(root)
            return
        except ValueError:
            continue
    raise PermissionError(f"Sandboxed plugins cannot {action} {path}.")
