from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, WebSocket, status
from pydantic import BaseModel, Field

from synapse.config import Settings
from synapse.security.policies import PrincipalType, scopes_require_project_context
from synapse.security.tokens import JWTCodec, TokenValidationError


class AuthPrincipal(BaseModel):
    subject: str
    principal_type: PrincipalType
    scopes: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    organization_id: str | None = None
    project_id: str | None = None
    api_key_id: str | None = None


class Authenticator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.codec = JWTCodec(settings.jwt_secret, settings.jwt_issuer, settings.jwt_audience)

    def issue_token(
        self,
        *,
        subject: str,
        principal_type: PrincipalType,
        scopes: list[str],
        agent_id: str | None = None,
        organization_id: str | None = None,
        project_id: str | None = None,
        api_key_id: str | None = None,
        expires_in_seconds: int | None = None,
    ) -> str:
        return self.codec.encode(
            {
                "sub": subject,
                "type": principal_type.value,
                "scopes": scopes,
                "agent_id": agent_id,
                "organization_id": organization_id,
                "project_id": project_id,
                "api_key_id": api_key_id,
            },
            expires_in_seconds=expires_in_seconds or self.settings.jwt_expiration_seconds,
        )

    def authenticate_token(self, token: str | None) -> AuthPrincipal:
        if not self.settings.auth_required:
            return AuthPrincipal(
                subject="development",
                principal_type=PrincipalType.OPERATOR,
                scopes=["admin"],
                organization_id="development",
                project_id="development",
            )
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token.")
        try:
            payload = self.codec.decode(token)
        except TokenValidationError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        scopes = payload.get("scopes", [])
        if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token scopes.")
        subject = payload.get("sub")
        principal_type = payload.get("type")
        if not isinstance(subject, str) or not isinstance(principal_type, str):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject.")
        principal = AuthPrincipal(
            subject=subject,
            principal_type=PrincipalType(principal_type),
            scopes=list(scopes),
            agent_id=payload.get("agent_id") if isinstance(payload.get("agent_id"), str) else None,
            organization_id=payload.get("organization_id") if isinstance(payload.get("organization_id"), str) else None,
            project_id=payload.get("project_id") if isinstance(payload.get("project_id"), str) else None,
            api_key_id=payload.get("api_key_id") if isinstance(payload.get("api_key_id"), str) else None,
        )
        if scopes_require_project_context(principal.scopes):
            if not principal.organization_id or not principal.project_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authenticated principals must include organization_id and project_id.",
                )
        return principal

    def authorize(self, principal: AuthPrincipal, required_scopes: tuple[str, ...], *, agent_id: str | None = None) -> AuthPrincipal:
        missing = [scope for scope in required_scopes if scope not in principal.scopes]
        if missing:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing required scopes: {', '.join(missing)}")
        if agent_id is not None and principal.principal_type == PrincipalType.AGENT and principal.agent_id not in {None, agent_id}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent token cannot access another agent identity.")
        if scopes_require_project_context(principal.scopes):
            if not principal.organization_id or not principal.project_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Principal is missing project-scoped tenant context.",
                )
        return principal

    def authorize_project(self, principal: AuthPrincipal, project_id: str) -> AuthPrincipal:
        if not principal.project_id or principal.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token is not authorized for this project.")
        return principal

    def authorize_agent_binding(
        self,
        principal: AuthPrincipal,
        *,
        agent_id: str,
        organization_id: str | None,
        project_id: str | None,
        allow_service: bool = False,
    ) -> AuthPrincipal:
        if not organization_id or not project_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Target agent is missing tenant scope.")
        if principal.organization_id != organization_id or principal.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token is not authorized for this agent project.")
        if principal.principal_type == PrincipalType.AGENT:
            if principal.agent_id != agent_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent token cannot bind to another agent.")
            return principal
        if principal.principal_type == PrincipalType.SERVICE and allow_service:
            return principal
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Principal is not allowed to act on behalf of this agent.")


BearerHeader = Annotated[str | None, Header(alias="Authorization")]


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def get_authenticator() -> Authenticator:
    from synapse.main import authenticator

    return authenticator


async def get_current_principal(
    authorization: BearerHeader = None,
    authenticator: Authenticator = Depends(get_authenticator),
) -> AuthPrincipal:
    return authenticator.authenticate_token(_extract_bearer_token(authorization))


def require_scopes(*required_scopes: str, agent_param: str | None = None):
    async def dependency(
        principal: AuthPrincipal = Depends(get_current_principal),
        authenticator: Authenticator = Depends(get_authenticator),
    ) -> AuthPrincipal:
        agent_id = None
        if agent_param is not None and principal.principal_type == PrincipalType.AGENT:
            agent_id = principal.agent_id
        return authenticator.authorize(principal, required_scopes, agent_id=agent_id)

    return dependency


def require_project_access(project_param: str = "project_id"):
    async def dependency(
        request: Request,
        principal: AuthPrincipal = Depends(get_current_principal),
        authenticator: Authenticator = Depends(get_authenticator),
    ) -> AuthPrincipal:
        project_id = request.path_params.get(project_param) or request.query_params.get(project_param)
        if not isinstance(project_id, str) or not project_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project context is required.")
        return authenticator.authorize_project(principal, project_id)

    return dependency


def authenticate_websocket(
    websocket: WebSocket,
    authenticator: Authenticator,
    *,
    required_scopes: tuple[str, ...],
    agent_id: str | None = None,
) -> AuthPrincipal:
    authorization = websocket.headers.get("authorization")
    token = _extract_bearer_token(authorization) or websocket.query_params.get("token")
    principal = authenticator.authenticate_token(token)
    return authenticator.authorize(principal, required_scopes, agent_id=agent_id)
