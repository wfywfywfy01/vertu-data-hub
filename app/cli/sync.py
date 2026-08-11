"""遍历 data_source，按 source_type 分发到对应 connector，记录 ingestion_run。

用法：
    python -m app.cli.sync --all              # 跑全部 enabled=true 的数据源
    python -m app.cli.sync --source <code>    # 只跑指定 code（即使 enabled=false 也会跑，便于手测）
"""
import argparse
import asyncio

from app import db
from app.catalog import registry
from app.connectors.registry import CONNECTOR_REGISTRY


async def sync_one(source: dict) -> None:
    connector_cls = CONNECTOR_REGISTRY.get(source["source_type"])
    if connector_cls is None:
        print(f"[{source['code']}] skip: unknown source_type {source['source_type']}")
        return

    run = await registry.start_run(source["id"])
    try:
        result = await connector_cls().sync(source)
        status = "success" if not result.errors else "failed"
        await registry.finish_run(
            run["id"], status, result.items_processed, "; ".join(result.errors) or None
        )
        await registry.mark_synced(source["id"])
        print(
            f"[{source['code']}] {status}: {result.items_processed} item(s) processed"
            + (f", errors: {result.errors}" if result.errors else "")
        )
    except Exception as exc:
        await registry.finish_run(run["id"], "failed", 0, str(exc))
        print(f"[{source['code']}] failed: {exc}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync data sources into vertu-data-hub")
    parser.add_argument("--all", action="store_true", help="sync all enabled data sources")
    parser.add_argument("--source", help="sync a single data source by code")
    args = parser.parse_args()

    if args.source:
        source = await registry.get_data_source(args.source)
        if not source:
            raise SystemExit(f"no such data_source: {args.source}")
        await sync_one(source)
    elif args.all:
        sources = await registry.list_data_sources(enabled_only=True)
        if not sources:
            print("no enabled data sources found — run `python -m app.cli.register_source` first")
        for source in sources:
            await sync_one(source)
    else:
        raise SystemExit("pass --all or --source <code>")

    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
