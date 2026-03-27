# Synthetic Alpha Swarm

This scaffold creates a small internal swarm that continuously exercises Synapse with safe public-domain workloads and isolated synthetic projects.

## Roles

- `director.py`
  Schedules workloads and emits run plans for the steady and chaos projects.
- `browser_runner_1.py`
  Executes deterministic public-doc browser tasks in the steady tenant.
- `browser_runner_2.py`
  Executes deterministic public-doc browser tasks in the chaos tenant.
- `auditor.py`
  Classifies failures and operator intervention patterns across both projects.
- `reporter.py`
  Generates daily and weekly markdown summaries from run and intervention data.
- `chaos_monkey.py`
  Injects controlled safe failures such as blocked-domain and missing-selector probes.
- `bootstrap.py`
  Optional helper that creates the synthetic organization, steady/chaos projects, role users, and scoped API keys.

## Project Layout

- Separate synthetic projects are used by default:
  - `steady`
  - `chaos`
- Separate synthetic contexts are embedded in task constraints:
  - `tenant-steady-docs`
  - `tenant-steady-reference`
  - `tenant-chaos-research`
  - `tenant-chaos-docs`

## Safe Domain Defaults

The scaffold defaults to public, low-risk domains only:

- `docs.python.org`
- `fastapi.tiangolo.com`
- `developer.mozilla.org`
- `en.wikipedia.org`
- `arxiv.org`
- `github.com`
- `raw.githubusercontent.com`

## Config Requirements

Copy [`/Users/drj/Documents/Synapse/examples/synthetic_alpha_swarm/.env.example`](/Users/drj/Documents/Synapse/examples/synthetic_alpha_swarm/.env.example) into your shell environment before running the roles.

Required env vars for normal role startup:

- `SYNTHETIC_ALPHA_SWARM_BASE_URL`
- `SYNTHETIC_ALPHA_SWARM_STEADY_PROJECT_ID`
- `SYNTHETIC_ALPHA_SWARM_STEADY_API_KEY`
- `SYNTHETIC_ALPHA_SWARM_CHAOS_PROJECT_ID`
- `SYNTHETIC_ALPHA_SWARM_CHAOS_API_KEY`

Optional env vars for bootstrap:

- `SYNTHETIC_ALPHA_SWARM_ADMIN_API_KEY` or `SYNTHETIC_ALPHA_SWARM_ADMIN_BEARER_TOKEN`
- `SYNTHETIC_ALPHA_SWARM_ADMIN_PROJECT_ID` if your admin token needs a project context header

## Startup Commands

Run from the repo root:

```bash
.venv/bin/python examples/synthetic_alpha_swarm/bootstrap.py
.venv/bin/python examples/synthetic_alpha_swarm/director.py --once
.venv/bin/python examples/synthetic_alpha_swarm/browser_runner_1.py
.venv/bin/python examples/synthetic_alpha_swarm/browser_runner_2.py
.venv/bin/python examples/synthetic_alpha_swarm/auditor.py --once
.venv/bin/python examples/synthetic_alpha_swarm/reporter.py --once
.venv/bin/python examples/synthetic_alpha_swarm/chaos_monkey.py --once
```

Continuous mode examples:

```bash
.venv/bin/python examples/synthetic_alpha_swarm/director.py --interval-seconds 300
.venv/bin/python examples/synthetic_alpha_swarm/browser_runner.py --runner browser-runner-1 --interval-seconds 180
.venv/bin/python examples/synthetic_alpha_swarm/browser_runner.py --runner browser-runner-2 --interval-seconds 180
.venv/bin/python examples/synthetic_alpha_swarm/auditor.py --interval-seconds 900
.venv/bin/python examples/synthetic_alpha_swarm/reporter.py --interval-seconds 3600
.venv/bin/python examples/synthetic_alpha_swarm/chaos_monkey.py --interval-seconds 1800
```

## Output

Role runs write JSON or markdown artifacts to:

- [`/Users/drj/Documents/Synapse/examples/synthetic_alpha_swarm/runtime`](/Users/drj/Documents/Synapse/examples/synthetic_alpha_swarm/runtime)

These artifacts are intended to provide local run-plan, audit, chaos, and summary data without using sensitive credentials.
