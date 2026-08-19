"""Download private OSS inbox objects, then run existing idempotent file ingestion."""
import argparse
import asyncio

from app import db
from app.catalog import registry
from app.cli.sync import sync_one
from app.config import settings, validate_production_settings
from app.oss_inbox import SOURCE_PREFIXES, download_sources


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync OSS inbox into vertu-data-hub")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--source", choices=sorted(SOURCE_PREFIXES))
    args = parser.parse_args()

    validate_production_settings()
    for name in ("oss_access_key_id", "oss_access_key_secret", "oss_endpoint", "oss_bucket"):
        if not getattr(settings, name):
            raise SystemExit(f"{name.upper()} is required")

    codes = list(SOURCE_PREFIXES) if args.all else [args.source]
    downloaded = await asyncio.to_thread(download_sources, codes)
    outcomes = []
    for code in codes:
        print(f"[{code}] OSS mirror: {downloaded[code]}")
        source = await registry.get_data_source(code)
        if not source:
            raise SystemExit("data sources missing; run `python -m app.cli.register_source` first")
        outcomes.append(await sync_one(source))
    await db.close_pool()
    if not all(outcomes):
        raise SystemExit("one or more OSS sources failed")


if __name__ == "__main__":
    asyncio.run(main())
