from __future__ import annotations

from common import (
    FAILURE_BUCKETS,
    build_agent_definition,
    build_project_admin_api,
    build_project_api,
    classify_failure,
    compute_project_metrics,
    filter_telemetry_events_since,
    load_telemetry_events_since,
    overall_metrics,
    parse_loop_args,
    register_role_agent,
    role_project_alias,
    run_forever,
    safe_list_audit_logs,
    sync_project_request_health,
    sync_project_runtime_events,
    start_role_a2a_listener,
    timestamp_slug,
    window_start_for,
    write_json_artifact,
)
from synapse.models.agent import AgentKind


_A2A_LISTENER = None


def ensure_a2a_listener() -> None:
    global _A2A_LISTENER
    if _A2A_LISTENER is None:
        _A2A_LISTENER = start_role_a2a_listener("auditor", "synthetic-alpha-auditor")


def run_once() -> None:
    register_role_agent(
        "auditor",
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
    ensure_a2a_listener()
    since = window_start_for("daily")
    project_reports: list[dict[str, object]] = []
    for alias in ("steady", "chaos"):
        sync_project_runtime_events(alias, since)
        sync_project_request_health(alias, since)
    telemetry_events = load_telemetry_events_since(since)
    for alias in ("steady", "chaos"):
        with build_project_api(alias) as api:
            runs = api.list_runs()
            interventions = api.list_interventions()
        with build_project_admin_api(alias) as admin_api:
            audit_logs = safe_list_audit_logs(admin_api, admin_api.project_id or "")
            metrics = compute_project_metrics(
                alias,
                runs,
                interventions,
                audit_logs,
                filter_telemetry_events_since(
                    [event for event in telemetry_events if str(event.get("project_alias")) == alias],
                    since,
                ),
            )
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
    run_forever(
        run_once,
        once=args.once,
        interval_seconds=args.interval_seconds,
        role_name="auditor",
        agent_id="synthetic-alpha-auditor",
        project_alias=role_project_alias("auditor"),
    )


if __name__ == "__main__":
    main()
