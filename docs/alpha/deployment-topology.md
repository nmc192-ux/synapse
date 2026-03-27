# Restricted Alpha Deployment Topology

Synapse restricted alpha is designed for controlled deployments, not open multi-tenant internet hosting.

## Supported Topology

Recommended minimum:

- 1 Synapse API/control-plane deployment
- 1 Redis deployment for runtime state and queue durability
- 1 controlled worker pool deployment owned by the same controller topology
- 1 project-scoped operator dashboard
- 1 isolated environment per design partner project

Recommended rollout order:

1. partner-specific staging environment
2. operator rehearsal with the fixture web and alpha examples
3. first supervised partner run on a narrow allowlist
4. gradual expansion of approved workflows only after successful review

## Why This Topology

Current Synapse supports:

- atomic lease ownership
- durable request/result tracking
- durable session ownership
- project-scoped live event delivery

But it still assumes a controlled worker/controller relationship. Restricted alpha should not be deployed as an open elastic worker fleet across loosely coordinated control planes.

## Strong Recommendations

- keep Redis required in alpha environments
- avoid memory fallback for partner-facing environments
- do not share project-scoped auth across partners
- keep controller and worker ownership aligned
- use a single environment per partner project when possible
- prefer one controller domain and one operator dashboard origin per partner environment
- keep worker count small until intervention and recovery behavior has been rehearsed
- pin domain allowlists and plugin allowlists in configuration, not ad hoc operator notes

## Not Supported Yet

- public hosted multi-tenant deployment
- untrusted user self-serve onboarding
- broad plugin marketplace execution
- unbounded cross-region worker fleets
- SLA-backed unattended operation

## Reference Safe Defaults

Use:

- [`/Users/jahanzebhussain/Synapse/config/examples/alpha.env.example`](/Users/jahanzebhussain/Synapse/config/examples/alpha.env.example)
- [`/Users/jahanzebhussain/Synapse/config/examples/local-safe-defaults.env.example`](/Users/jahanzebhussain/Synapse/config/examples/local-safe-defaults.env.example)

## Deployment Checklist

- Redis configured and reachable
- `SYNAPSE_AUTH_REQUIRED=true`
- project-scoped tokens or API keys issued
- operator dashboard configured with project context
- plugin trust policy reviewed
- domain allowlists defined
- failure taxonomy shared with operators
- support templates ready before partner onboarding
- staging drill completed for controller restart, worker failure, and intervention queue recovery
