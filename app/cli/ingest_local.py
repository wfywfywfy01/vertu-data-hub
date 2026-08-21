"""Import local dealer files into the searchable knowledge pipeline."""
import argparse
import asyncio
from uuid import UUID

from app import db
from app.ingestion.local_inbox import ingest_local_path
from app.knowledge import assets, dealers
from app.knowledge.scopes import resolve_scope


async def _resolve_dealer(dealer_id: str | None, dealer_name: str | None) -> dict:
    if dealer_id:
        try:
            value = UUID(dealer_id)
        except ValueError as exc:
            raise ValueError("dealer ID must be a UUID") from exc
        rows = await dealers.list_dealers([value])
        if not rows:
            raise ValueError("dealer not found")
        return rows[0]

    matches = await dealers.search_dealers(dealer_name or "", limit=5)
    normalized_name = dealers.normalize_name(dealer_name or "")
    exact = [
        row
        for row in matches
        if dealers.normalize_name(row["official_name"]) == normalized_name
    ]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError("dealer not found")
    names = ", ".join(row["official_name"] for row in matches[:3])
    raise ValueError(f"dealer name is ambiguous: {names}; use --dealer-id")


async def run(args) -> int:
    dealer = None
    if args.department:
        scope = resolve_scope(scope_type="department", scope_key=args.department)
    elif args.company:
        scope = resolve_scope(scope_type="company")
    else:
        dealer = await _resolve_dealer(args.dealer_id, args.dealer)
        scope = resolve_scope(dealer_id=dealer["id"])
    result = await ingest_local_path(
        args.path,
        dealer_id=scope.dealer_id,
        scope_type=scope.scope_type,
        scope_key=scope.scope_key,
        category=args.category,
        sensitivity=args.sensitivity,
        actor_id=args.actor,
        language_code=args.language,
    )
    if dealer:
        print(f"dealer: {dealer['official_name']} ({dealer['id']})")
    else:
        print(f"scope: {scope.scope_type}/{scope.scope_key}")
    for item in result["items"]:
        if item["status"] == "failed":
            detail = f" error={item['error']}"
        else:
            detail = f" version={item['version']}"
        print(f"[{item['status']}] {item['file']}{detail}")
    print(
        f"summary: succeeded={result['succeeded']} unchanged={result['unchanged']} "
        f"failed={result['failed']}"
    )
    return 1 if result["failed"] else 0


async def main() -> None:
    parser = argparse.ArgumentParser(description="Import local dealer files")
    parser.add_argument("--path", required=True, help="file or folder to import")
    scope_group = parser.add_mutually_exclusive_group(required=True)
    scope_group.add_argument("--dealer", help="exact dealer name")
    scope_group.add_argument("--dealer-id", help="dealer UUID")
    scope_group.add_argument("--department", help="department key, such as overseas-sales")
    scope_group.add_argument("--company", action="store_true", help="company-wide Vertu scope")
    parser.add_argument(
        "--category",
        choices=sorted(assets.CATEGORIES),
        default="unclassified",
    )
    parser.add_argument(
        "--sensitivity",
        choices=sorted(assets.SENSITIVITIES),
        default="confidential",
    )
    parser.add_argument("--language", help="language code such as fa, en, or zh")
    parser.add_argument("--actor", default="local-admin")
    args = parser.parse_args()
    try:
        raise SystemExit(await run(args))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
