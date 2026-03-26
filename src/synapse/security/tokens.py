from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone


class TokenValidationError(ValueError):
    pass


class JWTCodec:
    def __init__(self, secret: str, issuer: str, audience: str) -> None:
        self.secret = secret.encode("utf-8")
        self.issuer = issuer
        self.audience = audience

    def encode(self, claims: dict[str, object], *, expires_in_seconds: int) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            **claims,
            "iss": self.issuer,
            "aud": self.audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
        }
        header = {"alg": "HS256", "typ": "JWT"}
        encoded_header = self._b64encode(header)
        encoded_payload = self._b64encode(payload)
        signature = self._sign(f"{encoded_header}.{encoded_payload}")
        return f"{encoded_header}.{encoded_payload}.{signature}"

    def decode(self, token: str) -> dict[str, object]:
        try:
            encoded_header, encoded_payload, encoded_signature = token.split(".")
        except ValueError as exc:
            raise TokenValidationError("Malformed token.") from exc

        signed = f"{encoded_header}.{encoded_payload}"
        expected_signature = self._sign(signed)
        if not hmac.compare_digest(encoded_signature, expected_signature):
            raise TokenValidationError("Invalid token signature.")

        header = self._b64decode(encoded_header)
        if header.get("alg") != "HS256":
            raise TokenValidationError("Unsupported token algorithm.")
        payload = self._b64decode(encoded_payload)

        if payload.get("iss") != self.issuer:
            raise TokenValidationError("Invalid token issuer.")
        if payload.get("aud") != self.audience:
            raise TokenValidationError("Invalid token audience.")

        expires_at = payload.get("exp")
        if not isinstance(expires_at, int):
            raise TokenValidationError("Token missing expiration.")
        if datetime.now(timezone.utc).timestamp() >= expires_at:
            raise TokenValidationError("Token expired.")

        return payload

    def _sign(self, value: str) -> str:
        digest = hmac.new(self.secret, value.encode("utf-8"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")

    @staticmethod
    def _b64encode(value: dict[str, object]) -> str:
        encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("utf-8")

    @staticmethod
    def _b64decode(value: str) -> dict[str, object]:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(f"{value}{padding}".encode("utf-8"))
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TokenValidationError("Invalid token payload.")
        return payload
