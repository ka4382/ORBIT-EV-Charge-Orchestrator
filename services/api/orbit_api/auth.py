"""JWT verification and RBAC. HS256 with JWT_DEV_SECRET when JWKS_URL is empty, else RS256 via cached JWKS."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, Request
from jwt import PyJWKClient
from orbit_common.config import settings
from pydantic import BaseModel, Field

from .problem import ProblemException

ROLES = ("admin", "manager", "finance", "auditor")
Role = str
DEV_TOKEN_TTL_S = 3600


@dataclass(frozen=True, slots=True)
class Principal:
    sub: str
    tenant_id: int
    role: Role


class DevTokenRequest(BaseModel):
    tenant_id: int = Field(ge=1)
    role: str = Field(pattern="^(admin|manager|finance|auditor)$")
    sub: str | None = None


class DevTokenResponse(BaseModel):
    access_token: str
    expires_in: int
    token_type: str = "bearer"  # noqa: S105 - not a secret, the OAuth2 token type


class MeResponse(BaseModel):
    sub: str
    tenant_id: int
    role: str


def _unauthorized(detail: str) -> ProblemException:
    return ProblemException(
        401, detail, type_="urn:orbit:problem:unauthorized", headers={"WWW-Authenticate": "Bearer"}
    )


@lru_cache
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, lifespan=600)


def mint_dev_token(tenant_id: int, role: str, sub: str | None = None, ttl_s: int = DEV_TOKEN_TTL_S) -> str:
    s = settings()
    now = int(time.time())
    claims = {
        "sub": sub or f"dev-{uuid.uuid4().hex[:8]}",
        "tenant_id": tenant_id,
        "role": role,
        "iss": s.jwt_issuer,
        "iat": now,
        "exp": now + ttl_s,
    }
    return jwt.encode(claims, s.jwt_dev_secret, algorithm="HS256")


def _claims_to_principal(claims: dict[str, Any]) -> Principal:
    sub, tenant_id, role = claims.get("sub"), claims.get("tenant_id"), claims.get("role")
    if not sub or not isinstance(sub, str):
        raise _unauthorized("token missing sub")
    if not isinstance(tenant_id, int) or isinstance(tenant_id, bool) or tenant_id < 1:
        raise _unauthorized("token missing tenant_id")
    if role not in ROLES:
        raise _unauthorized("token has unknown role")
    return Principal(sub=sub, tenant_id=tenant_id, role=role)


async def verify_token(token: str) -> Principal:
    s = settings()
    options: Any = {"require": ["exp", "sub"]}
    try:
        if s.jwks_url:
            key = await asyncio.to_thread(_jwks_client(s.jwks_url).get_signing_key_from_jwt, token)
            claims = jwt.decode(token, key.key, algorithms=["RS256"], issuer=s.jwt_issuer, options=options)
        else:
            claims = jwt.decode(
                token, s.jwt_dev_secret, algorithms=["HS256"], issuer=s.jwt_issuer, options=options
            )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("token expired") from exc
    except jwt.PyJWTError as exc:
        raise _unauthorized("invalid token") from exc
    return _claims_to_principal(claims)


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("missing bearer token")
    return token.strip()


async def authenticate(request: Request) -> Principal:
    """Verify the bearer token, stash the principal on request.state (for audit/logging middleware)."""
    principal = await verify_token(_bearer(request))
    request.state.principal = principal
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is not None:
        await limiter.check(principal.sub)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(authenticate)]


def require(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: `Depends(require("manager", "admin"))`. No roles = any authenticated role."""
    allowed = frozenset(roles)

    async def dependency(principal: CurrentPrincipal) -> Principal:
        if allowed and principal.role not in allowed:
            raise ProblemException(
                403,
                f"role '{principal.role}' may not perform this action",
                type_="urn:orbit:problem:forbidden",
            )
        return principal

    return dependency


router = APIRouter(tags=["auth"])


@router.post(
    "/auth/dev-token", response_model=DevTokenResponse, summary="Mint a dev HS256 token (ORBIT_ENV=dev only)"
)
async def dev_token(body: DevTokenRequest) -> DevTokenResponse:
    if settings().orbit_env != "dev":
        raise ProblemException(404, "dev token issuer is disabled outside ORBIT_ENV=dev")
    return DevTokenResponse(
        access_token=mint_dev_token(body.tenant_id, body.role, body.sub), expires_in=DEV_TOKEN_TTL_S
    )


@router.get("/me", response_model=MeResponse, summary="Who am I")
async def me(principal: CurrentPrincipal) -> MeResponse:
    return MeResponse(sub=principal.sub, tenant_id=principal.tenant_id, role=principal.role)
