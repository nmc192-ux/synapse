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
uvicorn synapse.main:app --reload
```

## Project layout

```text
src/synapse/
  adapters/      Agent adapter interfaces and built-in implementations
  api/           FastAPI routes
  models/        Pydantic models
  runtime/       Browser runtime, orchestration, tools, registry
  transports/    WebSocket connection management
```
