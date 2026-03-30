# Local Supervision

This repo uses a hybrid local supervision model on macOS:

- `launchd` for Synapse backend, Synapse UI, OpenClaw gateway, and synthetic-alpha swarm roles.
- `brew services` for PostgreSQL, Redis, and Ollama.

This approach is preferred on macOS because `launchd` is native, reliable for user-scoped long-running processes, and works cleanly with Homebrew-managed dependencies.

## Managed Processes

Launchd labels:

- `ai.synapse.backend`
- `ai.synapse.ui`
- `ai.openclaw.default-local`
- `ai.openclaw.discord-hourly-report`
- `ai.synapse.swarm.director`
- `ai.synapse.swarm.browser-runner-1`
- `ai.synapse.swarm.browser-runner-2`
- `ai.synapse.swarm.auditor`
- `ai.synapse.swarm.reporter`
- `ai.synapse.swarm.chaos-monkey`

Homebrew services:

- `postgresql@17`
- `redis`
- `ollama`

## Config

Copy:

- [`/Users/drj/Documents/Synapse/config/examples/synthetic-alpha-supervision.env.example`](/Users/drj/Documents/Synapse/config/examples/synthetic-alpha-supervision.env.example)

To:

- `/Users/drj/Documents/Synapse/.env.synthetic-alpha-supervision`

The supervision env references:

- backend env: `.env.local.dev`
- ui env: `ui/.env.local`
- swarm env: `examples/synthetic_alpha_swarm/.env.synthetic-alpha`

Optional OpenClaw Discord report settings:

- `OPENCLAW_DISCORD_REPORT_CHANNEL=discord`
- `OPENCLAW_DISCORD_REPORT_TARGET=<discord-channel-or-user-id>`
- `OPENCLAW_DISCORD_REPORT_INTERVAL_SECONDS=3600`

## Stack Commands

```bash
./start_synthetic_alpha_stack.sh
./stop_synthetic_alpha_stack.sh
./status_synthetic_alpha_stack.sh
```

## Logging

Launchd service logs are written to:

- `~/synapse-logs/services/*.log`
- `~/synapse-logs/services/*.err.log`

The hourly OpenClaw Discord reporter writes to:

- `~/synapse-logs/services/ai.openclaw.discord-hourly-report.log`
- `~/synapse-logs/services/ai.openclaw.discord-hourly-report.err.log`

Synthetic swarm report artifacts are mirrored to:

- `~/synapse-logs/synthetic_alpha_swarm`
- `examples/synthetic_alpha_swarm/runtime`

## Restart Policy

- `launchd` services use `RunAtLoad = true`
- `launchd` services use `KeepAlive = { SuccessfulExit = false }`
- `launchd` services use `ThrottleInterval = 10`
- Homebrew services use their standard launchd restart behavior
- UI runs in `next start` production mode by default, with a build performed in the stack start script before kickstart
