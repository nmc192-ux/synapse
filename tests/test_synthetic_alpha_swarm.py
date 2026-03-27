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
