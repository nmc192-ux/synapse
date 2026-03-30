import importlib.util
import sys
from pathlib import Path

import httpx

SWARM_COMMON_PATH = Path("/Users/drj/Documents/Synapse/examples/synthetic_alpha_swarm/common.py")
spec = importlib.util.spec_from_file_location("synthetic_alpha_swarm_common", SWARM_COMMON_PATH)
common = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
sys.modules[spec.name] = common
spec.loader.exec_module(common)


def test_build_agent_definition_uses_continuous_role_limits(monkeypatch) -> None:
    monkeypatch.delenv("SYNTHETIC_ALPHA_SWARM_ROLE_MAX_PAGES", raising=False)
    definition = common.build_agent_definition(
        agent_id="synthetic-alpha-browser-runner-1",
        kind=common.AgentKind.CODEX,
        name="BrowserRunner-1",
        description="test",
        role="browser-runner-1",
    )

    assert definition.limits is not None
    assert definition.limits.max_pages == 20000
    assert definition.limits.max_runtime_seconds == 2_592_000
    assert definition.security.rate_limits.browser_actions_per_minute == 30


def test_retry_with_backoff_retries_http_429(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(common.time, "sleep", lambda delay: sleeps.append(delay))
    monkeypatch.setattr(common.random, "uniform", lambda a, b: 0.0)

    attempts = {"count": 0}

    def action() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            request = httpx.Request("POST", "http://example.test/api/browser/open")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("too many", request=request, response=response)
        return "ok"

    result = common.retry_with_backoff(action, label="browser.open", attempts=4, base_delay_seconds=2.0)

    assert result == "ok"
    assert attempts["count"] == 3
    assert sleeps == [2.0, 4.0]


def test_retry_with_backoff_does_not_retry_non_retryable_http_error(monkeypatch) -> None:
    monkeypatch.setattr(common.time, "sleep", lambda delay: None)
    monkeypatch.setattr(common.random, "uniform", lambda a, b: 0.0)

    request = httpx.Request("POST", "http://example.test/api/browser/open")
    response = httpx.Response(403, request=request)

    def action() -> str:
        raise httpx.HTTPStatusError("forbidden", request=request, response=response)

    try:
        common.retry_with_backoff(action, label="browser.open", attempts=3)
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 403
    else:
        raise AssertionError("expected HTTPStatusError")


def test_register_role_agent_falls_back_to_admin_for_agent_bootstrap(monkeypatch) -> None:
    definition = common.build_agent_definition(
        agent_id="synthetic-alpha-browser-runner-2",
        kind=common.AgentKind.OPENCLAW,
        name="BrowserRunner-2",
        description="test",
        role="browser-runner-2",
    )

    calls: list[str] = []

    class _RoleClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def register_agent(self, _definition):
            calls.append("role")
            raise PermissionError(
                "Authorization failed for POST /api/agents in project 'project-1': Missing required scopes: admin."
            )

    class _AdminClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def register_agent(self, passed_definition):
            calls.append("admin")
            return passed_definition

    monkeypatch.setattr(common, "build_role_client", lambda role_name, agent_id=None: _RoleClient())
    monkeypatch.setattr(common, "build_admin_api", lambda: _AdminClient())

    registered = common.register_role_agent("browser-runner-2", definition)

    assert registered.agent_id == definition.agent_id
    assert calls == ["role", "admin"]


def test_build_project_admin_api_prefers_alias_specific_admin_key(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    class _PlatformAPI:
        def __init__(self, *, base_url, api_key=None, bearer_token=None, project_id=None, timeout=30.0):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            captured["bearer_token"] = bearer_token
            captured["project_id"] = project_id

    monkeypatch.setattr(common, "PlatformAPI", _PlatformAPI)
    monkeypatch.setattr(common, "build_project_credentials", lambda alias: common.ProjectCredentials(alias=alias, project_id="project-chaos", api_key="shared-key"))
    monkeypatch.setattr(common, "env", lambda name, default=None: "http://127.0.0.1:8000")
    monkeypatch.setattr(
        common,
        "optional_env",
        lambda name, default=None: {"SYNTHETIC_ALPHA_SWARM_CHAOS_ADMIN_API_KEY": "chaos-admin-key"}.get(name, default),
    )

    common.build_project_admin_api("chaos")

    assert captured == {
        "base_url": "http://127.0.0.1:8000",
        "api_key": "chaos-admin-key",
        "bearer_token": None,
        "project_id": "project-chaos",
    }


def test_director_schedule_catalog_uses_chaos_browser_runner_identity() -> None:
    director_path = Path("/Users/drj/Documents/Synapse/examples/synthetic_alpha_swarm/director.py")
    sys.path.insert(0, str(director_path.parent))
    spec = importlib.util.spec_from_file_location("synthetic_alpha_swarm_director", director_path)
    director = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = director
    spec.loader.exec_module(director)

    plans = director.schedule_catalog()["hourly"]
    chaos_agent_ids = {plan["task_request"].agent_id for plan in plans if plan["project_alias"] == "chaos"}

    assert chaos_agent_ids == {"synthetic-alpha-chaos-browser-runner-2"}
