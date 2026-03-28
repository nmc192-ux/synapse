from __future__ import annotations

import json
from pathlib import Path

from common import (
    FAILURE_BUCKETS,
    build_agent_definition,
    build_project_api,
    compute_project_metrics,
    filter_audit_logs_since,
    filter_interventions_since,
    filter_runs_since,
    overall_metrics,
    parse_loop_args,
    register_role_agent,
    role_project_alias,
    run_forever,
    start_role_a2a_listener,
    timestamp_slug,
    utc_now,
    window_start_for,
    write_json_artifact,
    write_text_artifact,
)
from synapse.models.agent import AgentKind


_A2A_LISTENER = None


def ensure_a2a_listener() -> None:
    global _A2A_LISTENER
    if _A2A_LISTENER is None:
        _A2A_LISTENER = start_role_a2a_listener("reporter", "synthetic-alpha-reporter")


def collect_metrics(window_label: str) -> tuple[list[dict[str, object]], dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    since = window_start_for(window_label)
    for alias in ("steady", "chaos"):
        with build_project_api(alias) as api:
            runs = filter_runs_since(api.list_runs(), since)
            interventions = filter_interventions_since(api.list_interventions(), since)
            audit_logs = filter_audit_logs_since(api.list_audit_logs(api.project_id or ""), since)
            snapshots.append(compute_project_metrics(alias, runs, interventions, audit_logs))
    return snapshots, overall_metrics(snapshots)


def build_daily_report(projects: list[dict[str, object]], summary: dict[str, object]) -> str:
    lines = [
        "# Synthetic Alpha Daily Report",
        "",
        f"Generated: {utc_now().isoformat()}",
        f"Window start: {window_start_for('daily').isoformat()}",
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
        f"- Average run latency (s): {summary['average_run_latency_seconds']}",
        "",
        "## Per-Project Failure Rate",
    ]
    for alias, rate in summary["per_project_failure_rate"].items():
        lines.append(f"- {alias}: {rate:.2%}")
    lines.extend(["", "## Failure Classification", *[f"- {bucket}: {summary['failure_classification'].get(bucket, 0)}" for bucket in FAILURE_BUCKETS]])
    for project in projects:
        lines.extend(
            [
                "",
                f"## Project {project['project_alias']}",
                f"- Runs started: {project['runs_started']}",
                f"- Runs completed: {project['runs_completed']}",
                f"- Runs failed: {project['runs_failed']}",
                f"- Intervention count: {project['intervention_count']}",
                f"- Browser crash count: {project['browser_crash_count']}",
                f"- Captcha/challenge count: {project['captcha_challenge_count']}",
                f"- Session restore failures: {project['session_restore_failures']}",
                f"- Duplicate-result recoveries: {project['duplicate_result_recoveries']}",
                f"- Stale ownership incidents: {project['stale_ownership_incidents']}",
                f"- Average run latency (s): {project['average_run_latency_seconds']}",
                f"- Failure rate: {project['per_project_failure_rate']:.2%}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def build_weekly_review(projects: list[dict[str, object]], summary: dict[str, object]) -> str:
    lines = [
        "# Synthetic Alpha Weekly Review Draft",
        "",
        f"Generated: {utc_now().isoformat()}",
        f"Window start: {window_start_for('weekly').isoformat()}",
        "",
        "## Executive Summary",
        f"- Total runs started this week: {summary['runs_started']}",
        f"- Total failures this week: {summary['runs_failed']}",
        f"- Total interventions this week: {summary['intervention_count']}",
        f"- Mean run latency (s): {summary['average_run_latency_seconds']}",
        "",
        "## Key Risks",
        f"- Browser issues: {summary['failure_classification'].get('browser issue', 0)}",
        f"- Scheduler issues: {summary['failure_classification'].get('scheduler issue', 0)}",
        f"- Policy denials: {summary['failure_classification'].get('policy denial', 0)}",
        f"- Challenge/captcha: {summary['failure_classification'].get('challenge/captcha', 0)}",
        f"- Auth/session issues: {summary['failure_classification'].get('auth/session issue', 0)}",
        f"- Plugin issues: {summary['failure_classification'].get('plugin issue', 0)}",
        "",
        "## Recommendations",
        "- Increase attention on the highest-volume failure bucket before widening alpha scope.",
        "- Review hourly session-restore and delegated-run workloads for repeat regressions.",
        "- Keep chaos scenarios focused on policy-safe and auth-safe boundary conditions.",
        "",
        "## Project Notes",
    ]
    for project in projects:
        lines.append(
            f"- {project['project_alias']}: failure rate {project['per_project_failure_rate']:.2%}, avg latency {project['average_run_latency_seconds']}s, interventions {project['intervention_count']}"
        )
    return "\n".join(lines).strip() + "\n"


def write_example_reports() -> dict[str, dict[str, str]]:
    fixture_projects = [
        {
            "project_alias": "steady",
            "runs_started": 48,
            "runs_completed": 42,
            "runs_failed": 6,
            "intervention_count": 3,
            "browser_crash_count": 1,
            "captcha_challenge_count": 1,
            "session_restore_failures": 2,
            "duplicate_result_recoveries": 1,
            "stale_ownership_incidents": 1,
            "average_run_latency_seconds": 43.8,
            "per_project_failure_rate": 0.125,
            "failure_classification": {
                "browser issue": 2,
                "scheduler issue": 1,
                "policy denial": 1,
                "challenge/captcha": 1,
                "auth/session issue": 1,
                "plugin issue": 0,
                "other": 0,
            },
        },
        {
            "project_alias": "chaos",
            "runs_started": 28,
            "runs_completed": 22,
            "runs_failed": 6,
            "intervention_count": 5,
            "browser_crash_count": 2,
            "captcha_challenge_count": 2,
            "session_restore_failures": 1,
            "duplicate_result_recoveries": 2,
            "stale_ownership_incidents": 1,
            "average_run_latency_seconds": 51.2,
            "per_project_failure_rate": 0.2143,
            "failure_classification": {
                "browser issue": 1,
                "scheduler issue": 2,
                "policy denial": 1,
                "challenge/captcha": 1,
                "auth/session issue": 0,
                "plugin issue": 1,
                "other": 0,
            },
        },
    ]
    summary = overall_metrics(fixture_projects)
    daily_paths = write_text_artifact("example_daily_report.md", build_daily_report(fixture_projects, summary))
    weekly_paths = write_text_artifact("example_weekly_review.md", build_weekly_review(fixture_projects, summary))
    json_paths = write_json_artifact("example_metrics_snapshot.json", {"projects": fixture_projects, "summary": summary})
    return {"daily": daily_paths, "weekly": weekly_paths, "metrics": json_paths}


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
    daily_projects, daily_summary = collect_metrics("daily")
    weekly_projects, weekly_summary = collect_metrics("weekly")
    daily_paths = write_text_artifact(f"daily_report_{timestamp_slug()}.md", build_daily_report(daily_projects, daily_summary))
    weekly_paths = write_text_artifact(f"weekly_alpha_review_{timestamp_slug()}.md", build_weekly_review(weekly_projects, weekly_summary))
    metrics_paths = write_json_artifact(f"metrics_snapshot_{timestamp_slug()}.json", {"daily": {"projects": daily_projects, "summary": daily_summary}, "weekly": {"projects": weekly_projects, "summary": weekly_summary}})
    print({"daily": daily_paths, "weekly": weekly_paths, "metrics": metrics_paths})


def main() -> None:
    args = parse_loop_args("Synthetic Alpha Reporter", default_interval=3600.0)
    run_forever(run_once, once=args.once, interval_seconds=args.interval_seconds)


if __name__ == "__main__":
    main()
