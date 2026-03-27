# Security Boundaries

This document describes the actual security boundaries for restricted alpha use.

## Trust Assumptions

Restricted alpha assumes:

- trusted partner users
- trusted operator team
- supervised runs
- controlled deployment topology
- no sensitive credentials in tasks, prompts, or operator input

## Tenant Boundaries

Synapse enforces project-scoped access across:

- runs
- agents
- sessions
- session profiles
- checkpoints
- interventions
- runtime events
- capability registry
- A2A websocket binding

Runtime events include organization/project context and live delivery is filtered by org, project, and optional run.

## Browser Boundaries

Agents should always be configured with:

- explicit `allowed_domains`
- explicit `allowed_tools`
- conservative execution limits

Alpha guidance:

- keep domain lists short
- prefer deterministic targets
- avoid sensitive login flows
- route challenge-heavy tasks to supervised operators
- reject requests to broaden domain access without updating the reviewed allowlist config

## Plugin Boundaries

Hosted mode requires:

- `isolated_hosted`
- real hosted isolation backend
- trusted plugin policy

Do not allow arbitrary third-party plugins for restricted alpha.

See:

- [`/Users/jahanzebhussain/Synapse/docs/alpha/plugin-policy.md`](/Users/jahanzebhussain/Synapse/docs/alpha/plugin-policy.md)

## Operator Boundaries

Operators can:

- approve
- reject
- provide input
- inspect run context

Operators should not:

- inject secrets into runs
- override project boundaries
- resume repeated challenge loops without understanding the site behavior
- switch project context mid-investigation without reloading the dashboard with the correct scoped token

## Credential And Data Handling

Restricted alpha does not support sensitive credential handling.

That means:

- do not provide production passwords, MFA secrets, long-lived API tokens, or customer secrets to agents
- do not ask operators to paste secrets into intervention forms
- prefer test accounts and disposable partner fixtures where login coverage is necessary
- redact screenshots, traces, and exported logs before sharing outside the operating team

## Not a Public Hosted Security Posture

Restricted alpha is intentionally narrower than a public hosted platform:

- no open signup
- no general-purpose untrusted plugins
- no guarantee of deterministic replay after every failure
- no commitment to public-hosted abuse resistance
