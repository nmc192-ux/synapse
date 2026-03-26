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
