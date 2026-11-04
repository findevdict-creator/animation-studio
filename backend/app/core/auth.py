"""Authentication and authorisation (architecture 14).

Every request but health and readiness carries an OIDC access token. The token proves who
the caller is and which global roles the identity provider gives them; what each role may do
is the ``role_permissions`` table, so grants change without a deploy or a new token.

    @router.post("/jobs")
    def submit(..., who: Principal = Depends(require("job.submit"))): ...
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.database import get_session
from app.core.errors import ApiError
from app.models.audit import AuditLog
from app.models.identity import Role, User

log = logging.getLogger("studio.auth")


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    external_id: str
    email: str | None
    name: str
    roles: frozenset[str]
    permissions: frozenset[str] = field(default_factory=frozenset)

    def can(self, permission: str) -> bool:
        return permission in self.permissions


class KeySource:
    """Signing keys: a JWKS URL (cached, refetched on an unknown ``kid``) or a static JWKS."""

    def __init__(self, settings: Settings):
        self._static: dict[str, jwt.PyJWK] | None = None
        self._client: jwt.PyJWKClient | None = None
        if settings.auth_jwks_json:
            jwks = jwt.PyJWKSet.from_dict(json.loads(settings.auth_jwks_json))
            self._static = {k.key_id: k for k in jwks.keys}
        else:
            url = settings.auth_jwks_url or settings.auth_issuer.rstrip("/") + "/protocol/openid-connect/certs"
            self._client = jwt.PyJWKClient(url, cache_keys=True, lifespan=300, timeout=5)

    def key_for(self, token: str) -> Any:
        if self._static is not None:
            kid = jwt.get_unverified_header(token).get("kid")
            if kid not in self._static:
                raise jwt.InvalidTokenError("unknown signing key")
            return self._static[kid].key
        assert self._client is not None
        return self._client.get_signing_key_from_jwt(token).key


@lru_cache
def key_source() -> KeySource:
    return KeySource(get_settings())


def decode_token(token: str, settings: Settings | None = None, keys: KeySource | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    try:
        key = (keys or key_source()).key_for(token)
        return jwt.decode(
            token, key, algorithms=s.auth_algorithms, audience=s.auth_audience, issuer=s.auth_issuer,
            leeway=s.auth_leeway_s, options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWKClientError as exc:
        log.warning("could not fetch signing keys: %s", exc)
        raise ApiError(503, "auth_keys_unavailable", "the identity provider's keys could not be fetched") from None
    except jwt.InvalidTokenError as exc:
        raise ApiError(401, "invalid_token", str(exc), headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}) from None


def _claim(claims: dict[str, Any], dotted: str) -> Any:
    value: Any = claims
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def principal_from_claims(session: Session, claims: dict[str, Any], settings: Settings | None = None) -> Principal:
    """Find or create the user, sync global roles from the token, resolve permissions."""
    s = settings or get_settings()
    sub = str(claims["sub"])
    token_roles = set(_claim(claims, s.auth_roles_claim) or [])
    known = {r.name: r for r in session.scalars(select(Role))}
    roles = [known[r] for r in sorted(token_roles & set(known))]

    user = session.scalar(select(User).where(User.external_id == sub))
    created = user is None
    if user is None:
        user = User(external_id=sub, display_name="", active=True)
        session.add(user)
    user.email = claims.get("email") or user.email
    user.display_name = claims.get("name") or claims.get("preferred_username") or user.display_name or sub
    if not user.active:
        raise ApiError(403, "user_disabled", "this account is disabled")
    if {r.name for r in user.roles} != {r.name for r in roles}:
        before = sorted(r.name for r in user.roles)
        user.roles = roles
        session.flush()
        session.add(AuditLog(actor_id=user.id, action="user.roles_synced", target_kind="user", target_id=user.id,
                             detail={"before": before, "after": [r.name for r in roles]}))
    session.flush()
    if created:
        session.add(AuditLog(actor_id=user.id, action="user.created", target_kind="user", target_id=user.id,
                             detail={"sub": sub, "email": user.email}))
    perms = frozenset(p.name for r in roles for p in r.permissions)
    return Principal(user.id, sub, user.email, user.display_name, frozenset(r.name for r in roles), perms)


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "unauthorized", "a bearer token is required", headers={"WWW-Authenticate": "Bearer"})
    return token.strip()


def current_principal(request: Request, session: Session = Depends(get_session)) -> Principal:
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    principal = principal_from_claims(session, decode_token(bearer_token(request)))
    session.commit()
    request.state.principal = principal
    return principal


def require(permission: str):
    """Dependency factory: the caller must hold ``permission``."""

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(permission):
            raise ApiError(403, "forbidden", f"missing permission {permission}", required=permission)
        return principal

    dependency.__name__ = f"require_{permission.replace('.', '_')}"
    return dependency


def audit(session: Session, who: Principal | None, action: str, target_kind: str | None = None,
          target_id: uuid.UUID | None = None, **detail: Any) -> None:
    session.add(AuditLog(actor_id=who.user_id if who else None, action=action, target_kind=target_kind,
                         target_id=target_id, detail=detail or None))
