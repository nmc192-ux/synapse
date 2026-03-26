# Phase 28.1 Multi-Tenant Migration

This phase introduces first-class organization, project, user, API key, and agent ownership records.

Runtime ownership changes:

- agents now support `organization_id`, `project_id`, and `owner_user_id`
- runs now support `project_id`
- session profiles now support `organization_id`, `project_id`, and `owner_user_id`
- checkpoints now support `project_id`
- JWT principals now carry optional `organization_id`, `project_id`, and `api_key_id`

Platform APIs:

- `POST /api/platform/organizations`
- `GET /api/platform/organizations`
- `POST /api/platform/projects`
- `GET /api/platform/projects`
- `POST /api/platform/users`
- `GET /api/platform/users`
- `POST /api/platform/api-keys`
- `GET /api/platform/api-keys`
- `POST /api/platform/agents/{agent_id}/ownership`
- `GET /api/platform/agents/{agent_id}/ownership`

Project-scoped tokens:

- API keys issue JWT access tokens bound to a single `project_id`
- downstream runtime services can use these claims for project isolation and audit
