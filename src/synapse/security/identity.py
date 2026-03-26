from __future__ import annotations

from collections.abc import Mapping

from synapse.models.a2a import AgentIdentityRecord
from synapse.security.signing import MessageSigner


class AgentIdentityManager:
    def __init__(
        self,
        platform_signing_key: str,
        *,
        platform_key_id: str = "default",
        trusted_signing_keys: Mapping[str, str] | None = None,
    ) -> None:
        self.platform_signing_key = platform_signing_key
        self.platform_key_id = platform_key_id
        self.trusted_signing_keys = {
            platform_key_id: platform_signing_key,
            **dict(trusted_signing_keys or {}),
        }
        self.signer = MessageSigner()

    def issue_identity(
        self,
        *,
        agent_id: str,
        organization_id: str | None,
        project_id: str | None,
        verification_key: str,
        key_id: str,
        reputation: float,
        capabilities: list[str],
        issued_at,
    ) -> AgentIdentityRecord:
        record = AgentIdentityRecord(
            agent_id=agent_id,
            organization_id=organization_id,
            project_id=project_id,
            verification_key=verification_key,
            key_id=key_id,
            issuer_key_id=self.platform_key_id,
            reputation=reputation,
            capabilities=capabilities,
            issued_at=issued_at,
        )
        signature = self.signer._sign_dict(
            {
                "agent_id": record.agent_id,
                "organization_id": record.organization_id,
                "project_id": record.project_id,
                "verification_key": record.verification_key,
                "key_id": record.key_id,
                "issuer_key_id": record.issuer_key_id,
                "reputation": record.reputation,
                "capabilities": record.capabilities,
                "issued_at": record.issued_at.isoformat(),
            },
            self.platform_signing_key,
        )
        return record.model_copy(update={"signature": signature})

    def verify_identity(self, record: AgentIdentityRecord) -> None:
        signing_key = self.trusted_signing_keys.get(record.issuer_key_id or self.platform_key_id)
        if signing_key is None:
            raise ValueError("Unknown agent identity issuer key.")
        expected = self.signer._sign_dict(
            {
                "agent_id": record.agent_id,
                "organization_id": record.organization_id,
                "project_id": record.project_id,
                "verification_key": record.verification_key,
                "key_id": record.key_id,
                "issuer_key_id": record.issuer_key_id,
                "reputation": record.reputation,
                "capabilities": record.capabilities,
                "issued_at": record.issued_at.isoformat(),
            },
            signing_key,
        )
        if record.signature != expected:
            raise ValueError("Invalid agent identity signature.")
