from __future__ import annotations

from common import (
    build_agent_definition,
    build_project_api,
    build_run_plan,
    default_safe_urls,
    env,
    parse_loop_args,
    register_role_agent,
    role_project_alias,
    run_forever,
    summarize_run,
    timestamp_slug,
    write_json_artifact,
)
from synapse.models.agent import AgentKind
from synapse.models.task import TaskRequest

DIRECTOR_AGENT_ID = "synthetic-alpha-director"


def build_plans() -> list[dict[str, object]]:
    urls = default_safe_urls()
    return [
        build_run_plan(
            project_alias="steady",
            agent_id="synthetic-alpha-browser-runner-1",
            label="steady-docs",
            goal="Open a documentation page, inspect the main content, and capture a deterministic synthetic-alpha summary.",
            start_url=urls[0],
            context_label="tenant-steady-docs",
            extra_constraints={"plan_role": "browser-runner-1", "schedule_lane": "baseline"},
        ),
        build_run_plan(
            project_alias="steady",
            agent_id="synthetic-alpha-browser-runner-1",
            label="steady-wiki",
            goal="Open a public reference article and record headings and page layout for tenancy-safe browser coverage.",
            start_url=urls[2],
            context_label="tenant-steady-reference",
            extra_constraints={"plan_role": "browser-runner-1", "schedule_lane": "baseline"},
        ),
        build_run_plan(
            project_alias="chaos",
            agent_id="synthetic-alpha-browser-runner-2",
            label="chaos-arxiv",
            goal="Open a public research abstract page and generate an observation-only synthetic run for the chaos lane.",
            start_url=urls[3],
            context_label="tenant-chaos-research",
            extra_constraints={"plan_role": "browser-runner-2", "schedule_lane": "chaos"},
        ),
        build_run_plan(
            project_alias="chaos",
            agent_id="synthetic-alpha-browser-runner-2",
            label="chaos-github",
            goal="Open a public GitHub README page and exercise documentation-safe browsing without credentials.",
            start_url=urls[4],
            context_label="tenant-chaos-docs",
            extra_constraints={"plan_role": "browser-runner-2", "schedule_lane": "chaos"},
        ),
    ]


def run_once() -> None:
    register_role_agent(
        role_project_alias("director"),
        build_agent_definition(
            agent_id=DIRECTOR_AGENT_ID,
            kind=AgentKind.OPENCLAW,
            name="Synthetic Alpha Director",
            description="Schedules baseline and chaos swarm workloads across separate Synapse projects.",
            role="director",
            allowed_tools=["web.search", "github.search"],
            extra_tags=["scheduler", "planner"],
        ),
    )
    plans = build_plans()
    submitted_runs: list[dict[str, object]] = []
    if env("SYNTHETIC_ALPHA_SWARM_DIRECTOR_SUBMIT_RUNS", "true").lower() == "true":
        for plan in plans:
            project_alias = str(plan["project_alias"])
            task_request = TaskRequest.model_validate(plan["task_request"])
            with build_project_api(project_alias) as api:
                run = api.create_project_run(api.project_id or "", task_request)
                submitted_runs.append(summarize_run(run))
    artifact = write_json_artifact(
        f"director_run_plan_{timestamp_slug()}.json",
        {"plans": plans, "submitted_runs": submitted_runs},
    )
    print({"artifact": str(artifact), "plans": len(plans), "submitted_runs": submitted_runs})


def main() -> None:
    args = parse_loop_args("Synthetic Alpha Director")
    run_forever(run_once, once=args.once, interval_seconds=args.interval_seconds)


if __name__ == "__main__":
    main()
