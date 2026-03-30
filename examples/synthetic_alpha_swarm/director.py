from __future__ import annotations

from common import (
    SCHEDULE_WINDOWS,
    build_agent_definition,
    build_project_api,
    build_run_plan,
    default_safe_urls,
    env,
    load_json_state,
    parse_loop_args,
    register_role_agent,
    role_project_alias,
    run_forever,
    save_json_state,
    send_signed_role_message,
    start_role_a2a_listener,
    summarize_run,
    timestamp_slug,
    utc_now,
    write_json_artifact,
)
from synapse.models.agent import AgentKind
from synapse.models.task import TaskRequest

DIRECTOR_AGENT_ID = "synthetic-alpha-director"
STATE_FILE = "director_schedule_state.json"
_A2A_LISTENER = None


def ensure_a2a_listener() -> None:
    global _A2A_LISTENER
    if _A2A_LISTENER is None:
        _A2A_LISTENER = start_role_a2a_listener("director", DIRECTOR_AGENT_ID)


def schedule_catalog() -> dict[str, list[dict[str, object]]]:
    urls = default_safe_urls()
    return {
        "quarter_hourly": [
            build_run_plan(
                project_alias="steady",
                agent_id="synthetic-alpha-browser-runner-1",
                label="docs-check",
                goal="Open a docs page and produce a short deterministic browsing summary for synthetic alpha telemetry.",
                start_url=urls[0],
                context_label="tenant-steady-docs",
                extra_constraints={"schedule_tier": "quarter_hourly", "theme": "docs"},
            ),
            build_run_plan(
                project_alias="steady",
                agent_id="synthetic-alpha-browser-runner-1",
                label="public-news-check",
                goal="Open a safe public developer news page and summarize the top visible heading and lead content.",
                start_url=urls[2],
                context_label="tenant-steady-news",
                extra_constraints={"schedule_tier": "quarter_hourly", "theme": "news"},
            ),
            build_run_plan(
                project_alias="chaos",
                agent_id="synthetic-alpha-chaos-browser-runner-2",
                label="public-research-check",
                goal="Open a public research page and extract the title plus abstract heading as a synthetic-alpha research task.",
                start_url=urls[4],
                context_label="tenant-chaos-research",
                extra_constraints={"schedule_tier": "quarter_hourly", "theme": "research"},
            ),
        ],
        "hourly": [
            build_run_plan(
                project_alias="steady",
                agent_id="synthetic-alpha-browser-runner-1",
                label="session-restore-check",
                goal="Perform a session restore validation by reopening a prior-safe docs page and confirming the main heading remains extractable.",
                start_url=urls[1],
                context_label="tenant-steady-session-restore",
                extra_constraints={"schedule_tier": "hourly", "exercise": "session_restore_check"},
            ),
            build_run_plan(
                project_alias="chaos",
                agent_id="synthetic-alpha-chaos-browser-runner-2",
                label="delegated-run-check",
                goal="Run a delegated synthetic task across the chaos lane and record any duplicate-result or stale-ownership recoveries.",
                start_url=urls[5],
                context_label="tenant-chaos-delegated",
                extra_constraints={"schedule_tier": "hourly", "exercise": "delegated_run", "delegation_expected": True},
            ),
            build_run_plan(
                project_alias="chaos",
                agent_id="synthetic-alpha-chaos-browser-runner-2",
                label="intervention-trigger-check",
                goal="Open a safe page while simulating an intervention-triggering approval path without using sensitive credentials.",
                start_url=urls[3],
                context_label="tenant-chaos-intervention",
                extra_constraints={"schedule_tier": "hourly", "exercise": "intervention_trigger", "expect_intervention": True},
            ),
        ],
        "daily": [
            build_run_plan(
                project_alias="chaos",
                agent_id="synthetic-alpha-chaos-monkey",
                label="daily-chaos",
                goal="Execute the daily safe chaos validation and confirm policy-denial and missing-selector handling are observable.",
                start_url=urls[0],
                context_label="tenant-chaos-daily-chaos",
                extra_constraints={"schedule_tier": "daily", "exercise": "chaos_run"},
            ),
            build_run_plan(
                project_alias="steady",
                agent_id="synthetic-alpha-reporter",
                label="daily-health",
                goal="Run a daily health check against the synthetic alpha harness and summarize worker, run, and intervention health.",
                start_url=urls[1],
                context_label="tenant-steady-daily-health",
                extra_constraints={"schedule_tier": "daily", "exercise": "health_check"},
            ),
            build_run_plan(
                project_alias="steady",
                agent_id="synthetic-alpha-reporter",
                label="daily-summary",
                goal="Trigger the daily alpha summary generation workflow for steady and chaos project telemetry.",
                start_url=urls[5],
                context_label="tenant-steady-daily-summary",
                extra_constraints={"schedule_tier": "daily", "exercise": "summary_report"},
            ),
        ],
    }


def due_windows() -> list[str]:
    state = load_json_state(STATE_FILE, {})
    now = utc_now().timestamp()
    due: list[str] = []
    for label, seconds in SCHEDULE_WINDOWS.items():
        previous = float(state.get(label, 0.0))
        if now - previous >= seconds:
            due.append(label)
            state[label] = now
    save_json_state(STATE_FILE, state)
    return due


def run_once() -> None:
    register_role_agent(
        "director",
        build_agent_definition(
            agent_id=DIRECTOR_AGENT_ID,
            kind=AgentKind.OPENCLAW,
            name="Synthetic Alpha Director",
            description="Schedules recurring baseline and chaos workloads across separate Synapse projects.",
            role="director",
            allowed_tools=["web.search", "github.search"],
            extra_tags=["scheduler", "planner", "continuous"],
        ),
    )
    ensure_a2a_listener()
    catalog = schedule_catalog()
    windows = due_windows() if env("SYNTHETIC_ALPHA_SWARM_DIRECTOR_ENABLE_SCHEDULE", "true").lower() == "true" else ["quarter_hourly", "hourly", "daily"]
    plans = [plan for window in windows for plan in catalog.get(window, [])]
    submitted_runs: list[dict[str, object]] = []
    if env("SYNTHETIC_ALPHA_SWARM_DIRECTOR_SUBMIT_RUNS", "true").lower() == "true":
        for plan in plans:
            project_alias = str(plan["project_alias"])
            task_request = TaskRequest.model_validate(plan["task_request"])
            with build_project_api(project_alias) as api:
                run = api.create_project_run(api.project_id or "", task_request)
                submitted_runs.append(summarize_run(run))
    a2a_dispatches: list[dict[str, object]] = []
    for target_agent in ("synthetic-alpha-browser-runner-1",):
        target_status: dict[str, object] | None = None
        with build_project_api("steady") as api:
            target_status = api.get_agent_status(target_agent)
        if not bool(target_status.get("availability")):
            a2a_dispatches.append({
                "recipient_agent_id": target_agent,
                "status": "skipped_unavailable",
                "availability": target_status.get("availability"),
                "agent_status": target_status.get("status"),
                "last_seen_at": target_status.get("last_seen_at"),
            })
            continue
        try:
            dispatch = send_signed_role_message(
                role_name="director",
                sender_agent_id=DIRECTOR_AGENT_ID,
                recipient_agent_id=target_agent,
                payload={
                    "kind": "synthetic-alpha-control",
                    "command": "heartbeat",
                    "issued_at": utc_now().isoformat(),
                    "windows_triggered": windows,
                    "submitted_run_count": len(submitted_runs),
                },
            )
            a2a_dispatches.append({
                "recipient_agent_id": target_agent,
                "message_id": dispatch.message_id,
                "nonce": dispatch.nonce,
                "status": "sent",
            })
        except Exception as exc:
            a2a_dispatches.append({
                "recipient_agent_id": target_agent,
                "status": "error",
                "detail": str(exc),
            })
    artifact = write_json_artifact(
        f"director_schedule_{timestamp_slug()}.json",
        {
            "windows_triggered": windows,
            "plans": plans,
            "submitted_runs": submitted_runs,
            "schedule_enabled": True,
            "a2a_dispatches": a2a_dispatches,
        },
    )
    print({"artifacts": artifact, "windows_triggered": windows, "submitted_runs": len(submitted_runs)})


def main() -> None:
    args = parse_loop_args("Synthetic Alpha Director", default_interval=300.0)
    run_forever(
        run_once,
        once=args.once,
        interval_seconds=args.interval_seconds,
        role_name="director",
        agent_id=DIRECTOR_AGENT_ID,
        project_alias=role_project_alias("director"),
    )


if __name__ == "__main__":
    main()
