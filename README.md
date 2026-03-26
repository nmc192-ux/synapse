# Synapse

Synapse is a browser runtime for autonomous agents.

It provides a Python backend for:

- browser navigation and extraction
- tool execution
- WebSocket event streaming
- multi-agent coordination
- pluggable agent adapters

## Supported agent categories

- OpenClaw agents
- Claude Code agents
- Codex agents
- A2A protocol agents
- custom agents

## Stack

- FastAPI
- Playwright
- WebSockets
- Pydantic

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
export SYNAPSE_POSTGRES_DSN=postgresql://postgres:postgres@localhost:5432/synapse
export SYNAPSE_REDIS_URL=redis://localhost:6379/0
uvicorn synapse.main:app --reload
```

Optional LLM planner configuration:

```bash
export SYNAPSE_LLM_PROVIDER=openai
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-4o-mini
```

Supported providers are `openai`, `anthropic`, and `local`. Local models can be
configured with `SYNAPSE_LOCAL_MODEL_ENDPOINT` and `SYNAPSE_LOCAL_MODEL_NAME`.

Optional compression provider configuration:

```bash
export SYNAPSE_COMPRESSION_PROVIDER=noop
```

Supported compression providers are `noop` and `turboquant`. The TurboQuant
provider is currently a stub integration layer so the runtime can adopt a real
TurboQuant SDK later without changing service interfaces.

Runtime durability configuration:

```bash
export SYNAPSE_REDIS_URL=redis://localhost:6379/0
export SYNAPSE_REDIS_REQUIRED=false
export SYNAPSE_RUNTIME_STATE_FALLBACK_MEMORY=true
```

If Redis is unavailable and fallback is enabled, Synapse logs a warning and uses
in-memory runtime state.

Worker and scheduler configuration:

```bash
export SYNAPSE_BROWSER_WORKER_COUNT=2
export SYNAPSE_BROWSER_WORKER_HEARTBEAT_INTERVAL_SECONDS=15
export SYNAPSE_SCHEDULER_LEASE_TIMEOUT_SECONDS=60
export SYNAPSE_SCHEDULER_MAX_ASSIGNMENT_RETRIES=3
```

## Project layout

```text
src/synapse/
  adapters/      Agent adapter interfaces and built-in implementations
  api/           FastAPI routes
  models/        Pydantic models
  sdk/           Python SDK for agent clients
  runtime/       Browser runtime, orchestration, tools, registry
  transports/    WebSocket connection management
sdk/javascript/  JavaScript SDK for agent clients
ui/              Next.js operator interface
```

## Python SDK

```python
from synapse.models.agent import AgentDefinition, AgentKind, AgentSecurityPolicy
from synapse.sdk import SynapseClient

with SynapseClient("http://127.0.0.1:8000", agent_id="codex") as client:
    client.register_agent(
        AgentDefinition(
            agent_id="codex",
            kind=AgentKind.CODEX,
            name="Codex",
            security=AgentSecurityPolicy(
                allowed_domains=["example.com"],
                allowed_tools=["web.search"],
            ),
        )
    )
    browser = client.browser
    page = browser.open("https://example.com")
    data = browser.extract("h1")
    tool_result = browser.call_tool("web.search", {"query": "Synapse"})
```

Example agents are available in `examples/` for OpenClaw, Codex, and Claude Code.

Agent actions are sandboxed by default. Register each agent with explicit
`allowed_domains`, `allowed_tools`, and rate limits before issuing browser or tool calls.
If `SYNAPSE_LLM_PROVIDER` is configured, the navigation planner will use the selected
LLM provider before falling back to the built-in heuristic planner.

## JavaScript SDK

```bash
cd sdk/javascript
node ./examples/codex-agent.mjs
```

```javascript
import { SynapseClient, createAgentDefinition } from "@synapse-dev/sdk";

const client = new SynapseClient({
  baseUrl: "http://127.0.0.1:8000",
  agentId: "codex-js"
});

await client.registerAgent(
  createAgentDefinition({
    agentId: "codex-js",
    kind: "codex",
    name: "Codex JS",
    allowedDomains: ["example.com"],
    allowedTools: ["github.search"]
  })
);

const browser = client.browser;
await browser.open("https://example.com");
const heading = await browser.extract("h1");
const repos = await browser.callTool("github.search", { query: "browser agents python" });
```

JavaScript example agents are available in [`/Users/jahanzebhussain/Synapse/sdk/javascript/examples/openclaw-agent.mjs`](/Users/jahanzebhussain/Synapse/sdk/javascript/examples/openclaw-agent.mjs), [`/Users/jahanzebhussain/Synapse/sdk/javascript/examples/claude-code-agent.mjs`](/Users/jahanzebhussain/Synapse/sdk/javascript/examples/claude-code-agent.mjs), and [`/Users/jahanzebhussain/Synapse/sdk/javascript/examples/codex-agent.mjs`](/Users/jahanzebhussain/Synapse/sdk/javascript/examples/codex-agent.mjs).

## Next.js UI

```bash
cd ui
npm install
npm run dev
```

The UI renders a Synapse operator dashboard with agent activity, page view, thoughts,
actions log, memory, and agent communication. It listens to `NEXT_PUBLIC_SYNAPSE_WS_URL`
and defaults to `ws://127.0.0.1:8000/api/ws`.

## Fixture Web

Synapse includes a controlled fixture web app for reproducible browsing benchmarks.

Run it locally:

```bash
uvicorn synapse.fixtures.web:app --host 127.0.0.1 --port 8011 --reload
```

The fixture app includes deterministic pages for search/extraction, form filling,
popup dismissal, SPA navigation, upload/download, iframe interaction, lazy loading,
and login/session continuation.

Fixture docs:

- [`/Users/jahanzebhussain/Synapse/docs/fixture-web.md`](/Users/jahanzebhussain/Synapse/docs/fixture-web.md)

## Benchmark Suite

Synapse includes a benchmark scenario catalog and run-scoped scoring layer in
[`/Users/jahanzebhussain/Synapse/src/synapse/runtime/benchmarking.py`](/Users/jahanzebhussain/Synapse/src/synapse/runtime/benchmarking.py).

The default fixture benchmark suite covers:

- extraction
- form completion
- SPA navigation
- popups
- session continuation
- A2A delegated browsing tasks

Scoring aggregates:

- success and failure
- latency
- token usage
- compression savings
- retries
- operator intervention

## Task Execution API

Synapse now includes a PostgreSQL-backed task manager for task creation, claiming,
progress updates, and result submission.

- `POST /api/tasks/create`
- `POST /api/tasks/{task_id}/claim`
- `POST /api/tasks/{task_id}/update`
- `GET /api/tasks/active`
- `POST /api/tasks/{task_id}/checkpoint`
- `POST /api/tasks/resume/{checkpoint_id}`
- `GET /api/checkpoints`
- `GET /api/checkpoints/{checkpoint_id}`

Checkpoint resume flow:
1. Save checkpoint state with task/session/planner context.
2. Restore last persisted browser session metadata.
3. Rehydrate pending planner actions from checkpoint.
4. Continue execution from best-known state if some fields are unavailable.

## Persistent Memory

Synapse also includes persistent agent memory backed by PostgreSQL with `pgvector`.

- `POST /api/memory/store`
- `POST /api/memory/search`
- `GET /api/memory/{agent_id}/recent`

The Python SDK exposes:

```python
client.memory.store(agent_id="codex", memory_type="short_term", content="Observed stable login form.", embedding=[0.1, 0.2, 0.3])
client.memory.search(agent_id="codex", embedding=[0.1, 0.2, 0.3])
client.memory.get_recent(agent_id="codex", limit=5)
```

## Durable Runtime State

Synapse now persists runtime state to Redis with namespace keys:

- `synapse:agents:{agent_id}`
- `synapse:sessions:{session_id}`
- `synapse:profiles:{profile_id}`
- `synapse:connections:{agent_id}`
- `synapse:checkpoints:{checkpoint_id}`
- `synapse:events:{event_id}`

Connection heartbeats update agent liveness. If an A2A connection misses heartbeat
TTL (default `60s`), it is marked stale/offline and emits `connection.stale`.

## Session Profiles

Synapse supports durable authenticated session profiles for restoring browsing state
 across runs.

- `POST /api/profiles/create`
- `POST /api/profiles/{profile_id}/load`
- `GET /api/profiles`
- `DELETE /api/profiles/{profile_id}`

Profiles persist:

- cookies
- local storage snapshots by origin
- session storage metadata by origin
- domain-specific auth state
- expiration metadata

Loading a profile with a `run_id` attaches that profile to the run so the browser
session bootstrap can restore it automatically. Expired profiles emit
`session.profile.expired`.

## Control Plane / Execution Plane

Synapse now splits orchestration from execution:

- control plane: auth, APIs, run creation, scheduling, checkpoints, state persistence, event aggregation
- execution plane: queued browser work, worker heartbeats, local session handling, assigned tool execution

Architecture notes:

- [`/Users/jahanzebhussain/Synapse/docs/architecture/control-plane-execution-plane.md`](/Users/jahanzebhussain/Synapse/docs/architecture/control-plane-execution-plane.md)
- [`/Users/jahanzebhussain/Synapse/docs/migration/phase-24-3-control-plane-split.md`](/Users/jahanzebhussain/Synapse/docs/migration/phase-24-3-control-plane-split.md)

## Browser Hardening

Synapse browser runtime now includes:
- session auth resilience with cookie/storage persistence and restore
- popup/modal dismissal helpers
- retryable click/type for stale element recovery
- download and upload flows with runtime events
- SPA route-change metadata and bounded scroll extraction helpers

New browser endpoints:
- `POST /api/browser/dismiss`
- `POST /api/browser/upload`
- `POST /api/browser/download`
- `POST /api/browser/scroll_extract`

New browser observability events:
- `popup.dismissed`
- `browser.popup.opened`
- `download.completed`
- `upload.completed`
- `navigation.route_changed`
- `browser.error`
- `session.expired`
- `browser.challenge.detected`
- `browser.captcha.detected`
- `browser.human_intervention.required`
- `browser.console.logged`
- `browser.network.failed`
- `browser.navigation.traced`

Run-scoped browser diagnostics APIs:

- `GET /api/runs/{run_id}/trace`
- `GET /api/runs/{run_id}/network`

CAPTCHA and anti-bot policy behavior is controlled by `challenge_policy` on the
agent or run security policy. Supported values are:

- `fail`
- `pause`
- `escalate_to_operator`
- `retry_with_profile`
