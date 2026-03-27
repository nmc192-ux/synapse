from __future__ import annotations

from collections import Counter

from common import (
    build_agent_definition,
    build_project_api,
    parse_loop_args,
    register_role_agent,
    role_project_alias,
    run_forever,
    timestamp_slug,
    write_json_artifact,
)
from synapse.models.agent import AgentKind


def classify_run(run: object) -> str:
    status = getattr(run, "status", None)
    phase = getattr(run, "current_phase", None) or ""
    metadata = getattr(run, "metadata", {}) or {}
    resolved_status = getattr(status, "value", status)
    if resolved_status == "failed":
        if "timeout" in str(metadata).lower() or "timeout" in str(phase).lower():
            return "timeout"
        return "failed"
    if resolved_status == "waiting_for_operator":
        return "operator_pause"
    if resolved_status == "completed":
        return "completed"
    return "in_progress"


def classify_intervention(intervention: object) -> str:
    reason = getattr(intervention, "reason", "")
    normalized = reason.lower()
    if "domain" in normalized:
        return "domain_policy"
    if "approval" in normalized or "operator" in normalized:
        return "approval_required"
    if "timeout" in normalized:
        return "timeout"
    return "other"


def run_once() -> None:
    register_role_agent(
        role_project_alias("auditor"),
        build_agent_definition(
            agent_id="synthetic-alpha-auditor",
            kind=AgentKind.OPENCLAW,
            name="Synthetic Alpha Auditor",
            description="Classifies run failures and intervention patterns across the synthetic-alpha swarm.",
            role="auditor",
            allowed_tools=[],
            extra_tags=["audit", "triage"],
        ),
    )
    report: dict[str, object] = {}
    for alias in ("steady", "chaos"):
        with build_project_api(alias) as api:
            runs = api.list_runs()
            interventions = api.list_interventions()
            run_counts = Counter(classify_run(run) for run in runs)
            intervention_counts = Counter(classify_intervention(item) for item in interventions)
            report[alias] = {
                "runs_total": len(runs),
                "interventions_total": len(interventions),
                "run_classes": dict(run_counts),
                "intervention_classes": dict(intervention_counts),
            }
    artifact = write_json_artifact(f"auditor_report_{timestamp_slug()}.json", report)
    print({"artifact": str(artifact), "report": report})


def main() -> None:
    args = parse_loop_args("Synthetic Alpha Auditor")
    run_forever(run_once, once=args.once, interval_seconds=args.interval_seconds)


if __name__ == "__main__":
    main()
