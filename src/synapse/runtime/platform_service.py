from __future__ import annotations

import hashlib
import secrets

from synapse.models.agent import AgentDefinition
from synapse.models.platform import (
    APIKeyCreateRequest,
    APIKeyIssueResponse,
    APIKeyRecord,
    AuditLogRecord,
    AgentOwnership,
    AgentOwnershipRequest,
    Organization,
    OrganizationCreateRequest,
    PlatformUser,
    Project,
    ProjectCreateRequest,
    UserCreateRequest,
)
from synapse.runtime.registry import AgentRegistry
from synapse.runtime.state_store import RuntimeStateStore
from synapse.security.auth import Authenticator
from synapse.security.policies import PrincipalType


class PlatformService:
    def __init__(
        self,
        state_store: RuntimeStateStore | None,
        authenticator: Authenticator | None,
        agents: AgentRegistry,
    ) -> None:
        self.state_store = state_store
        self.authenticator = authenticator
        self.agents = agents

    def set_state_store(self, state_store: RuntimeStateStore | None) -> None:
        self.state_store = state_store

    async def create_organization(self, request: OrganizationCreateRequest) -> Organization:
        self._require_store()
        organization = Organization(name=request.name, slug=request.slug, metadata=request.metadata)
        await self.state_store.store_organization(organization.organization_id, organization.model_dump(mode="json"))
        return organization

    async def list_organizations(self) -> list[Organization]:
        self._require_store()
        rows = await self.state_store.list_organizations()
        return [Organization.model_validate(row) for row in rows]

    async def create_project(self, request: ProjectCreateRequest) -> Project:
        self._require_store()
        project = Project(
            organization_id=request.organization_id,
            name=request.name,
            slug=request.slug,
            description=request.description,
            metadata=request.metadata,
        )
        await self.state_store.store_project(project.project_id, project.model_dump(mode="json"))
        return project

    async def list_projects(self, organization_id: str | None = None) -> list[Project]:
        self._require_store()
        rows = await self.state_store.list_projects()
        projects = [Project.model_validate(row) for row in rows]
        if organization_id is not None:
            projects = [project for project in projects if project.organization_id == organization_id]
        return projects

    async def create_user(self, request: UserCreateRequest) -> PlatformUser:
        self._require_store()
        user = PlatformUser(
            organization_id=request.organization_id,
            project_ids=request.project_ids,
            email=request.email,
            display_name=request.display_name,
            metadata=request.metadata,
        )
        await self.state_store.store_user(user.user_id, user.model_dump(mode="json"))
        return user

    async def list_users(self, organization_id: str | None = None, project_id: str | None = None) -> list[PlatformUser]:
        self._require_store()
        rows = await self.state_store.list_users()
        users = [PlatformUser.model_validate(row) for row in rows]
        if organization_id is not None:
            users = [user for user in users if user.organization_id == organization_id]
        if project_id is not None:
            users = [user for user in users if project_id in user.project_ids]
        return users

    async def create_api_key(self, request: APIKeyCreateRequest) -> APIKeyIssueResponse:
        self._require_store()
        if self.authenticator is None:
            raise RuntimeError("Authenticator is required for issuing API keys.")
        raw_secret = f"synp_{secrets.token_urlsafe(24)}"
        prefix = raw_secret[:12]
        record = APIKeyRecord(
            organization_id=request.organization_id,
            project_id=request.project_id,
            user_id=request.user_id,
            name=request.name,
            scopes=request.scopes,
            prefix=prefix,
            secret_hash=self._hash_secret(raw_secret),
            expires_at=request.expires_at,
            metadata=request.metadata,
        )
        await self.state_store.store_api_key(record.api_key_id, record.model_dump(mode="json"))
        access_token = self.authenticator.issue_token(
            subject=record.api_key_id,
            principal_type=PrincipalType.SERVICE,
            scopes=request.scopes,
            project_id=request.project_id,
            organization_id=request.organization_id,
            api_key_id=record.api_key_id,
        )
        return APIKeyIssueResponse(record=record, api_key=raw_secret, access_token=access_token)

    async def list_api_keys(self, project_id: str | None = None) -> list[APIKeyRecord]:
        self._require_store()
        rows = await self.state_store.list_api_keys()
        records = [APIKeyRecord.model_validate(row) for row in rows]
        if project_id is not None:
            records = [record for record in records if record.project_id == project_id]
        return records

    async def assign_agent_ownership(self, agent_id: str, request: AgentOwnershipRequest) -> AgentOwnership:
        self._require_store()
        definition = self.agents.get(agent_id).model_copy(deep=True)
        definition.organization_id = request.organization_id
        definition.project_id = request.project_id
        definition.owner_user_id = request.owner_user_id
        definition.metadata = {
            **definition.metadata,
            **{str(key): str(value) for key, value in request.metadata.items()},
        }
        agent = self.agents.register(definition)
        await self.agents.save_to_store(agent)
        ownership = AgentOwnership(
            agent_id=agent_id,
            organization_id=request.organization_id,
            project_id=request.project_id,
            owner_user_id=request.owner_user_id,
            metadata=request.metadata,
        )
        await self.state_store.store_agent_ownership(agent_id, ownership.model_dump(mode="json"))
        return ownership

    async def get_agent_ownership(self, agent_id: str) -> AgentOwnership | None:
        self._require_store()
        payload = await self.state_store.get_agent_ownership(agent_id)
        if payload is None:
            agent = self.agents.get(agent_id)
            if agent.project_id is None or agent.organization_id is None:
                return None
            payload = AgentOwnership(
                agent_id=agent.agent_id,
                organization_id=agent.organization_id,
                project_id=agent.project_id,
                owner_user_id=agent.owner_user_id,
            ).model_dump(mode="json")
        return AgentOwnership.model_validate(payload)

    async def log_audit_action(
        self,
        *,
        actor_id: str,
        actor_type: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        project_id: str | None = None,
        organization_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AuditLogRecord:
        self._require_store()
        record = AuditLogRecord(
            organization_id=organization_id,
            project_id=project_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata or {},
        )
        await self.state_store.store_audit_log(record.audit_log_id, record.model_dump(mode="json"))
        return record

    async def list_audit_logs(self, project_id: str | None = None, limit: int = 100) -> list[AuditLogRecord]:
        self._require_store()
        rows = await self.state_store.list_audit_logs(project_id=project_id, limit=limit)
        return [AuditLogRecord.model_validate(row) for row in rows]

    @staticmethod
    def _hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    def _require_store(self) -> None:
        if self.state_store is None:
            raise RuntimeError("Platform service requires a runtime state store.")
