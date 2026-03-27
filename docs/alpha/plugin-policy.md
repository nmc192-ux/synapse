# Plugin Policy For Restricted Alpha

Restricted alpha plugin policy is intentionally narrow.

## Trust Classes

- `trusted_internal`
- `trusted_partner`
- `untrusted_external`

## Allowed In Restricted Alpha

- `trusted_internal`: allowed
- `trusted_partner`: allowed only when explicitly allowlisted
- `untrusted_external`: denied by default

## Hosted Execution Requirements

Hosted plugin execution must use:

- `SYNAPSE_PLUGIN_EXECUTION_MODE=isolated_hosted`
- a real hosted isolation backend
- durable audit logging

If the hosted isolation backend is unavailable, Synapse should fail closed.

Restricted alpha guidance:

- start with zero partner plugins if possible
- require a named reviewer for each allowlisted `trusted_partner` plugin
- review plugin stdout/stderr retention expectations before enabling it

## Partner Guidance

For design partners:

- start with `trusted_internal` plugins only
- add `trusted_partner` plugins one at a time
- keep plugin capabilities narrow
- prefer stateless plugins
- do not onboard external partner-written plugins without a review gate

## What To Review Before Allowlisting

- plugin purpose
- domains and external services touched
- filesystem expectations
- failure handling
- audit usefulness
- whether the workflow can be done without the plugin

## Not Supported

- arbitrary plugin uploads
- public plugin marketplace behavior
- broad self-serve partner plugin enablement
- treating the subprocess guard path as equivalent to hosted isolation
