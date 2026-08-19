"""Validate short-lived PDCA service JWTs and dealer scope."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from fastapi import Header
import jwt

from app.api.errors import ApiError
from app.config import settings


@dataclass(frozen=True)
class ServiceClaims:
    user_id: str
    role: str
    scope: str
    dealer_ids: frozenset[UUID]

    @property
    def unrestricted(self) -> bool:
        return self.role == "admin" and self.scope == "all"

    def require_dealer(self, dealer_id: UUID) -> None:
        if not self.unrestricted and dealer_id not in self.dealer_ids:
            raise ApiError(403, "dealer_scope_denied", "Dealer is outside the caller scope")

    def require_role(self, *roles: str) -> None:
        if self.role not in roles:
            raise ApiError(403, "role_denied", "Role is not allowed for this operation")


def _key() -> str:
    if settings.service_token_key_file:
        try:
            value = Path(settings.service_token_key_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ApiError(503, "service_token_unavailable", "Service token key is unavailable") from exc
    else:
        value = settings.service_token_secret
    if len(value.encode("utf-8")) < 32:
        raise ApiError(503, "service_token_unavailable", "Service token key is unavailable")
    return value


async def require_service_claims(authorization: str = Header(default="")) -> ServiceClaims:
    if not authorization.startswith("Bearer "):
        raise ApiError(401, "invalid_service_token", "Bearer service token is required")
    try:
        payload = jwt.decode(
            authorization[7:],
            _key(),
            algorithms=["HS256"],
            audience=settings.service_token_audience,
            issuer=settings.service_token_issuer,
            options={"require": ["sub", "user_id", "role", "scope", "dealer_ids", "iat", "exp", "jti"]},
        )
        if int(payload["exp"]) - int(payload["iat"]) > 300:
            raise ValueError("token lifetime exceeds five minutes")
        role = str(payload["role"])
        scope = str(payload["scope"])
        if role not in {"viewer", "dealer", "sales", "manager", "admin"}:
            raise ValueError("unknown role")
        if scope not in {"none", "self", "team", "all"}:
            raise ValueError("unknown scope")
        if scope == "all" and role != "admin":
            raise ValueError("all scope requires admin")
        dealer_ids = frozenset(UUID(str(value)) for value in payload["dealer_ids"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise ApiError(401, "invalid_service_token", "Service token is invalid or expired")
    return ServiceClaims(
        user_id=str(payload["user_id"]),
        role=role,
        scope=scope,
        dealer_ids=dealer_ids,
    )

