from __future__ import annotations

from common import (
    build_agent_definition,
    build_role_api,
    build_role_client,
    default_safe_urls,
    parse_loop_args,
    register_role_agent,
    retry_with_backoff,
    role_project_alias,
    run_forever,
    start_role_a2a_listener,
    timestamp_slug,
    write_json_artifact,
)
from synapse.models.agent import AgentChallengePolicy, AgentKind


_A2A_LISTENER = None


def ensure_a2a_listener() -> None:
    global _A2A_LISTENER
    if _A2A_LISTENER is None:
        _A2A_LISTENER = start_role_a2a_listener("chaos-monkey", "synthetic-alpha-chaos-monkey")


def run_once() -> None:
    register_role_agent(
        "chaos-monkey",
        build_agent_definition(
            agent_id="synthetic-alpha-chaos-monkey",
            kind=AgentKind.OPENCLAW,
            name="Synthetic Alpha ChaosMonkey",
            description="Injects controlled safe failures against public sites and policy boundaries.",
            role="chaos-monkey",
            allowed_tools=[],
            extra_tags=["chaos", "safe-failure"],
            challenge_policy=AgentChallengePolicy.PAUSE,
        ),
    )
    ensure_a2a_listener()

    failures: list[dict[str, str]] = []
    with build_role_client("chaos-monkey", agent_id="synthetic-alpha-chaos-monkey") as client:
        browser = client.browser
        try:
            retry_with_backoff(
                lambda: browser.open(default_safe_urls()[0]),
                label="chaos-monkey:browser.open",
                telemetry_context={
                    "project_alias": role_project_alias("chaos-monkey"),
                    "project_id": getattr(client, "project_id", None),
                    "role": "chaos-monkey",
                    "agent_id": "synthetic-alpha-chaos-monkey",
                },
            )
            try:
                browser.inspect("#this-selector-does-not-exist")
            except Exception as exc:  # pragma: no cover - scaffold path
                failures.append({"scenario": "missing-selector", "outcome": "expected-failure", "detail": str(exc)})

            try:
                browser.open("https://example.com")
            except Exception as exc:  # pragma: no cover - scaffold path
                failures.append({"scenario": "blocked-domain", "outcome": "expected-failure", "detail": str(exc)})
        finally:
            browser.close()

    with build_role_api("chaos-monkey") as api:
        workers = [worker.model_dump(mode="json") for worker in api.list_workers()]

    artifact = write_json_artifact(
        f"chaos_report_{timestamp_slug()}.json",
        {"expected_failures": failures, "worker_snapshot": workers},
    )
    print({"artifact": str(artifact), "expected_failures": failures, "workers": len(workers)})


def main() -> None:
    args = parse_loop_args("Synthetic Alpha ChaosMonkey")
    run_forever(
        run_once,
        once=args.once,
        interval_seconds=args.interval_seconds,
        role_name="chaos-monkey",
        agent_id="synthetic-alpha-chaos-monkey",
        project_alias=role_project_alias("chaos-monkey"),
    )


if __name__ == "__main__":
    main()
