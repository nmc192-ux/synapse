from __future__ import annotations

from enum import Enum


class PrincipalType(str, Enum):
    OPERATOR = "operator"
    AGENT = "agent"
    SERVICE = "service"


class Scope(str, Enum):
    TASKS_READ = "tasks:read"
    TASKS_WRITE = "tasks:write"
    BROWSER_CONTROL = "browser:control"
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    A2A_SEND = "a2a:send"
    A2A_RECEIVE = "a2a:receive"
    ADMIN = "admin"


PROJECT_SCOPED_SCOPES: set[str] = {
    Scope.TASKS_READ.value,
    Scope.TASKS_WRITE.value,
    Scope.BROWSER_CONTROL.value,
    Scope.MEMORY_READ.value,
    Scope.MEMORY_WRITE.value,
    Scope.A2A_SEND.value,
    Scope.A2A_RECEIVE.value,
    Scope.ADMIN.value,
}


def scopes_require_project_context(scopes: list[str]) -> bool:
    return any(scope in PROJECT_SCOPED_SCOPES for scope in scopes)
