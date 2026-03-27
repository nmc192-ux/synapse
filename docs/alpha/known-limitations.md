# Known Limitations

Restricted alpha is intentionally constrained.

## Product Limits

- supervised runs are required for higher-risk browsing flows
- no SLA commitments
- no support for sensitive credentials
- no public hosted deployment support

## Technical Limits

- browser recovery is still best-effort in some crash paths
- deterministic replay/resume is limited
- distributed worker execution is still optimized for controlled topology
- plugin isolation is improved, but not yet a full dedicated isolation fleet
- operator UI auth is functional but still lightweight

## Operational Limits

- Redis and worker failures are handled more safely now, but partner environments still need operator review and runbooks
- some failure drills remain partially simulated and should be exercised in staging before new partner onboarding
- operator dashboard auth is adequate for a restricted operator group, not a polished end-user IAM experience
- controller/worker topology should remain controlled; broader elastic worker fabrics still need more hardening

## What Partners Should Expect

- occasional operator pauses
- stricter domain and plugin policies than local development
- requests for traces, checkpoints, and reproduction details when failures occur
