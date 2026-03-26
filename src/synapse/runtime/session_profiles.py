from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from synapse.models.runtime_event import EventSeverity, EventType, RuntimeEvent
from synapse.runtime.state_store import RuntimeStateStore


EventPublisher = Callable[[RuntimeEvent], Awaitable[None]]


class StorageSnapshot(BaseModel):
    local_storage: dict[str, str] = Field(default_factory=dict)
    session_storage: dict[str, str] = Field(default_factory=dict)


class SessionProfile(BaseModel):
    profile_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    agent_id: str | None = None
    cookies: list[dict[str, object]] = Field(default_factory=list)
    storage_by_origin: dict[str, StorageSnapshot] = Field(default_factory=dict)
    auth_state_by_domain: dict[str, dict[str, object]] = Field(default_factory=dict)
    domain_expirations: dict[str, datetime | None] = Field(default_factory=dict)
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, object] = Field(default_factory=dict)


class SessionProfileCreateRequest(BaseModel):
    name: str
    agent_id: str | None = None
    source_session_id: str | None = None
    cookies: list[dict[str, object]] = Field(default_factory=list)
    storage_by_origin: dict[str, StorageSnapshot] = Field(default_factory=dict)
    auth_state_by_domain: dict[str, dict[str, object]] = Field(default_factory=dict)
    domain_expirations: dict[str, datetime | None] = Field(default_factory=dict)
    expires_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class SessionProfileLoadRequest(BaseModel):
    run_id: str | None = None


class SessionProfileManager:
    def __init__(
        self,
        state_store: RuntimeStateStore | None = None,
        event_publisher: EventPublisher | None = None,
    ) -> None:
        self.state_store = state_store
        self.event_publisher = event_publisher

    def set_state_store(self, state_store: RuntimeStateStore | None) -> None:
        self.state_store = state_store

    def set_event_publisher(self, event_publisher: EventPublisher | None) -> None:
        self.event_publisher = event_publisher

    async def create_profile(self, request: SessionProfileCreateRequest) -> SessionProfile:
        if self.state_store is None:
            raise RuntimeError("Session profiles require a runtime state store.")
        session_payload = None
        if request.source_session_id is not None:
            session_payload = await self.state_store.get_session(request.source_session_id)
            if session_payload is None:
                raise KeyError(f"Session not found: {request.source_session_id}")

        profile = SessionProfile(
            name=request.name,
            agent_id=request.agent_id or self._agent_id_from_session(session_payload),
            cookies=request.cookies or self._cookies_from_session(session_payload),
            storage_by_origin=request.storage_by_origin or self._storage_from_session(session_payload),
            auth_state_by_domain=request.auth_state_by_domain or self._auth_state_from_session(session_payload),
            domain_expirations=request.domain_expirations,
            expires_at=request.expires_at,
            metadata={
                **request.metadata,
                **({"source_session_id": request.source_session_id} if request.source_session_id else {}),
            },
        )
        await self.state_store.store_profile(profile.profile_id, profile.model_dump(mode="json"))
        return profile

    async def load_profile(self, profile_id: str, *, run_id: str | None = None) -> SessionProfile:
        profile = await self.get_profile(profile_id)
        if await self._emit_if_expired(profile, run_id=run_id):
            raise ValueError(f"Session profile expired: {profile_id}")
        if run_id is not None:
            await self.attach_profile_to_run(profile.profile_id, run_id)
        return profile

    async def get_profile(self, profile_id: str) -> SessionProfile:
        if self.state_store is None:
            raise RuntimeError("Session profiles require a runtime state store.")
        payload = await self.state_store.get_profile(profile_id)
        if payload is None:
            raise KeyError(f"Session profile not found: {profile_id}")
        return SessionProfile.model_validate(payload)

    async def list_profiles(self, agent_id: str | None = None) -> list[SessionProfile]:
        if self.state_store is None:
            return []
        rows = await self.state_store.list_profiles(agent_id=agent_id)
        return [SessionProfile.model_validate(row) for row in rows]

    async def delete_profile(self, profile_id: str) -> None:
        if self.state_store is None:
            raise RuntimeError("Session profiles require a runtime state store.")
        await self.state_store.delete_profile(profile_id)

    async def attach_profile_to_run(self, profile_id: str, run_id: str) -> SessionProfile:
        profile = await self.get_profile(profile_id)
        if await self._emit_if_expired(profile, run_id=run_id):
            raise ValueError(f"Session profile expired: {profile_id}")
        if self.state_store is None:
            raise RuntimeError("Session profiles require a runtime state store.")
        run_payload = await self.state_store.get_run(run_id)
        if run_payload is None:
            raise KeyError(f"Run not found: {run_id}")
        metadata = run_payload.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["session_profile_id"] = profile.profile_id
        metadata["session_profile_name"] = profile.name
        run_payload["metadata"] = metadata
        await self.state_store.store_run(run_id, run_payload)
        return profile

    async def get_profile_for_run(self, run_id: str) -> SessionProfile | None:
        if self.state_store is None:
            return None
        run_payload = await self.state_store.get_run(run_id)
        if run_payload is None:
            return None
        metadata = run_payload.get("metadata")
        if not isinstance(metadata, dict):
            return None
        profile_id = metadata.get("session_profile_id")
        if not isinstance(profile_id, str) or not profile_id:
            return None
        profile = await self.get_profile(profile_id)
        if await self._emit_if_expired(profile, run_id=run_id):
            return None
        return profile

    async def apply_profile_to_browser(self, run_id: str, context, page) -> SessionProfile | None:
        profile = await self.get_profile_for_run(run_id)
        if profile is None:
            return None
        if profile.cookies:
            try:
                await context.add_cookies(profile.cookies)
            except Exception:
                pass
        for origin, storage in profile.storage_by_origin.items():
            try:
                await page.goto(origin)
                await page.wait_for_load_state("domcontentloaded")
                await page.evaluate(
                    """
                    ({ localStorageData, sessionStorageData }) => {
                      Object.entries(localStorageData || {}).forEach(([key, value]) => window.localStorage.setItem(key, value));
                      Object.entries(sessionStorageData || {}).forEach(([key, value]) => window.sessionStorage.setItem(key, value));
                    }
                    """,
                    {
                        "localStorageData": storage.local_storage,
                        "sessionStorageData": storage.session_storage,
                    },
                )
            except Exception:
                continue
        return profile

    async def _emit_if_expired(self, profile: SessionProfile, *, run_id: str | None = None) -> bool:
        now = datetime.now(timezone.utc)
        expired = profile.expires_at is not None and profile.expires_at <= now
        if not expired:
            for value in profile.domain_expirations.values():
                if value is not None and value <= now:
                    expired = True
                    break
        if expired and self.event_publisher is not None:
            await self.event_publisher(
                RuntimeEvent(
                    event_type=EventType.SESSION_PROFILE_EXPIRED,
                    run_id=run_id,
                    agent_id=profile.agent_id,
                    source="session_profiles",
                    payload={
                        "profile_id": profile.profile_id,
                        "name": profile.name,
                        "expires_at": profile.expires_at.isoformat() if profile.expires_at else None,
                    },
                    severity=EventSeverity.WARNING,
                    correlation_id=profile.profile_id,
                )
            )
        return expired

    @staticmethod
    def _agent_id_from_session(session_payload: dict[str, object] | None) -> str | None:
        agent_id = None if session_payload is None else session_payload.get("agent_id")
        return str(agent_id) if isinstance(agent_id, str) and agent_id else None

    @staticmethod
    def _cookies_from_session(session_payload: dict[str, object] | None) -> list[dict[str, object]]:
        cookies = [] if session_payload is None else session_payload.get("cookies", [])
        return [dict(cookie) for cookie in cookies] if isinstance(cookies, list) else []

    @staticmethod
    def _storage_from_session(session_payload: dict[str, object] | None) -> dict[str, StorageSnapshot]:
        if session_payload is None:
            return {}
        current_url = session_payload.get("current_url")
        origin = SessionProfileManager._origin_from_url(str(current_url)) if isinstance(current_url, str) else None
        if origin is None:
            return {}
        local_storage = session_payload.get("local_storage", {})
        session_storage = session_payload.get("session_storage", {})
        return {
            origin: StorageSnapshot(
                local_storage=dict(local_storage) if isinstance(local_storage, dict) else {},
                session_storage=dict(session_storage) if isinstance(session_storage, dict) else {},
            )
        }

    @staticmethod
    def _auth_state_from_session(session_payload: dict[str, object] | None) -> dict[str, dict[str, object]]:
        if session_payload is None:
            return {}
        current_url = session_payload.get("current_url")
        hostname = urlparse(str(current_url)).hostname if isinstance(current_url, str) else None
        auth_state = session_payload.get("auth_state", {})
        if hostname is None or not isinstance(auth_state, dict):
            return {}
        return {hostname: dict(auth_state)}

    @staticmethod
    def _origin_from_url(url: str) -> str | None:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
