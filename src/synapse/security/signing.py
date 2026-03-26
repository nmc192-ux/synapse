from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from synapse.models.a2a import A2AEnvelope, AgentWireMessage


class SignatureValidationError(ValueError):
    pass


class MessageReplayError(SignatureValidationError):
    pass


class MessageExpiredError(SignatureValidationError):
    pass


class MessageSigner:
    def sign_wire_message(
        self,
        message: AgentWireMessage,
        *,
        signing_key: str,
        key_id: str | None = None,
        nonce: str | None = None,
        timestamp: datetime | None = None,
    ) -> AgentWireMessage:
        signed = message.model_copy(
            update={
                "key_id": key_id or message.key_id or "default",
                "nonce": nonce or message.nonce,
                "timestamp": timestamp or message.timestamp,
            }
        )
        signature = self._sign_dict(self._wire_payload(signed), signing_key)
        return signed.model_copy(update={"signature": signature})

    def verify_wire_message(
        self,
        message: AgentWireMessage,
        *,
        verification_key: str,
        max_age_seconds: int = 300,
        seen_nonces: set[str] | None = None,
    ) -> None:
        self._verify_signature(
            message.signature,
            self._wire_payload(message),
            verification_key,
            timestamp=message.timestamp,
            nonce=message.nonce,
            max_age_seconds=max_age_seconds,
            seen_nonces=seen_nonces,
        )

    def sign_envelope(self, envelope: A2AEnvelope, *, signing_key: str) -> A2AEnvelope:
        signature = self._sign_dict(self._envelope_payload(envelope), signing_key)
        return envelope.model_copy(update={"signature": signature})

    @staticmethod
    def _verify_signature(
        signature: str | None,
        payload: dict[str, object],
        verification_key: str,
        *,
        timestamp: datetime,
        nonce: str | None,
        max_age_seconds: int,
        seen_nonces: set[str] | None,
    ) -> None:
        if not signature:
            raise SignatureValidationError("Missing A2A message signature.")
        now = datetime.now(timezone.utc)
        if now - timestamp > timedelta(seconds=max_age_seconds):
            raise MessageExpiredError("A2A message expired.")
        if nonce:
            if seen_nonces is not None and nonce in seen_nonces:
                raise MessageReplayError("A2A message nonce was already used.")
            if seen_nonces is not None:
                seen_nonces.add(nonce)
        expected = MessageSigner._sign_dict(payload, verification_key)
        if not hmac.compare_digest(signature, expected):
            raise SignatureValidationError("Invalid A2A message signature.")

    @staticmethod
    def _wire_payload(message: AgentWireMessage) -> dict[str, object]:
        return {
            "message_id": message.message_id,
            "type": message.type.value,
            "sender_id": message.sender_id or message.agent,
            "recipient_id": message.recipient_id or message.target_agent,
            "target_agent": message.target_agent,
            "organization_id": message.organization_id,
            "project_id": message.project_id,
            "key_id": message.key_id,
            "nonce": message.nonce,
            "timestamp": message.timestamp.isoformat(),
            "payload": message.payload,
        }

    @staticmethod
    def _envelope_payload(envelope: A2AEnvelope) -> dict[str, object]:
        return {
            "message_id": envelope.message_id,
            "type": envelope.type.value,
            "organization_id": envelope.organization_id,
            "project_id": envelope.project_id,
            "sender_agent_id": envelope.sender_agent_id,
            "recipient_agent_id": envelope.recipient_agent_id,
            "correlation_id": envelope.correlation_id,
            "key_id": envelope.key_id,
            "nonce": envelope.nonce,
            "timestamp": envelope.timestamp.isoformat(),
            "payload": envelope.payload,
        }

    @staticmethod
    def _sign_dict(payload: dict[str, object], signing_key: str) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hmac.new(signing_key.encode("utf-8"), encoded, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")
