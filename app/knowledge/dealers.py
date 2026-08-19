"""Dealer master data and explicit PDCA ownership assignments."""
from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from uuid import UUID

from psycopg.types.json import Jsonb

from app import db


_ARABIC_VARIANTS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه"})


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().translate(_ARABIC_VARIANTS)
    return "".join(
        char for char in text
        if unicodedata.category(char)[0] in {"L", "N"}
    )


def _required(value: str, field: str, maximum: int) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    if not cleaned:
        raise ValueError(f"{field} is required")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return cleaned


def _country_code(value: str) -> str:
    code = str(value or "").strip().upper()
    if len(code) != 2 or not code.isalpha() or not code.isascii():
        raise ValueError("country_code must be a two-letter ISO code")
    return code


async def propose_dealer(
    *,
    official_name: str,
    country_code: str,
    proposed_by: str,
    city: str | None = None,
    language_codes: Iterable[str] = (),
    aliases: Iterable[str] = (),
    request_id: str | None = None,
) -> dict:
    name = _required(official_name, "official_name", 240)
    actor = _required(proposed_by, "proposed_by", 160)
    normalized = normalize_name(name)
    if not normalized:
        raise ValueError("official_name has no searchable characters")
    languages = sorted({str(code).strip().lower() for code in language_codes if str(code).strip()})
    if any(len(code) > 16 for code in languages):
        raise ValueError("language code exceeds 16 characters")

    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """
                INSERT INTO dealer
                    (official_name, normalized_name, country_code, city, language_codes, proposed_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (name, normalized, _country_code(country_code), city, languages, actor),
            )
            dealer = await cur.fetchone()
            unique_aliases = {normalize_name(value): " ".join(str(value).strip().split()) for value in (name, *aliases)}
            for alias_normalized, alias in unique_aliases.items():
                if not alias_normalized:
                    continue
                await conn.execute(
                    """
                    INSERT INTO dealer_alias (dealer_id, alias, normalized_alias)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (dealer_id, normalized_alias) DO NOTHING
                    """,
                    (dealer["id"], alias, alias_normalized),
                )
            await _audit(
                conn, actor, "dealer.proposed", "dealer", dealer["id"],
                {"status": "draft"}, request_id,
            )
            return dealer


async def search_dealers(
    query: str,
    *,
    dealer_ids: Iterable[UUID | str] | None = None,
    limit: int = 20,
) -> list[dict]:
    normalized = normalize_name(_required(query, "query", 240))
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    scoped_ids = None if dealer_ids is None else list(dealer_ids)
    if scoped_ids == []:
        return []
    return await db.fetch_all(
        """
        SELECT d.*,
               greatest(
                   similarity(d.normalized_name, %s),
                   coalesce(max(similarity(a.normalized_alias, %s)), 0)
               ) AS match_score
        FROM dealer d
        LEFT JOIN dealer_alias a ON a.dealer_id = d.id AND a.active
        WHERE d.status <> 'merged'
          AND (
              d.normalized_name LIKE '%%' || %s || '%%'
              OR a.normalized_alias LIKE '%%' || %s || '%%'
              OR similarity(d.normalized_name, %s) >= 0.18
              OR similarity(a.normalized_alias, %s) >= 0.18
          )
          AND (%s::uuid[] IS NULL OR d.id = ANY(%s::uuid[]))
        GROUP BY d.id
        ORDER BY match_score DESC, d.official_name
        LIMIT %s
        """,
        (
            normalized, normalized, normalized, normalized, normalized, normalized,
            scoped_ids, scoped_ids, limit,
        ),
    )


async def confirm_dealer(
    dealer_id: UUID | str,
    *,
    confirmed_by: str,
    expected_version: int,
    request_id: str | None = None,
) -> dict:
    actor = _required(confirmed_by, "confirmed_by", 160)
    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """
                UPDATE dealer
                SET status = 'active', confirmed_by = %s, confirmed_at = now(),
                    version = version + 1, updated_at = now()
                WHERE id = %s AND version = %s AND status IN ('draft','active')
                RETURNING *
                """,
                (actor, dealer_id, expected_version),
            )
            row = await cur.fetchone()
            if not row:
                raise ValueError("dealer not found or version conflict")
            await _audit(
                conn, actor, "dealer.confirmed", "dealer", row["id"],
                {"version": row["version"]}, request_id,
            )
            return row


async def list_dealers(dealer_ids: Iterable[UUID | str] | None = None, *, limit: int = 100) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if dealer_ids is None:
        return await db.fetch_all(
            "SELECT * FROM dealer WHERE status <> 'merged' ORDER BY official_name LIMIT %s",
            (limit,),
        )
    ids = list(dealer_ids)
    if not ids:
        return []
    return await db.fetch_all(
        "SELECT * FROM dealer WHERE status <> 'merged' AND id = ANY(%s) ORDER BY official_name LIMIT %s",
        (ids, limit),
    )


async def assign_owner(
    dealer_id: UUID | str,
    *,
    principal_id: str,
    assigned_by: str,
    team_key: str | None = None,
) -> dict:
    principal = _required(principal_id, "principal_id", 160)
    actor = _required(assigned_by, "assigned_by", 160)
    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """
                INSERT INTO dealer_owner (dealer_id, principal_id, team_key, assigned_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (dealer_id, principal_id) DO UPDATE SET
                    team_key = EXCLUDED.team_key,
                    assigned_by = EXCLUDED.assigned_by,
                    assigned_at = now(),
                    active = TRUE
                RETURNING *
                """,
                (dealer_id, principal, team_key, actor),
            )
            row = await cur.fetchone()
            await _audit(conn, actor, "dealer.owner_assigned", "dealer", row["dealer_id"], {"principal_id": principal})
            return row


async def list_dealer_ids_for_principal(principal_id: str) -> list[UUID]:
    principal = _required(principal_id, "principal_id", 160)
    rows = await db.fetch_all(
        "SELECT dealer_id FROM dealer_owner WHERE principal_id = %s AND active ORDER BY dealer_id",
        (principal,),
    )
    return [row["dealer_id"] for row in rows]


async def _audit(
    conn, actor_id: str, action: str, object_type: str, object_id, payload: dict,
    request_id: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO audit_event (actor_id, action, object_type, object_id, payload, request_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (actor_id, action, object_type, object_id, Jsonb(payload), request_id),
    )

