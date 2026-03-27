# Synthetic Alpha Swarm

This scaffold creates a 24/7 internal alpha harness that continuously exercises Synapse with safe public-domain workloads and isolated synthetic projects.

## Roles

- `director.py`
  Schedules recurring quarter-hourly, hourly, and daily synthetic workloads across the steady and chaos projects.
- `browser_runner_1.py`
  Executes public docs, reference, and browser self-test work in the steady tenant.
- `browser_runner_2.py`
  Executes public docs, research, delegation, and intervention-oriented work in the chaos tenant.
- `auditor.py`
  Classifies failures into browser, scheduler, policy, challenge/captcha, auth/session, and plugin buckets.
- `reporter.py`
  Produces daily reports, weekly alpha review drafts, and mirrored metrics snapshots.
- `chaos_monkey.py`
  Injects safe failures and daily chaos validations without using sensitive credentials.
- `bootstrap.py`
  Optional helper that creates the synthetic organization, steady/chaos projects, role users, and scoped API keys.

## Scheduling Setup

The director now operates in recurring tiers:

- Every 15 minutes:
  - docs checks
  - public developer news checks
  - public research checks
- Hourly:
  - session restore checks
  - delegated runs
  - intervention-triggering runs
- Daily:
  - chaos runs
  - health checks
  - summary-trigger runs

Schedule state is persisted in:

- [`/Users/drj/Documents/Synapse/examples/synthetic_alpha_swarm/runtime/director_schedule_state.json`](/Users/drj/Documents/Synapse/examples/synthetic_alpha_swarm/runtime/director_schedule_state.json)

## Safe Domain Defaults

The harness stays on public, low-risk domains only:

- `docs.python.org`
- `fastapi.tiangolo.com`
- `developer.mozilla.org`
- `en.wikipedia.org`
- `arxiv.org`
- `github.com`
- `raw.githubusercontent.com`

## Metrics Tracked

At minimum the harness tracks:

- runs started
- runs completed
- runs failed
- intervention count
- browser crash count
- captcha/challenge count
- session restore failures
- duplicate-result recoveries
- stale ownership incidents
- average run latency
- per-project failure rate

## Report Formats

Reporter outputs include:

- daily report markdown
- weekly alpha review draft markdown
- JSON metrics snapshot

Daily report sections:

- fleet summary
- per-project failure rate
- failure classification
- per-project operational details

Weekly review sections:

- executive summary
- key risks
- recommendations
- project notes

## Output Locations

Artifacts are mirrored to both:

- repo-local runtime folder:
  - [`/Users/drj/Documents/Synapse/examples/synthetic_alpha_swarm/runtime`](/Users/drj/Documents/Synapse/examples/synthetic_alpha_swarm/runtime)
- machine-local log folder:
  - [`/Users/drj/synapse-logs/synthetic_alpha_swarm`](/Users/drj/synapse-logs/synthetic_alpha_swarm)

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
- `SYNTHETIC_ALPHA_SWARM_DIRECTOR_ENABLE_SCHEDULE=true`
- `SYNTHETIC_ALPHA_SWARM_DIRECTOR_SUBMIT_RUNS=true`
- `SYNTHETIC_ALPHA_SWARM_ROLE_MAX_PAGES=20000`
- `SYNTHETIC_ALPHA_SWARM_ROLE_MAX_RUNTIME_SECONDS=2592000`
- `SYNTHETIC_ALPHA_SWARM_ROLE_BROWSER_ACTIONS_PER_MINUTE=30`

The browser runners now rotate a single smoke URL per interval, apply jitter between browser actions, and back off automatically on `429`/transient upstream errors so the supervisor does not amplify temporary throttling into a restart storm.

## Startup Instructions

Run from the repo root:

```bash
.venv/bin/python examples/synthetic_alpha_swarm/bootstrap.py
.venv/bin/python examples/synthetic_alpha_swarm/director.py --interval-seconds 300
.venv/bin/python examples/synthetic_alpha_swarm/browser_runner_1.py --interval-seconds 180
.venv/bin/python examples/synthetic_alpha_swarm/browser_runner_2.py --interval-seconds 180
.venv/bin/python examples/synthetic_alpha_swarm/auditor.py --interval-seconds 900
.venv/bin/python examples/synthetic_alpha_swarm/reporter.py --interval-seconds 3600
.venv/bin/python examples/synthetic_alpha_swarm/chaos_monkey.py --interval-seconds 86400
```

For one-shot checks:

```bash
.venv/bin/python examples/synthetic_alpha_swarm/director.py --once
.venv/bin/python examples/synthetic_alpha_swarm/auditor.py --once
.venv/bin/python examples/synthetic_alpha_swarm/reporter.py --once
.venv/bin/python examples/synthetic_alpha_swarm/chaos_monkey.py --once
```

## Example Generated Reports

The reporter can emit example fixture-based artifacts for format review. Example filenames:

- `example_daily_report.md`
- `example_weekly_review.md`
- `example_metrics_snapshot.json`
