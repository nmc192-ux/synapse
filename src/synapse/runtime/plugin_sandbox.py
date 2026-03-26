from __future__ import annotations

import builtins
import io
import json
import os
import socket
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
    network_enabled: bool = False
    allowed_env_keys: tuple[str, ...] = ("PATH", "PYTHONPATH", "LANG", "LC_ALL")
    allowed_read_roots: tuple[str, ...] = ()
    allowed_write_roots: tuple[str, ...] = ()

    def to_env(self) -> dict[str, str]:
        return {
            "SYNAPSE_SANDBOX_CONFIG": json.dumps(
                {
                    "plugin_module": self.plugin_module,
                    "tool_name": self.tool_name,
                    "timeout_seconds": self.timeout_seconds,
                    "memory_limit_mb": self.memory_limit_mb,
                    "network_enabled": self.network_enabled,
                    "allowed_read_roots": list(self.allowed_read_roots),
                    "allowed_write_roots": list(self.allowed_write_roots),
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
            network_enabled=bool(data.get("network_enabled", False)),
            allowed_read_roots=tuple(str(item) for item in data.get("allowed_read_roots", [])),
            allowed_write_roots=tuple(str(item) for item in data.get("allowed_write_roots", [])),
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
    env["SYNAPSE_PLUGIN_NETWORK_ENABLED"] = "1" if config.network_enabled else "0"
    return env


def sandbox_workdir() -> str:
    return tempfile.mkdtemp(prefix="synapse-plugin-")


def configure_process_sandbox() -> None:
    config = PluginSandboxConfig.from_env()
    if config is None:
        return
    _apply_memory_limit(config.memory_limit_mb)
    _install_network_guard(config.network_enabled)
    _install_filesystem_guard(config.allowed_read_roots, config.allowed_write_roots)


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


def _install_network_guard(network_enabled: bool) -> None:
    if network_enabled:
        return

    original_socket = socket.socket

    class DeniedSocket(original_socket):  # type: ignore[misc, valid-type]
        def connect(self, *args: Any, **kwargs: Any) -> Any:
            raise PermissionError("Sandboxed plugins cannot open network connections.")

        def connect_ex(self, *args: Any, **kwargs: Any) -> Any:
            raise PermissionError("Sandboxed plugins cannot open network connections.")

    socket.socket = DeniedSocket  # type: ignore[assignment]
    socket.create_connection = _deny_network  # type: ignore[assignment]


def _deny_network(*args: Any, **kwargs: Any) -> Any:
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
