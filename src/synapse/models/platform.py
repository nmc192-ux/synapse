from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Organization(BaseModel):
    organization_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    slug: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, object] = Field(default_factory=dict)


class OrganizationCreateRequest(BaseModel):
    name: str
    slug: str
    metadata: dict[str, object] = Field(default_factory=dict)


class Project(BaseModel):
    project_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organization_id: str
    name: str
    slug: str
    description: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, object] = Field(default_factory=dict)


class ProjectCreateRequest(BaseModel):
    organization_id: str
    name: str
    slug: str
    description: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class PlatformUser(BaseModel):
    user_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organization_id: str
    project_ids: list[str] = Field(default_factory=list)
    email: str
    display_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, object] = Field(default_factory=dict)


class UserCreateRequest(BaseModel):
    organization_id: str
    project_ids: list[str] = Field(default_factory=list)
    email: str
    display_name: str
    metadata: dict[str, object] = Field(default_factory=dict)


class APIKeyRecord(BaseModel):
    api_key_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organization_id: str
    project_id: str
    user_id: str | None = None
    name: str
    scopes: list[str] = Field(default_factory=list)
    prefix: str
    secret_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    revoked: bool = False
    metadata: dict[str, object] = Field(default_factory=dict)


class APIKeyCreateRequest(BaseModel):
    organization_id: str
    project_id: str
    user_id: str | None = None
    name: str
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class APIKeyIssueResponse(BaseModel):
    record: APIKeyRecord
    api_key: str
    access_token: str


class AuditLogRecord(BaseModel):
    audit_log_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organization_id: str | None = None
    project_id: str | None = None
    actor_id: str
    actor_type: str
    action: str
    resource_type: str
    resource_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentOwnership(BaseModel):
    agent_id: str
    organization_id: str
    project_id: str
    owner_user_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentOwnershipRequest(BaseModel):
    organization_id: str
    project_id: str
    owner_user_id: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
