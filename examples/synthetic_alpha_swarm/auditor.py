from __future__ import annotations

from common import (
    FAILURE_BUCKETS,
    build_agent_definition,
    build_project_api,
    classify_failure,
    compute_project_metrics,
    overall_metrics,
    parse_loop_args,
    register_role_agent,
    role_project_alias,
    run_forever,
    timestamp_slug,
    write_json_artifact,
)
from synapse.models.agent import AgentKind


def run_once() -> None:
    register_role_agent(
        role_project_alias("auditor"),
        build_agent_definition(
            agent_id="synthetic-alpha-auditor",
            kind=AgentKind.OPENCLAW,
            name="Synthetic Alpha Auditor",
            description="Classifies failures, interventions, and stability issues across the synthetic-alpha swarm.",
            role="auditor",
            allowed_tools=[],
            extra_tags=["audit", "triage", "continuous"],
        ),
    )
    project_reports: list[dict[str, object]] = []
    for alias in ("steady", "chaos"):
        with build_project_api(alias) as api:
            runs = api.list_runs()
            interventions = api.list_interventions()
            audit_logs = api.list_audit_logs(api.project_id or "")
            metrics = compute_project_metrics(alias, runs, interventions, audit_logs)
            metrics["classified_failures"] = [
                {
                    "run_id": run.run_id,
                    "failure_class": classify_failure(run, interventions),
                    "status": run.status.value,
                }
                for run in runs
                if run.status.value == "failed"
            ]
            project_reports.append(metrics)
    summary = overall_metrics(project_reports)
    summary["required_failure_buckets"] = FAILURE_BUCKETS
    artifact = write_json_artifact(
        f"auditor_report_{timestamp_slug()}.json",
        {"projects": project_reports, "summary": summary},
    )
    print({"artifacts": artifact, "projects": len(project_reports), "summary": summary})


def main() -> None:
    args = parse_loop_args("Synthetic Alpha Auditor", default_interval=900.0)
    run_forever(run_once, once=args.once, interval_seconds=args.interval_seconds)


if __name__ == "__main__":
    main()
