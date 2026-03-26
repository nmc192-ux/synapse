from __future__ import annotations

from enum import Enum
import uuid
from datetime import datetime, timezone

from pydantic import AliasChoices, BaseModel, Field, model_validator


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


class APIKeyStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class APIKeyRecord(BaseModel):
    api_key_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organization_id: str
    project_id: str
    user_id: str | None = None
    name: str
    scopes: list[str] = Field(default_factory=list)
    prefix: str
    hashed_secret: str = Field(validation_alias=AliasChoices("hashed_secret", "secret_hash"))
    status: APIKeyStatus = APIKeyStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "hashed_secret" not in payload and "secret_hash" in payload:
            payload["hashed_secret"] = payload["secret_hash"]
        if "status" not in payload and payload.get("revoked") is True:
            payload["status"] = APIKeyStatus.REVOKED.value
        payload.pop("secret_hash", None)
        payload.pop("revoked", None)
        return payload


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
