from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class BenchmarkCategory(str, Enum):
    EXTRACTION = "extraction"
    FORM_COMPLETION = "form_completion"
    SPA_NAVIGATION = "spa_navigation"
    POPUPS = "popups"
    SESSION_CONTINUATION = "session_continuation"
    A2A_DELEGATION = "a2a_delegation"


class BenchmarkScenario(BaseModel):
    scenario_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    category: BenchmarkCategory
    description: str
    entrypoint_url: str
    task_template: dict[str, object] = Field(default_factory=dict)
    success_criteria: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class BenchmarkRunScore(BaseModel):
    run_id: str
    task_id: str
    agent_id: str
    scenario_id: str | None = None
    scenario_name: str | None = None
    category: BenchmarkCategory | None = None
    success: bool
    status: str
    latency_ms: int = 0
    tokens_used: int = 0
    pages_opened: int = 0
    tool_calls: int = 0
    memory_writes: int = 0
    llm_cost_estimate: float = 0.0
    tool_cost_estimate: float = 0.0
    compression_savings_ratio: float = 0.0
    retries: int = 0
    operator_interventions: int = 0
    delegated_messages: int = 0
    planning_iterations: int = 0
    notes: list[str] = Field(default_factory=list)


class BenchmarkAggregate(BaseModel):
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    total_tokens_used: int = 0
    total_tool_calls: int = 0
    total_retries: int = 0
    total_operator_interventions: int = 0
    avg_compression_savings_ratio: float = 0.0
    total_delegated_messages: int = 0


class BenchmarkReport(BaseModel):
    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    suite_name: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    fixture_base_url: str | None = None
    scenarios: list[BenchmarkScenario] = Field(default_factory=list)
    scores: list[BenchmarkRunScore] = Field(default_factory=list)
    aggregate: BenchmarkAggregate = Field(default_factory=BenchmarkAggregate)
    metadata: dict[str, object] = Field(default_factory=dict)
