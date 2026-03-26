from __future__ import annotations

from synapse.models.a2a import AgentIdentityRecord
from synapse.security.signing import MessageSigner


class AgentIdentityManager:
    def __init__(self, platform_signing_key: str) -> None:
        self.platform_signing_key = platform_signing_key
        self.signer = MessageSigner()

    def issue_identity(
        self,
        *,
        agent_id: str,
        verification_key: str,
        key_id: str,
        reputation: float,
        capabilities: list[str],
        issued_at,
    ) -> AgentIdentityRecord:
        record = AgentIdentityRecord(
            agent_id=agent_id,
            verification_key=verification_key,
            key_id=key_id,
            reputation=reputation,
            capabilities=capabilities,
            issued_at=issued_at,
        )
        signature = self.signer._sign_dict(
            {
                "agent_id": record.agent_id,
                "verification_key": record.verification_key,
                "key_id": record.key_id,
                "reputation": record.reputation,
                "capabilities": record.capabilities,
                "issued_at": record.issued_at.isoformat(),
            },
            self.platform_signing_key,
        )
        return record.model_copy(update={"signature": signature})

    def verify_identity(self, record: AgentIdentityRecord) -> None:
        expected = self.signer._sign_dict(
            {
                "agent_id": record.agent_id,
                "verification_key": record.verification_key,
                "key_id": record.key_id,
                "reputation": record.reputation,
                "capabilities": record.capabilities,
                "issued_at": record.issued_at.isoformat(),
            },
            self.platform_signing_key,
        )
        if record.signature != expected:
            raise ValueError("Invalid agent identity signature.")
