from __future__ import annotations

from common import (
    FAILURE_BUCKETS,
    assess_project_alpha_gate,
    build_agent_definition,
    build_project_admin_api,
    build_project_api,
    compute_project_metrics,
    ensure_project_runtime_listener,
    filter_audit_logs_since,
    filter_interventions_since,
    filter_runs_since,
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
    utc_now,
    window_start_for,
    write_report_json,
    write_report_text,
)
from synapse.models.agent import AgentKind


_A2A_LISTENER = None


def ensure_a2a_listener() -> None:
    global _A2A_LISTENER
    if _A2A_LISTENER is None:
        _A2A_LISTENER = start_role_a2a_listener("reporter", "synthetic-alpha-reporter")


def ensure_runtime_listeners() -> None:
    for alias in ("steady", "chaos"):
        ensure_project_runtime_listener(alias)


def collect_metrics(window_label: str) -> tuple[list[dict[str, object]], dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    since = window_start_for(window_label)
    for alias in ("steady", "chaos"):
        sync_project_runtime_events(alias, since)
        sync_project_request_health(alias, since)
    telemetry_events = load_telemetry_events_since(since)
    for alias in ("steady", "chaos"):
        with build_project_api(alias) as api:
            runs = filter_runs_since(api.list_runs(), since)
            interventions = filter_interventions_since(api.list_interventions(), since)
        with build_project_admin_api(alias) as admin_api:
            audit_logs = filter_audit_logs_since(safe_list_audit_logs(admin_api, admin_api.project_id or ""), since)
            project_telemetry = filter_telemetry_events_since(
                [event for event in telemetry_events if str(event.get("project_alias")) == alias],
                since,
            )
            snapshots.append(compute_project_metrics(alias, runs, interventions, audit_logs, project_telemetry))
    return snapshots, overall_metrics(snapshots)


def build_daily_report(projects: list[dict[str, object]], summary: dict[str, object]) -> str:
    alpha_gate = summary["alpha_gate"]
    blocker_lines = [f"  - {reason}" for reason in alpha_gate.get("release_blockers", [])] or ["  - none"]
    lines = [
        "# Synthetic Alpha Daily Report",
        "",
        f"Generated: {utc_now().isoformat()}",
        f"Window start: {window_start_for('daily').isoformat()}",
        "",
        "## Alpha Gate Recommendation",
        f"- Recommendation: {alpha_gate['recommendation']}",
        f"- Safe degraded recoveries: {alpha_gate['safe_degraded_recoveries']}",
        f"- Unresolved degradation: {alpha_gate['unresolved_degradation']}",
        f"- Unsafe failures: {alpha_gate['unsafe_failures']}",
        f"- Manual interventions: {alpha_gate['manual_interventions']}",
        "## Release Blockers",
        *blocker_lines,
        "- Reasons:",
        *[f"  - {reason}" for reason in alpha_gate["reasons"]],
        "",
        "## Fleet Summary",
        f"- Runs started: {summary['runs_started']}",
        f"- Runs completed: {summary['runs_completed']}",
        f"- Runs failed: {summary['runs_failed']}",
        f"- Intervention count: {summary['intervention_count']}",
        f"- Browser crash count: {summary['browser_crash_count']}",
        f"- Captcha/challenge count: {summary['captcha_challenge_count']}",
        f"- Session restore failures: {summary['session_restore_failures']}",
        f"- Duplicate-result recoveries: {summary['duplicate_result_recoveries']}",
        f"- Stale ownership incidents: {summary['stale_ownership_incidents']}",
        f"- A2A messages sent: {summary['a2a_messages_sent']}",
        f"- A2A messages succeeded: {summary['a2a_messages_succeeded']}",
        f"- A2A messages failed: {summary['a2a_messages_failed']}",
        f"- Scheduler recovery events: {summary['scheduler_recovery_events']}",
        f"- Plugin denials: {summary['plugin_denials']}",
        f"- Average run latency (s): {summary['average_run_latency_seconds']}",
        f"- Runs waiting for operator: {summary.get('waiting_for_operator_runs', 0)}",
        f"- Runs waiting for operator beyond timeout: {summary.get('stale_waiting_for_operator_runs', 0)}",
        f"- Pending operator-review interventions: {summary.get('pending_operator_review_interventions', 0)}",
        f"- Pending operator-review interventions beyond timeout: {summary.get('overdue_operator_review_interventions', 0)}",
        "",
        "## Durable Request Health",
        f"- Slow requests observed: {summary['request_health_summary']['slow']}",
        f"- Stuck requests observed: {summary['request_health_summary']['stuck']}",
        f"- Recovered requests observed: {summary['request_health_summary']['recovered']}",
        f"- Abandoned requests observed: {summary['request_health_summary']['abandoned']}",
        f"- Operator-required requests observed: {summary['request_health_summary']['operator_required']}",
        f"- Operator-review backlog beyond timeout: {summary['request_health_summary']['operator_review_overdue']}",
        f"- Completed after slow/stuck: {summary['request_health_summary']['completed_after_slow']}",
        f"- Unresolved active requests: {summary['request_health_summary']['unresolved']}",
        "",
        "## Browser Errors By Category",
    ]
    for bucket, count in summary["browser_errors_by_category"].items():
        lines.append(f"- {bucket}: {count}")
    lines.extend(["", "## Intervention Count By Reason"])
    for reason, count in summary["intervention_count_by_reason"].items():
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## Per-Project Failure Rate"])
    for alias, rate in summary["per_project_failure_rate"].items():
        lines.append(f"- {alias}: {rate:.2%}")
    lines.extend(["", "## Failure Classification", *[f"- {bucket}: {summary['failure_classification'].get(bucket, 0)}" for bucket in FAILURE_BUCKETS]])
    lines.extend(["", "## Per-Agent Outcomes"])
    for agent_id, values in summary["per_agent_outcomes"].items():
        lines.append(
            f"- {agent_id}: started {values['runs_started']}, completed {values['runs_completed']}, failed {values['runs_failed']}, success rate {values['success_rate']:.2%}"
        )
    lines.extend(["", "## Agents Requiring Intervention"])
    for agent_id, count in summary["agents_requiring_intervention"].items():
        lines.append(f"- {agent_id}: {count}")
    for project in projects:
        project_gate = project["alpha_gate"]
        lines.extend(
            [
                "",
                f"## Project {project['project_alias']}",
                f"- Alpha gate recommendation: {project_gate['recommendation']}",
                f"- Alpha gate safe degraded / unresolved / unsafe / manual: {project_gate['safe_degraded_recoveries']}/{project_gate['unresolved_degradation']}/{project_gate['unsafe_failures']}/{project_gate['manual_interventions']}",
                f"- Runs started: {project['runs_started']}",
                f"- Runs completed: {project['runs_completed']}",
                f"- Runs failed: {project['runs_failed']}",
                f"- Intervention count: {project['intervention_count']}",
                f"- Browser crash count: {project['browser_crash_count']}",
                f"- Captcha/challenge count: {project['captcha_challenge_count']}",
                f"- Session restore failures: {project['session_restore_failures']}",
                f"- Duplicate-result recoveries: {project['duplicate_result_recoveries']}",
                f"- Stale ownership incidents: {project['stale_ownership_incidents']}",
                f"- A2A sent/succeeded/failed: {project['a2a_messages_sent']}/{project['a2a_messages_succeeded']}/{project['a2a_messages_failed']}",
                f"- Scheduler recovery events: {project['scheduler_recovery_events']}",
                f"- Plugin denials: {project['plugin_denials']}",
                f"- Average run latency (s): {project['average_run_latency_seconds']}",
                f"- Runs waiting for operator: {project.get('waiting_for_operator_runs', 0)}",
                f"- Runs waiting for operator beyond timeout: {project.get('stale_waiting_for_operator_runs', 0)}",
                f"- Pending operator-review interventions: {project.get('pending_operator_review_interventions', 0)}",
                f"- Pending operator-review interventions beyond timeout: {project.get('overdue_operator_review_interventions', 0)}",
                f"- Failure rate: {project['per_project_failure_rate']:.2%}",
                f"- Request health slow/stuck/recovered/abandoned/operator-required/operator-review-overdue/completed-after-slow/unresolved: {project['request_health_summary']['slow']}/{project['request_health_summary']['stuck']}/{project['request_health_summary']['recovered']}/{project['request_health_summary']['abandoned']}/{project['request_health_summary']['operator_required']}/{project['request_health_summary']['operator_review_overdue']}/{project['request_health_summary']['completed_after_slow']}/{project['request_health_summary']['unresolved']}",
                f"- Alpha gate blockers: {', '.join(project_gate.get('release_blockers', [])) or 'none'}",
                f"- Alpha gate reasons: {', '.join(project_gate['reasons'])}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def build_weekly_review(projects: list[dict[str, object]], summary: dict[str, object]) -> str:
    alpha_gate = summary["alpha_gate"]
    top_regressions = sorted(summary["failure_classification"].items(), key=lambda item: (-item[1], item[0]))[:3]
    intervention_heavy = list(summary["agents_requiring_intervention"].items())[:5]
    lines = [
        "# Synthetic Alpha Weekly Review Draft",
        "",
        f"Generated: {utc_now().isoformat()}",
        f"Window start: {window_start_for('weekly').isoformat()}",
        "",
        "## Executive Summary",
        f"- Alpha gate recommendation: {alpha_gate['recommendation']}",
        f"- Total runs started this week: {summary['runs_started']}",
        f"- Total failures this week: {summary['runs_failed']}",
        f"- Total interventions this week: {summary['intervention_count']}",
        f"- Total A2A failures this week: {summary['a2a_messages_failed']}",
        f"- Mean run latency (s): {summary['average_run_latency_seconds']}",
        f"- Unresolved degradation signals: {alpha_gate['unresolved_degradation']}",
        f"- Active unresolved request-health signals: {summary['request_health_summary']['unresolved']}",
        f"- Runs waiting for operator: {summary.get('waiting_for_operator_runs', 0)}",
        f"- Runs waiting for operator beyond timeout: {summary.get('stale_waiting_for_operator_runs', 0)}",
        f"- Pending operator-review interventions: {summary.get('pending_operator_review_interventions', 0)}",
        f"- Pending operator-review interventions beyond timeout: {summary.get('overdue_operator_review_interventions', 0)}",
        "",
        "## Key Risks",
        f"- Browser issues: {summary['failure_classification'].get('browser issue', 0)}",
        f"- Scheduler issues: {summary['failure_classification'].get('scheduler issue', 0)}",
        f"- Policy denials: {summary['failure_classification'].get('policy denial', 0)}",
        f"- Challenge/captcha: {summary['failure_classification'].get('challenge/captcha', 0)}",
        f"- Auth/session issues: {summary['failure_classification'].get('auth/session issue', 0)}",
        f"- Plugin issues: {summary['failure_classification'].get('plugin issue', 0)}",
        "",
        "## Top Regressions",
        *[f"- {name}: {count}" for name, count in top_regressions],
        "",
        "## Agents Requiring Most Intervention",
        *[f"- {agent_id}: {count} interventions" for agent_id, count in intervention_heavy],
        "",
        "## Durable Request Health",
        f"- Slow: {summary['request_health_summary']['slow']}",
        f"- Stuck: {summary['request_health_summary']['stuck']}",
        f"- Recovered: {summary['request_health_summary']['recovered']}",
        f"- Abandoned: {summary['request_health_summary']['abandoned']}",
        f"- Operator-required: {summary['request_health_summary']['operator_required']}",
        f"- Operator-review overdue: {summary['request_health_summary']['operator_review_overdue']}",
        f"- Completed after slow/stuck: {summary['request_health_summary']['completed_after_slow']}",
        f"- Unresolved: {summary['request_health_summary']['unresolved']}",
        "",
        "## Recommendations",
        *[f"- {reason}" for reason in alpha_gate["reasons"]],
        "",
        "## Release Blockers",
        *([f"- {reason}" for reason in alpha_gate.get("release_blockers", [])] or ["- none"]),
        "",
        "## Project Notes",
    ]
    for project in projects:
        project_gate = project["alpha_gate"]
        lines.append(
            f"- {project['project_alias']}: recommendation {project_gate['recommendation']}, failure rate {project['per_project_failure_rate']:.2%}, avg latency {project['average_run_latency_seconds']}s, interventions {project['intervention_count']}"
        )
    return "\n".join(lines).strip() + "\n"


def build_dashboard_html(daily_summary: dict[str, object], weekly_summary: dict[str, object]) -> str:
    daily_gate = daily_summary["alpha_gate"]
    top_failures = "".join(
        f"<li><strong>{bucket}</strong>: {count}</li>" for bucket, count in daily_summary["failure_classification"].items()
    )
    top_agents = "".join(
        f"<li><strong>{agent}</strong>: {count} interventions</li>" for agent, count in daily_summary["agents_requiring_intervention"].items()
    )
    gate_reasons = "".join(f"<li>{reason}</li>" for reason in daily_gate["reasons"])
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Synthetic Alpha Review</title>"
        "<style>body{font-family:ui-sans-serif,system-ui;margin:32px;background:#f5f0e8;color:#17212b}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}.card{background:white;border-radius:16px;padding:18px;box-shadow:0 10px 30px rgba(0,0,0,.08)}h1,h2{margin:0 0 12px}ul{padding-left:18px}</style></head><body>"
        f"<h1>Synthetic Alpha Review</h1><p>Generated {utc_now().isoformat()}</p>"
        "<div class='grid'>"
        f"<section class='card'><h2>Alpha Gate</h2><p>Recommendation: {daily_gate['recommendation']}</p><p>Safe degraded: {daily_gate['safe_degraded_recoveries']}</p><p>Unresolved: {daily_gate['unresolved_degradation']}</p><p>Unsafe: {daily_gate['unsafe_failures']}</p><p>Manual: {daily_gate['manual_interventions']}</p><ul>{gate_reasons}</ul></section>"
        f"<section class='card'><h2>Daily Summary</h2><p>Runs started: {daily_summary['runs_started']}</p><p>Runs failed: {daily_summary['runs_failed']}</p><p>A2A failures: {daily_summary['a2a_messages_failed']}</p><p>Avg latency: {daily_summary['average_run_latency_seconds']}s</p><p>Waiting for operator: {daily_summary.get('waiting_for_operator_runs', 0)}</p><p>Waiting beyond timeout: {daily_summary.get('stale_waiting_for_operator_runs', 0)}</p><p>Pending operator review: {daily_summary.get('pending_operator_review_interventions', 0)}</p><p>Overdue operator review: {daily_summary.get('overdue_operator_review_interventions', 0)}</p></section>"
        f"<section class='card'><h2>Weekly Summary</h2><p>Runs started: {weekly_summary['runs_started']}</p><p>Runs failed: {weekly_summary['runs_failed']}</p><p>Interventions: {weekly_summary['intervention_count']}</p><p>Plugin denials: {weekly_summary['plugin_denials']}</p></section>"
        f"<section class='card'><h2>Request Health</h2><p>Slow: {daily_summary['request_health_summary']['slow']}</p><p>Stuck: {daily_summary['request_health_summary']['stuck']}</p><p>Recovered: {daily_summary['request_health_summary']['recovered']}</p><p>Abandoned: {daily_summary['request_health_summary']['abandoned']}</p><p>Operator-required: {daily_summary['request_health_summary']['operator_required']}</p><p>Operator-review overdue: {daily_summary['request_health_summary']['operator_review_overdue']}</p><p>Completed after slow: {daily_summary['request_health_summary']['completed_after_slow']}</p><p>Unresolved: {daily_summary['request_health_summary']['unresolved']}</p></section>"
        f"<section class='card'><h2>Top Failure Categories</h2><ul>{top_failures}</ul></section>"
        f"<section class='card'><h2>Agents Requiring Intervention</h2><ul>{top_agents}</ul></section>"
        "</div></body></html>"
    )


def fixture_project_summary(project_alias: str, started: int, completed: int, failed: int, interventions: int) -> dict[str, object]:
    snapshot = {
        "project_alias": project_alias,
        "runs_started": started,
        "runs_completed": completed,
        "runs_failed": failed,
        "intervention_count": interventions,
        "browser_crash_count": 1 if project_alias == "steady" else 2,
        "captcha_challenge_count": 1 if project_alias == "steady" else 2,
        "session_restore_failures": 2 if project_alias == "steady" else 1,
        "duplicate_result_recoveries": 1 if project_alias == "steady" else 2,
        "stale_ownership_incidents": 1,
        "a2a_messages_sent": 6 if project_alias == "steady" else 4,
        "a2a_messages_succeeded": 5 if project_alias == "steady" else 3,
        "a2a_messages_failed": 1,
        "scheduler_recovery_events": 2 if project_alias == "steady" else 3,
        "plugin_denials": 0 if project_alias == "steady" else 1,
        "average_run_latency_seconds": 43.8 if project_alias == "steady" else 51.2,
        "waiting_for_operator_runs": 0 if project_alias == "steady" else 1,
        "stale_waiting_for_operator_runs": 0 if project_alias == "steady" else 1,
        "pending_operator_review_interventions": 0 if project_alias == "steady" else 1,
        "overdue_operator_review_interventions": 0 if project_alias == "steady" else 1,
        "per_project_failure_rate": 0.125 if project_alias == "steady" else 0.2143,
        "request_health_summary": {
            "slow": 2 if project_alias == "steady" else 1,
            "stuck": 1,
            "recovered": 1 if project_alias == "steady" else 0,
            "abandoned": 0 if project_alias == "steady" else 1,
            "operator_required": 0 if project_alias == "steady" else 1,
            "operator_review_overdue": 0 if project_alias == "steady" else 1,
            "completed_after_slow": 1,
            "unresolved": 0 if project_alias == "steady" else 1,
        },
        "failure_classification": {
            "browser issue": 2 if project_alias == "steady" else 1,
            "scheduler issue": 1 if project_alias == "steady" else 2,
            "policy denial": 1,
            "challenge/captcha": 1,
            "auth/session issue": 1 if project_alias == "steady" else 0,
            "plugin issue": 0 if project_alias == "steady" else 1,
            "other": 0,
        },
        "browser_errors_by_category": {
            "browser issue": 2 if project_alias == "steady" else 1,
            "scheduler issue": 1 if project_alias == "steady" else 2,
            "policy denial": 1,
            "challenge/captcha": 1,
            "auth/session issue": 1 if project_alias == "steady" else 0,
            "plugin issue": 0 if project_alias == "steady" else 1,
            "other": 0,
        },
        "intervention_count_by_reason": {
            "browser issue": 1 if project_alias == "steady" else 2,
            "challenge/captcha": 1,
            "policy denial": 1 if project_alias == "chaos" else 0,
        },
        "per_agent_outcomes": {
            f"synthetic-alpha-{project_alias}-agent": {
                "runs_started": started,
                "runs_completed": completed,
                "runs_failed": failed,
                "success_rate": round(completed / started, 4),
                "failure_rate": round(failed / started, 4),
            }
        },
        "agents_requiring_intervention": {f"synthetic-alpha-{project_alias}-agent": interventions},
    }
    snapshot["alpha_gate"] = assess_project_alpha_gate(snapshot)
    return snapshot


def write_example_reports() -> dict[str, dict[str, str]]:
    fixture_projects = [
        fixture_project_summary("steady", 48, 42, 6, 3),
        fixture_project_summary("chaos", 28, 22, 6, 5),
    ]
    summary = overall_metrics(fixture_projects)
    daily_paths = write_report_text("example_daily_report.md", build_daily_report(fixture_projects, summary))
    weekly_paths = write_report_text("example_weekly_review.md", build_weekly_review(fixture_projects, summary))
    json_paths = write_report_json("example_metrics_snapshot.json", {"projects": fixture_projects, "summary": summary})
    dashboard_paths = write_report_text("example_dashboard.html", build_dashboard_html(summary, summary))
    return {"daily": daily_paths, "weekly": weekly_paths, "metrics": json_paths, "dashboard": dashboard_paths}


def run_once() -> None:
    register_role_agent(
        "reporter",
        build_agent_definition(
            agent_id="synthetic-alpha-reporter",
            kind=AgentKind.OPENCLAW,
            name="Synthetic Alpha Reporter",
            description="Produces daily and weekly synthetic-alpha reports and review drafts.",
            role="reporter",
            allowed_tools=[],
            extra_tags=["reporting", "summaries", "continuous"],
        ),
    )
    ensure_a2a_listener()
    ensure_runtime_listeners()
    daily_projects, daily_summary = collect_metrics("daily")
    weekly_projects, weekly_summary = collect_metrics("weekly")
    slug = timestamp_slug()
    daily_paths = write_report_text(f"daily_report_{slug}.md", build_daily_report(daily_projects, daily_summary))
    weekly_paths = write_report_text(f"weekly_alpha_review_{slug}.md", build_weekly_review(weekly_projects, weekly_summary))
    metrics_paths = write_report_json(
        f"metrics_snapshot_{slug}.json",
        {"daily": {"projects": daily_projects, "summary": daily_summary}, "weekly": {"projects": weekly_projects, "summary": weekly_summary}},
    )
    latest_paths = write_report_json(
        "synthetic_alpha_summary_latest.json",
        {"daily": {"projects": daily_projects, "summary": daily_summary}, "weekly": {"projects": weekly_projects, "summary": weekly_summary}},
    )
    dashboard_paths = write_report_text("synthetic_alpha_dashboard_latest.html", build_dashboard_html(daily_summary, weekly_summary))
    print({"daily": daily_paths, "weekly": weekly_paths, "metrics": metrics_paths, "latest": latest_paths, "dashboard": dashboard_paths})


def main() -> None:
    args = parse_loop_args("Synthetic Alpha Reporter", default_interval=3600.0)
    run_forever(
        run_once,
        once=args.once,
        interval_seconds=args.interval_seconds,
        role_name="reporter",
        agent_id="synthetic-alpha-reporter",
        project_alias=role_project_alias("reporter"),
    )


if __name__ == "__main__":
    main()
