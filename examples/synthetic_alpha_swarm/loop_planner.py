from __future__ import annotations

import argparse
import subprocess
from typing import Any

from common import run_forever, timestamp_slug, utc_now, write_report_json, write_report_text


def _dominant_backlog_subtype(summary: dict[str, Any]) -> tuple[str, int]:
    subtypes = summary.get("request_backlog_subtypes", {})
    if not isinstance(subtypes, dict) or not subtypes:
        return "none", 0
    name, count = next(iter(subtypes.items()))
    return str(name), int(count)


def _phase_for_subtype(subtype: str) -> dict[str, Any]:
    normalized = subtype.strip().lower()
    if normalized.endswith("bootstrap_claimed_not_entered"):
        return {
            "phase_key": "bootstrap_claimed_not_entered",
            "title": "Fix claimed-not-entered bootstrap gap",
            "task": "Implement the next focused runtime slice to reduce create_session requests that are claimed by a worker but never enter execution.",
            "primary_files": [
                "src/synapse/runtime/browser_workers.py",
                "src/synapse/workers/browser_worker.py",
                "src/synapse/runtime/queues.py",
                "tests/test_browser_workers.py",
            ],
            "commit_message": "fix: reduce claimed bootstrap execution gaps",
            "validation_focus": [
                "whether session_bootstrap_claimed_not_entered appears or falls",
                "whether worker_claimed_at is present more often than execution_started_at in failed bootstrap requests",
                "whether bootstrap_not_started volume shifts into the narrower claimed-not-entered bucket",
            ],
        }
    if normalized.endswith("bootstrap_not_started") or normalized.endswith("generic_timeout_before_start"):
        return {
            "phase_key": "bootstrap_not_started",
            "title": "Reduce bootstrap pre-start starvation",
            "task": "Implement the next focused runtime slice to reduce create_session requests that remain dispatched but do not reach worker claim or execution entry.",
            "primary_files": [
                "src/synapse/runtime/browser_workers.py",
                "src/synapse/workers/browser_worker.py",
                "src/synapse/runtime/queues.py",
                "src/synapse/runtime/runtime_controller.py",
                "tests/test_browser_workers.py",
            ],
            "commit_message": "fix: reduce bootstrap pre-start starvation",
            "validation_focus": [
                "whether operator_required:bootstrap_not_started drops",
                "whether failed bootstrap requests still lack worker_claimed_at entirely",
                "whether queue claim or admission remains the dominant missing lifecycle edge",
            ],
        }
    if "bootstrap_stalled" in normalized or "started_no_durable_progress" in normalized:
        return {
            "phase_key": "bootstrap_started_no_progress",
            "title": "Reduce bootstrap started-no-progress tail",
            "task": "Implement the next focused runtime slice to improve create_session convergence after execution start but before first durable bootstrap progress.",
            "primary_files": [
                "src/synapse/runtime/browser_workers.py",
                "src/synapse/workers/browser_worker.py",
                "src/synapse/runtime/run_store.py",
                "tests/test_browser_workers.py",
            ],
            "commit_message": "fix: reduce bootstrap started no progress tail",
            "validation_focus": [
                "whether operator_required:bootstrap_stalled drops",
                "whether execution_started_at is present without first_progress_at on failing bootstrap requests",
                "whether started bootstrap requests converge more often before operator escalation",
            ],
        }
    if "ownership_conflict" in normalized or "lease_" in normalized:
        return {
            "phase_key": "ownership_or_lease",
            "title": "Reduce lease and ownership disruption backlog",
            "task": "Implement the next focused runtime slice to reduce browser-request backlog caused by lease movement or repeated ownership conflicts.",
            "primary_files": [
                "src/synapse/runtime/browser_workers.py",
                "src/synapse/runtime/run_store.py",
                "src/synapse/runtime/scheduler.py",
                "tests/test_browser_workers.py",
            ],
            "commit_message": "fix: reduce lease ownership backlog",
            "validation_focus": [
                "whether ownership_conflict and lease-related backlog subtypes fall",
                "whether stale ownership incidents drop",
                "whether abandoned requests are reduced without hiding real lease movement",
            ],
        }
    return {
        "phase_key": "generic_backlog",
        "title": "Reduce dominant synthetic-alpha backlog subtype",
        "task": "Implement the next focused runtime slice against the currently dominant backlog subtype using the latest synthetic-alpha report as the source of truth.",
        "primary_files": [
            "src/synapse/runtime/browser_workers.py",
            "src/synapse/runtime/run_store.py",
            "tests/test_browser_workers.py",
            "tests/test_run_state.py",
        ],
        "commit_message": "fix: reduce dominant backlog subtype",
        "validation_focus": [
            "whether the top backlog subtype falls",
            "whether unresolved and operator_required pressure improve",
            "whether a narrower next bottleneck emerges cleanly",
        ],
    }


def derive_next_iteration(summary_payload: dict[str, Any]) -> dict[str, Any]:
    daily = summary_payload.get("daily", {})
    daily_summary = daily.get("summary", {}) if isinstance(daily, dict) else {}
    weekly = summary_payload.get("weekly", {})
    weekly_summary = weekly.get("summary", {}) if isinstance(weekly, dict) else {}
    dominant_subtype, dominant_count = _dominant_backlog_subtype(daily_summary)
    phase = _phase_for_subtype(dominant_subtype)
    request_health = daily_summary.get("request_health_summary", {})
    return {
        "generated_at": utc_now().isoformat(),
        "phase_key": phase["phase_key"],
        "phase_title": phase["title"],
        "task": phase["task"],
        "dominant_backlog_subtype": dominant_subtype,
        "dominant_backlog_count": dominant_count,
        "request_health_summary": dict(request_health) if isinstance(request_health, dict) else {},
        "waiting_for_operator_runs": int(daily_summary.get("waiting_for_operator_runs", 0)),
        "operator_required": int((request_health or {}).get("operator_required", 0)) if isinstance(request_health, dict) else 0,
        "unresolved": int((request_health or {}).get("unresolved", 0)) if isinstance(request_health, dict) else 0,
        "operator_review_timed_out": int((request_health or {}).get("operator_review_timed_out", 0)) if isinstance(request_health, dict) else 0,
        "daily_runs_started": int(daily_summary.get("runs_started", 0)),
        "daily_runs_completed": int(daily_summary.get("runs_completed", 0)),
        "daily_average_latency_seconds": float(daily_summary.get("average_run_latency_seconds", 0.0)),
        "weekly_runs_started": int(weekly_summary.get("runs_started", 0)) if isinstance(weekly_summary, dict) else 0,
        "primary_files": phase["primary_files"],
        "commit_message": phase["commit_message"],
        "validation_focus": phase["validation_focus"],
    }


def build_macbook_brief(plan: dict[str, Any]) -> str:
    primary_files = "\n".join(f"- {path}" for path in plan["primary_files"])
    validation_focus = "\n".join(f"- {item}" for item in plan["validation_focus"])
    return f"""Continue Synapse development from the latest main branch.

Current live validation context:
- The latest synthetic-alpha loop analysis shows the dominant backlog subtype is:
  - {plan['dominant_backlog_subtype']} = {plan['dominant_backlog_count']}
- Current pressure remains:
  - operator_required: {plan['operator_required']}
  - unresolved: {plan['unresolved']}
  - waiting_for_operator_runs: {plan['waiting_for_operator_runs']}
  - operator_review_timed_out: {plan['operator_review_timed_out']}

Interpretation:
- The highest-signal next target is now: {plan['phase_title']}
- This iteration should stay narrow and measurable.

Task:
{plan['task']}

Primary files:
{primary_files}

Validation focus:
{validation_focus}

Commit message:
{plan['commit_message']}
"""


def current_git_head() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], text=True)
            .strip()
        )
    except Exception:
        return "unknown"


def build_mac_mini_validation_prompt(plan: dict[str, Any], *, commit_head: str | None = None) -> str:
    head = commit_head or current_git_head()
    validation_focus = "\n".join(f"   - {item}" for item in plan["validation_focus"])
    return f"""Update and validate the live Synapse synthetic-alpha stack on this Mac Mini against the latest main branch.

Important context:
- Latest upstream head to deploy is:
  - {head}
- Planned focus for this iteration:
  - {plan['phase_title']}
- Dominant backlog subtype before this deployment:
  - {plan['dominant_backlog_subtype']} = {plan['dominant_backlog_count']}

What this deployment is intended to improve:
{validation_focus}

Your task:
1. Pull latest `main` in the Synapse repo on this Mac Mini.
2. Sync the repo into the live supervised runtime copy if this machine uses one.
3. Restart the synthetic-alpha stack cleanly.
4. Validate whether this iteration changed the dominant backlog shape.
5. Report whether the next bottleneck is now clearer.

Output:
- deployed commit
- service/stack health
- latest synthetic-alpha metrics
- delta vs prior snapshot
- top backlog subtypes overall
- whether {plan['dominant_backlog_subtype']} changed materially
- highest-signal remaining issue after this update
"""


def write_next_iteration_artifacts(summary_payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    plan = derive_next_iteration(summary_payload)
    slug = timestamp_slug()
    plan_paths = write_report_json(f"development_loop_{slug}.json", plan)
    latest_plan_paths = write_report_json("development_loop_latest.json", plan)
    macbook_paths = write_report_text(f"macbook_iteration_brief_{slug}.txt", build_macbook_brief(plan))
    latest_macbook_paths = write_report_text("macbook_iteration_brief_latest.txt", build_macbook_brief(plan))
    return {
        "plan": plan_paths,
        "plan_latest": latest_plan_paths,
        "macbook": macbook_paths,
        "macbook_latest": latest_macbook_paths,
    }


def write_mac_mini_validation_artifacts(summary_payload: dict[str, Any], *, commit_head: str | None = None) -> dict[str, dict[str, str]]:
    plan = derive_next_iteration(summary_payload)
    head = commit_head or current_git_head()
    slug = timestamp_slug()
    validation_paths = write_report_text(
        f"mac_mini_validation_prompt_{slug}.txt",
        build_mac_mini_validation_prompt(plan, commit_head=head),
    )
    latest_validation_paths = write_report_text(
        "mac_mini_validation_prompt_latest.txt",
        build_mac_mini_validation_prompt(plan, commit_head=head),
    )
    return {"mac_mini": validation_paths, "mac_mini_latest": latest_validation_paths}


def run_once(*, prepare_validation: bool = False) -> None:
    from reporter import collect_metrics

    daily_projects, daily_summary = collect_metrics("daily")
    weekly_projects, weekly_summary = collect_metrics("weekly")
    payload = {"daily": {"projects": daily_projects, "summary": daily_summary}, "weekly": {"projects": weekly_projects, "summary": weekly_summary}}
    outputs = write_next_iteration_artifacts(payload)
    if prepare_validation:
        outputs["validation"] = write_mac_mini_validation_artifacts(payload)
    print(outputs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic Alpha Loop Planner")
    parser.add_argument("--once", action="store_true", help="Run a single iteration and exit.")
    parser.add_argument("--interval-seconds", type=float, default=3600.0, help="Loop interval for continuous execution.")
    parser.add_argument(
        "--prepare-validation",
        action="store_true",
        help="Also emit the latest Mac Mini validation brief using the current git head.",
    )
    args = parser.parse_args()
    run_forever(
        lambda: run_once(prepare_validation=args.prepare_validation),
        once=args.once,
        interval_seconds=args.interval_seconds,
        role_name="reporter",
        agent_id="synthetic-alpha-loop-planner",
        project_alias="steady",
    )


if __name__ == "__main__":
    main()
