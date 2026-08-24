"""Validate short-lived PDCA service JWTs and dealer scope."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import Header
import jwt

from app.api.errors import ApiError
from app.config import settings
from app.knowledge.scopes import KnowledgeScope, normalize_scope_key


@dataclass(frozen=True)
class ServiceClaims:
    user_id: str
    role: str
    scope: str
    dealer_ids: frozenset[UUID]
    team_keys: frozenset[str]
    reauthenticated_at: datetime | None = None
    reauthentication_purpose: str | None = None

    @property
    def unrestricted(self) -> bool:
        return self.role == "admin" and self.scope == "all"

    def require_dealer(self, dealer_id: UUID) -> None:
        if not self.unrestricted and dealer_id not in self.dealer_ids:
            raise ApiError(403, "dealer_scope_denied", "Dealer is outside the caller scope")

    def require_knowledge_scope(self, knowledge_scope: KnowledgeScope) -> None:
        if self.unrestricted or knowledge_scope.scope_type == "company":
            return
        if knowledge_scope.scope_type == "dealer":
            self.require_dealer(knowledge_scope.dealer_id)
            return
        if knowledge_scope.scope_key not in self.team_keys:
            raise ApiError(
                403,
                "department_scope_denied",
                "Department is outside the caller scope",
            )

    def require_role(self, *roles: str) -> None:
        if self.role not in roles:
            raise ApiError(403, "role_denied", "Role is not allowed for this operation")

    def require_recent_reauth(self, now: datetime, max_age_seconds: int = 300) -> None:
        if (
            self.reauthenticated_at is None
            or self.reauthentication_purpose != "knowledge-original-export"
        ):
            raise ApiError(403, "recent_reauth_required", "Recent step-up authentication is required")
        age = (now - self.reauthenticated_at).total_seconds()
        if age < 0 or age > max_age_seconds:
            raise ApiError(403, "recent_reauth_required", "Recent step-up authentication is required")


def service_token_key() -> str:
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
            service_token_key(),
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
        raw_team_keys = payload.get("team_keys", [])
        if not isinstance(raw_team_keys, list):
            raise ValueError("team_keys must be a list")
        team_keys = frozenset(normalize_scope_key(value) for value in raw_team_keys)
        reauthenticated_at = None
        reauthentication_purpose = None
        if "reauth_at" in payload:
            reauthenticated_at = datetime.fromtimestamp(float(payload["reauth_at"]), timezone.utc)
            reauthentication_purpose = str(payload.get("reauth_purpose") or "")
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise ApiError(401, "invalid_service_token", "Service token is invalid or expired")
    return ServiceClaims(
        user_id=str(payload["user_id"]),
        role=role,
        scope=scope,
        dealer_ids=dealer_ids,
        team_keys=team_keys,
        reauthenticated_at=reauthenticated_at,
        reauthentication_purpose=reauthentication_purpose,
    )

