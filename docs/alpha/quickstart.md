# Restricted Alpha Quickstart

This quickstart is for trusted Synapse design partners running supervised agent workflows.

## Before You Start

Synapse restricted alpha assumes:

- trusted users only
- supervised runs only
- project-scoped operator access
- `trusted_internal` plugins only, or tightly allowlisted `trusted_partner` plugins
- restricted domain allowlists
- no sensitive credentials
- no SLA guarantees

Read these before onboarding a partner:

- [`/Users/jahanzebhussain/Synapse/docs/alpha/deployment-topology.md`](/Users/jahanzebhussain/Synapse/docs/alpha/deployment-topology.md)
- [`/Users/jahanzebhussain/Synapse/docs/alpha/security-boundaries.md`](/Users/jahanzebhussain/Synapse/docs/alpha/security-boundaries.md)
- [`/Users/jahanzebhussain/Synapse/docs/alpha/plugin-policy.md`](/Users/jahanzebhussain/Synapse/docs/alpha/plugin-policy.md)
- [`/Users/jahanzebhussain/Synapse/docs/alpha/operator-runbook.md`](/Users/jahanzebhussain/Synapse/docs/alpha/operator-runbook.md)

## Recommended Deployment Shape

Use one restricted project per design partner.

- one organization
- one project per partner environment
- one or more trusted operator tokens or project API keys
- Redis enabled
- worker/controller deployment kept tightly controlled
- operator dashboard enabled with project-scoped auth
- `trusted_internal` plugins only unless a reviewed `trusted_partner` plugin has been allowlisted
- no partner workflow should require pasting sensitive credentials into prompts, tasks, or operator notes

## 1. Start Synapse Safely

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
```

Start with the alpha-safe environment template:

```bash
cp config/examples/alpha.env.example .env.alpha
```

Review and set at minimum:

- `SYNAPSE_PLUGIN_EXECUTION_MODE=isolated_hosted`
- `SYNAPSE_REDIS_URL`
- `SYNAPSE_AUTH_REQUIRED=true`
- `SYNAPSE_BROWSER_WORKER_COUNT`
- `SYNAPSE_SCHEDULER_LEASE_TIMEOUT_SECONDS`
- `SYNAPSE_A2A_SERVICE_AGENT_ALLOWLIST`
- `SYNAPSE_HOSTED_PLUGIN_PARTNER_ALLOWLIST`

Then run:

```bash
uvicorn synapse.main:app --host 0.0.0.0 --port 8000
```

## 2. Create Project-Scoped Access

Use project-scoped operator tokens or API keys only.

Recommended:

1. create organization
2. create project
3. create operator user
4. issue project-scoped API key or bearer token
5. configure the dashboard with that project context
6. verify the operator token has only the scopes required for run inspection and intervention
7. verify the dashboard cannot query another partner project before onboarding begins

Example config material:

- [`/Users/jahanzebhussain/Synapse/config/examples/operator-project-setup.env.example`](/Users/jahanzebhussain/Synapse/config/examples/operator-project-setup.env.example)

## 3. Apply Alpha-Safe Policy

Before running any partner workflow:

- restrict agent `allowed_domains`
- restrict `allowed_tools`
- keep plugin set to trusted-only
- keep no sensitive secrets in task payloads
- require operator supervision for challenge-heavy runs
- document who is on-call for interventions before the first partner run

Example files:

- [`/Users/jahanzebhussain/Synapse/config/examples/alpha.allowed_domains.yaml`](/Users/jahanzebhussain/Synapse/config/examples/alpha.allowed_domains.yaml)
- [`/Users/jahanzebhussain/Synapse/config/examples/alpha.trusted_plugins.yaml`](/Users/jahanzebhussain/Synapse/config/examples/alpha.trusted_plugins.yaml)

## 4. Launch the Operator Dashboard

```bash
cd ui
npm install
npm run dev
```

Provide:

- `NEXT_PUBLIC_SYNAPSE_BEARER_TOKEN` or `NEXT_PUBLIC_SYNAPSE_API_KEY`
- `NEXT_PUBLIC_SYNAPSE_PROJECT_ID`
- `NEXT_PUBLIC_SYNAPSE_API_BASE_URL`
- `NEXT_PUBLIC_SYNAPSE_WS_URL`

The dashboard is intended for supervised runs and operator intervention handling, not unattended fleet management.

## 5. Run Alpha Example Agents

Alpha-constrained examples live in:

- [`/Users/jahanzebhussain/Synapse/examples/alpha/openclaw_research_agent.py`](/Users/jahanzebhussain/Synapse/examples/alpha/openclaw_research_agent.py)
- [`/Users/jahanzebhussain/Synapse/examples/alpha/codex_browser_agent.py`](/Users/jahanzebhussain/Synapse/examples/alpha/codex_browser_agent.py)
- [`/Users/jahanzebhussain/Synapse/examples/alpha/claude_code_agent.py`](/Users/jahanzebhussain/Synapse/examples/alpha/claude_code_agent.py)
- [`/Users/jahanzebhussain/Synapse/examples/alpha/multi_agent_delegation_demo.py`](/Users/jahanzebhussain/Synapse/examples/alpha/multi_agent_delegation_demo.py)

These examples are intentionally constrained:

- they expect project-scoped auth from environment variables
- they use explicit domain/tool limits
- they default challenge handling to operator escalation
- they are examples for supervised workflows, not unattended production agents

## 6. If Something Goes Wrong

Use the diagnostics workflow in:

- [`/Users/jahanzebhussain/Synapse/docs/alpha/support-and-diagnostics.md`](/Users/jahanzebhussain/Synapse/docs/alpha/support-and-diagnostics.md)
- [`/Users/jahanzebhussain/Synapse/docs/alpha/failure-taxonomy.md`](/Users/jahanzebhussain/Synapse/docs/alpha/failure-taxonomy.md)

If the run touches CAPTCHA, repeated challenge loops, stale worker ownership, or unexpected plugin policy denials, stop and follow the operator runbook instead of forcing continuation.

## 7. Do Not Expand Scope During Alpha

Do not change these assumptions casually during partner onboarding:

- no wildcard domain access
- no public internet plugin uploads
- no sensitive credential workflows
- no unattended long-running browser loops
- no public hosted exposure
