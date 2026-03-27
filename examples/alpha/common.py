from __future__ import annotations

import os

from synapse.models.agent import (
    AgentChallengePolicy,
    AgentDefinition,
    AgentExecutionLimits,
    AgentKind,
    AgentSecurityPolicy,
)
from synapse.sdk import SynapseClient


def require_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required for restricted alpha examples.")
    return value.strip()


def optional_csv_env(name: str, default: str) -> list[str]:
    raw_value = os.getenv(name, default)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def build_client(agent_id: str) -> SynapseClient:
    return SynapseClient(
        base_url=require_env("SYNAPSE_BASE_URL", "http://127.0.0.1:8000"),
        bearer_token=os.getenv("SYNAPSE_BEARER_TOKEN"),
        api_key=os.getenv("SYNAPSE_API_KEY"),
        project_id=require_env("SYNAPSE_PROJECT_ID", "project_design_partner_a"),
        agent_id=agent_id,
    )


def restricted_alpha_agent(
    *,
    agent_id: str,
    kind: AgentKind,
    name: str,
    description: str,
    allowed_tools: list[str],
    allowed_domains_env: str = "SYNAPSE_ALLOWED_DOMAINS",
    capability_tags: list[str] | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        agent_id=agent_id,
        kind=kind,
        name=name,
        description=description,
        capability_tags=capability_tags or ["restricted-alpha", "supervised"],
        security=AgentSecurityPolicy(
            allowed_domains=optional_csv_env(allowed_domains_env, "example.com"),
            allowed_tools=allowed_tools,
            dangerous_action_requires_approval=True,
            challenge_policy=AgentChallengePolicy.ESCALATE_TO_OPERATOR,
            uploads_allowed=False,
            downloads_allowed=False,
            max_cross_domain_jumps=2,
        ),
        limits=AgentExecutionLimits(
            max_steps=20,
            max_pages=6,
            max_tool_calls=6,
            max_runtime_seconds=120,
            max_tokens=12000,
            max_memory_writes=10,
        ),
        metadata={
            "launch_channel": "restricted_design_partner_alpha",
            "supervision_required": "true",
            "sensitive_credentials_allowed": "false",
        },
    )
