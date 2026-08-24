"""Validated ownership scopes for dealer, department, and company knowledge."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


SCOPE_TYPES = {"dealer", "department", "company"}
COMPANY_SCOPE_KEY = "vertu"


def normalize_scope_key(value: str) -> str:
    key = str(value or "").strip().casefold()
    if not key or len(key) > 160:
        raise ValueError("scope key must be between 1 and 160 characters")
    if any(character in key for character in "/\\") or key in {".", ".."}:
        raise ValueError("scope key contains unsafe path characters")
    if not key.isprintable():
        raise ValueError("scope key contains control characters")
    return key


@dataclass(frozen=True)
class KnowledgeScope:
    scope_type: str
    scope_key: str
    dealer_id: UUID | None = None

    @property
    def storage_prefix(self) -> str:
        segment = {
            "dealer": "dealers",
            "department": "departments",
            "company": "companies",
        }[self.scope_type]
        return f"{segment}/{self.scope_key}"


def resolve_scope(
    *,
    dealer_id: UUID | str | None = None,
    scope_type: str | None = None,
    scope_key: str | None = None,
) -> KnowledgeScope:
    scope_type = str(scope_type or ("dealer" if dealer_id is not None else "")).strip().lower()
    if scope_type not in SCOPE_TYPES:
        raise ValueError("scope_type must be dealer, department, or company")
    if scope_type == "dealer":
        if dealer_id is None:
            raise ValueError("dealer scope requires dealer_id")
        value = UUID(str(dealer_id))
        key = str(value)
        if scope_key is not None and normalize_scope_key(scope_key) != key:
            raise ValueError("dealer scope_key must match dealer_id")
        return KnowledgeScope("dealer", key, value)
    if dealer_id is not None:
        raise ValueError("shared scope must not include dealer_id")
    if scope_type == "company":
        key = normalize_scope_key(scope_key or COMPANY_SCOPE_KEY)
        if key != COMPANY_SCOPE_KEY:
            raise ValueError(f"company scope_key must be {COMPANY_SCOPE_KEY}")
        return KnowledgeScope("company", key)
    return KnowledgeScope("department", normalize_scope_key(scope_key or ""))


def authorized_scope_sql(
    alias: str,
    dealer_ids: list[UUID | str] | None,
    team_keys: list[str] | None,
) -> tuple[str, list]:
    if dealer_ids is None:
        return "TRUE", []
    conditions = [f"{alias}.scope_type = 'company'"]
    params: list = []
    if dealer_ids:
        conditions.append(f"({alias}.scope_type = 'dealer' AND {alias}.dealer_id = ANY(%s))")
        params.append(list(dealer_ids))
    normalized_teams = list(dict.fromkeys(normalize_scope_key(key) for key in team_keys or []))
    if normalized_teams:
        conditions.append(
            f"({alias}.scope_type = 'department' AND {alias}.scope_key = ANY(%s))"
        )
        params.append(normalized_teams)
    return f"({' OR '.join(conditions)})", params
