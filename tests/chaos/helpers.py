from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from synapse.models.runtime_event import RuntimeEvent


@dataclass(slots=True)
class ChaosScenarioReport:
    scenario: str
    severity: str
    failure_mode: str
    safe: bool
    recovered: bool
    manual_intervention_required: bool
    expected_behavior: str
    notes: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "scenario": self.scenario,
            "severity": self.severity,
            "failure_mode": self.failure_mode,
            "safe": self.safe,
            "recovered": self.recovered,
            "manual_intervention_required": self.manual_intervention_required,
            "expected_behavior": self.expected_behavior,
            "evidence": self.evidence,
        }
        if self.notes:
            payload["notes"] = self.notes
        return payload


async def collect_events(queue: asyncio.Queue[RuntimeEvent], expected_count: int, timeout: float = 0.3) -> list[RuntimeEvent]:
    events: list[RuntimeEvent] = []
    for _ in range(expected_count):
        events.append(await asyncio.wait_for(queue.get(), timeout=timeout))
    return events


def event_types(events: list[RuntimeEvent]) -> set[str]:
    return {event.event_type.value for event in events}
