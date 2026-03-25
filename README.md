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
uvicorn synapse.main:app --reload
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

## Task Execution API

Synapse now includes a PostgreSQL-backed task manager for task creation, claiming,
progress updates, and result submission.

- `POST /api/tasks/create`
- `POST /api/tasks/{task_id}/claim`
- `POST /api/tasks/{task_id}/update`
- `GET /api/tasks/active`

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
