from __future__ import annotations

import argparse
from datetime import timedelta
from typing import Any
from uuid import uuid4

from common import (
    build_agent_definition,
    build_project_api,
    build_role_client,
    build_run_plan,
    default_safe_urls,
    ensure_project_runtime_listener,
    env_bool,
    load_json_state,
    register_role_agent,
    retry_with_backoff,
    role_project_alias,
    run_forever,
    save_json_state,
    send_signed_role_message,
    sleep_with_jitter,
    start_role_a2a_listener,
    summarize_run,
    sync_project_runtime_events,
    timestamp_slug,
    utc_now,
    write_json_artifact,
)
from synapse.models.agent import AgentKind
from synapse.models.task import TaskRequest

RUNNER_CONFIG: dict[str, dict[str, str]] = {
    "browser-runner-1": {
        "agent_id": "synthetic-alpha-browser-runner-1",
        "name": "BrowserRunner-1",
        "description": "Baseline browser runner for public docs and reference browsing in the steady project.",
    },
    "browser-runner-2": {
        "agent_id": "synthetic-alpha-chaos-browser-runner-2",
        "name": "BrowserRunner-2",
        "description": "Chaos-lane browser runner for safe tenancy and failure exercises in the chaos project.",
    },
}

_A2A_LISTENERS: dict[str, object] = {}


def ensure_a2a_listener(runner: str) -> None:
    if runner in _A2A_LISTENERS:
        return

    config = RUNNER_CONFIG[runner]
    agent_id = config["agent_id"]

    def _handle(message) -> None:
        if str(message.type) != 'A2AMessageType.SEND_MESSAGE' and getattr(message.type, 'value', '') != 'SEND_MESSAGE':
            return
        payload = dict(message.payload)
        if payload.get('kind') != 'synthetic-alpha-control':
            return
        write_json_artifact(
            f"{runner}_a2a_control_{timestamp_slug()}.json",
            {
                'runner': runner,
                'agent_id': agent_id,
                'received_at': utc_now().isoformat(),
                'message': message.model_dump(mode='json'),
            },
        )
        send_signed_role_message(
            role_name=runner,
            sender_agent_id=agent_id,
            recipient_agent_id='synthetic-alpha-director',
            payload={
                'kind': 'synthetic-alpha-control-ack',
                'source_role': runner,
                'received_message_id': message.message_id,
                'received_at': utc_now().isoformat(),
            },
            nonce=f"{runner}-ack-{uuid4().hex}",
        )

    _A2A_LISTENERS[runner] = start_role_a2a_listener(runner, agent_id, message_handler=_handle)


def parse_args(forced_runner: str | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthetic Alpha Browser Runner")
    if forced_runner is None:
        parser.add_argument("--runner", choices=sorted(RUNNER_CONFIG), required=True)
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    args = parser.parse_args()
    if forced_runner is not None:
        args.runner = forced_runner
    return args


def smoke_urls(runner: str) -> list[str]:
    urls = default_safe_urls()
    if runner == "browser-runner-1":
        return urls[:3]
    return [urls[0], urls[1], urls[2]]


def next_smoke_url(runner: str) -> str:
    state_name = f"{runner}_smoke_state.json"
    urls = smoke_urls(runner)
    state = load_json_state(state_name, {"index": 0})
    index = int(state.get("index", 0)) % len(urls)
    save_json_state(state_name, {"index": (index + 1) % len(urls)})
    return urls[index]


def run_once(runner: str) -> None:
    config = RUNNER_CONFIG[runner]
    project_alias = role_project_alias(runner)
    definition = build_agent_definition(
        agent_id=config["agent_id"],
        kind=AgentKind.CODEX if runner == "browser-runner-1" else AgentKind.OPENCLAW,
        name=config["name"],
        description=config["description"],
        role=runner,
        extra_tags=["browser", "synthetic-runner"],
    )
    register_role_agent(runner, definition)
    ensure_a2a_listener(runner)
    ensure_project_runtime_listener(project_alias)

    direct_results: list[dict[str, Any]] = []
    if env_bool("SYNTHETIC_ALPHA_SWARM_DIRECT_BROWSER_SMOKE", False):
        with build_role_client(runner, agent_id=config["agent_id"]) as client:
            browser = client.browser
            url = next_smoke_url(runner)
            telemetry_context = {
                "project_alias": project_alias,
                "project_id": getattr(client, "project_id", None),
                "role": runner,
                "agent_id": config["agent_id"],
            }
            try:
                opened = retry_with_backoff(
                    lambda: browser.open(url),
                    label=f"{runner}:browser.open",
                    telemetry_context=telemetry_context,
                )
                sleep_with_jitter(1.0, jitter_seconds=0.5)
                extracted = retry_with_backoff(
                    lambda: browser.extract("h1"),
                    label=f"{runner}:browser.extract",
                    telemetry_context=telemetry_context,
                )
                direct_results.append(
                    {
                        "url": url,
                        "session_id": opened.session_id,
                        "title": opened.page.title if opened.page else None,
                        "matches": [match.model_dump(mode="json") for match in extracted.matches[:3]],
                    }
                )
            finally:
                browser.close()

    submitted_runs: list[dict[str, Any]] = []
    plan = build_run_plan(
        project_alias=project_alias,
        agent_id=config["agent_id"],
        label=f"{runner}-selftest",
        goal=f"Run a deterministic browser self-test for {runner} against public safe domains.",
        start_url=smoke_urls(runner)[0],
        context_label=f"{project_alias}-{runner}-selftest",
        extra_constraints={"runner": runner, "source": "browser_runner"},
    )
    with build_project_api(project_alias) as api:
        run = api.create_project_run(api.project_id or "", TaskRequest.model_validate(plan["task_request"]))
        submitted_runs.append(summarize_run(run))

    artifact = write_json_artifact(
        f"{runner}_{timestamp_slug()}.json",
        {"runner": runner, "direct_results": direct_results, "submitted_runs": submitted_runs},
    )
    sync_project_runtime_events(project_alias, utc_now() - timedelta(minutes=15))
    print({"artifact": str(artifact), "runner": runner, "submitted_runs": submitted_runs})


def main(runner: str | None = None) -> None:
    args = parse_args(runner)
    run_forever(
        lambda: run_once(args.runner),
        once=args.once,
        interval_seconds=args.interval_seconds,
        role_name=args.runner,
        agent_id=RUNNER_CONFIG[args.runner]["agent_id"],
        project_alias=role_project_alias(args.runner),
    )


if __name__ == "__main__":
    main()
