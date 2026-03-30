import importlib.util
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SWARM_COMMON_PATH = REPO_ROOT / "examples" / "synthetic_alpha_swarm" / "common.py"
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


def test_retry_with_backoff_retries_timeout_errors(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(common.time, "sleep", lambda delay: sleeps.append(delay))
    monkeypatch.setattr(common.random, "uniform", lambda a, b: 0.0)

    attempts = {"count": 0}

    def action() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise httpx.ReadTimeout("timed out waiting for browser result")
        return "ok"

    result = common.retry_with_backoff(action, label="browser-runner-1:browser.open", attempts=4, base_delay_seconds=2.0)

    assert result == "ok"
    assert attempts["count"] == 3
    assert sleeps == [2.0, 4.0]


def test_retry_with_backoff_retries_stale_ownership_errors(monkeypatch) -> None:
    sleeps: list[float] = []
    telemetry: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(common.time, "sleep", lambda delay: sleeps.append(delay))
    monkeypatch.setattr(common.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(
        common,
        "record_telemetry_event",
        lambda event_type, **kwargs: telemetry.append((event_type, kwargs)) or {},
    )

    attempts = {"count": 0}

    def action() -> str:
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise KeyError("No browser worker assigned to session: s-stale")
        return "ok"

    result = common.retry_with_backoff(
        action,
        label="browser-runner-1:browser.open",
        attempts=3,
        base_delay_seconds=1.0,
        telemetry_context={"project_alias": "steady", "project_id": "project-1", "role": "browser-runner-1", "agent_id": "synthetic-alpha-browser-runner-1"},
    )

    assert result == "ok"
    assert attempts["count"] == 2
    assert sleeps == [1.0]
    assert any(event_type == "scheduler.stale_ownership" for event_type, _ in telemetry)


def test_build_role_client_uses_extended_default_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(common, "SynapseClient", _Client)
    monkeypatch.setattr(common, "build_role_credentials", lambda role_name: common.RoleCredentials(role=role_name, project_alias="steady", project_id="project-1", api_key="key-1"))
    monkeypatch.setattr(common, "env", lambda name, default=None: "http://127.0.0.1:8000")
    monkeypatch.setattr(common, "optional_env", lambda name, default=None: None)

    common.build_role_client("browser-runner-1", agent_id="agent-1")

    assert captured["timeout"] == common.DEFAULT_SYNTHETIC_ALPHA_CLIENT_TIMEOUT_SECONDS


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
    captured: dict[str, object] = {}

    class _PlatformAPI:
        def __init__(self, *, base_url, api_key=None, bearer_token=None, project_id=None, timeout=30.0):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            captured["bearer_token"] = bearer_token
            captured["project_id"] = project_id
            captured["timeout"] = timeout

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
        "timeout": common.DEFAULT_SYNTHETIC_ALPHA_CLIENT_TIMEOUT_SECONDS,
    }


def test_platform_api_get_run_worker_requests_passes_query_params(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _request(method: str, path: str, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = kwargs.get("params")

        class _Response:
            def json(self):
                return [{"health_state": "slow"}]

        return _Response()

    api = common.PlatformAPI(base_url="http://127.0.0.1:8000", api_key="key", project_id="project-1")
    monkeypatch.setattr(api, "_request", _request)

    payload = api.get_run_worker_requests("run-1", session_id="session-1", status="slow")

    assert payload == [{"health_state": "slow"}]
    assert captured == {
        "method": "GET",
        "path": "/api/runs/run-1/worker-requests",
        "params": {"session_id": "session-1", "status": "slow"},
    }
    api.close()


def test_director_schedule_catalog_uses_chaos_browser_runner_identity() -> None:
    director_path = REPO_ROOT / "examples" / "synthetic_alpha_swarm" / "director.py"
    sys.path.insert(0, str(director_path.parent))
    spec = importlib.util.spec_from_file_location("synthetic_alpha_swarm_director", director_path)
    director = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = director
    spec.loader.exec_module(director)

    plans = director.schedule_catalog()["hourly"]
    chaos_agent_ids = {plan["task_request"].agent_id for plan in plans if plan["project_alias"] == "chaos"}

    assert chaos_agent_ids == {"synthetic-alpha-chaos-browser-runner-2"}


def test_browser_runner_starts_project_runtime_listener(monkeypatch) -> None:
    browser_runner_path = REPO_ROOT / "examples" / "synthetic_alpha_swarm" / "browser_runner.py"
    sys.path.insert(0, str(browser_runner_path.parent))
    spec = importlib.util.spec_from_file_location("synthetic_alpha_swarm_browser_runner_runtime", browser_runner_path)
    browser_runner = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = browser_runner
    spec.loader.exec_module(browser_runner)

    calls: list[tuple[str, str]] = []
    synced: list[tuple[str, object]] = []
    monkeypatch.setattr(
        browser_runner,
        "register_role_agent",
        lambda role_name, definition: calls.append(("register", role_name)) or definition,
    )
    monkeypatch.setattr(
        browser_runner,
        "ensure_a2a_listener",
        lambda role_name: calls.append(("a2a", role_name)),
    )
    monkeypatch.setattr(
        browser_runner,
        "ensure_project_runtime_listener",
        lambda project_alias: calls.append(("runtime", project_alias)),
    )
    monkeypatch.setattr(
        browser_runner,
        "sync_project_runtime_events",
        lambda project_alias, since: synced.append((project_alias, since)) or 0,
    )
    monkeypatch.setattr(
        browser_runner,
        "sync_project_request_health",
        lambda project_alias, since: synced.append((f"{project_alias}:request-health", since)) or 0,
    )
    monkeypatch.setattr(browser_runner, "env_bool", lambda name, default=False: False)
    monkeypatch.setattr(
        browser_runner,
        "build_project_api",
        lambda alias: type(
            "_API",
            (),
            {
                "__enter__": lambda self: self,
                "__exit__": lambda self, *_args: None,
                "project_id": f"{alias}-project",
                "create_project_run": lambda self, project_id, request: request,
            },
        )(),
    )
    monkeypatch.setattr(
        browser_runner,
        "build_run_plan",
        lambda **kwargs: {
            "task_request": browser_runner.TaskRequest(
                task_id="task-1",
                agent_id=kwargs["agent_id"],
                goal="goal",
                start_url="https://example.com",
            )
        },
    )
    monkeypatch.setattr(browser_runner, "summarize_run", lambda run: {"run": "ok"})
    monkeypatch.setattr(browser_runner, "write_json_artifact", lambda *args, **kwargs: Path("/tmp/browser-runner.json"))

    browser_runner.run_once("browser-runner-1")

    assert ("runtime", "steady") in calls
    assert synced and synced[0][0] == "steady"
    assert any(alias == "steady:request-health" for alias, _ in synced)
