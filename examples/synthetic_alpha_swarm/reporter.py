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
    utc_now,
    write_text_artifact,
)
from synapse.models.agent import AgentKind


def build_summary_window(window_label: str) -> str:
    lines = [f"# Synthetic Alpha {window_label.title()} Summary", "", f"Generated: {utc_now().isoformat()}", ""]
    for alias in ("steady", "chaos"):
        with build_project_api(alias) as api:
            runs = api.list_runs()
            interventions = api.list_interventions()
            statuses = Counter(run.status.value for run in runs)
            lines.extend(
                [
                    f"## {alias.title()}",
                    f"- Runs: {len(runs)}",
                    f"- Interventions: {len(interventions)}",
                    f"- Status counts: {dict(statuses)}",
                    "",
                ]
            )
    return "\n".join(lines).strip() + "\n"


def run_once() -> None:
    register_role_agent(
        role_project_alias("reporter"),
        build_agent_definition(
            agent_id="synthetic-alpha-reporter",
            kind=AgentKind.OPENCLAW,
            name="Synthetic Alpha Reporter",
            description="Produces daily and weekly summaries for the synthetic-alpha swarm.",
            role="reporter",
            allowed_tools=[],
            extra_tags=["reporting", "summaries"],
        ),
    )
    daily = build_summary_window("daily")
    weekly = build_summary_window("weekly")
    daily_path = write_text_artifact(f"daily_summary_{timestamp_slug()}.md", daily)
    weekly_path = write_text_artifact(f"weekly_summary_{timestamp_slug()}.md", weekly)
    print({"daily": str(daily_path), "weekly": str(weekly_path)})


def main() -> None:
    args = parse_loop_args("Synthetic Alpha Reporter")
    run_forever(run_once, once=args.once, interval_seconds=args.interval_seconds)


if __name__ == "__main__":
    main()
