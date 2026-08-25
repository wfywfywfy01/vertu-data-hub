"""Backfill processed images with cloud multimodal vectors."""
import argparse
import asyncio

from app import db
from app.cli.ingest_local import _resolve_dealer
from app.semantic_images import index_semantic_images


async def main() -> None:
    parser = argparse.ArgumentParser(description="Index cloud multimodal image vectors")
    dealer_group = parser.add_mutually_exclusive_group(required=True)
    dealer_group.add_argument("--dealer", help="exact dealer name")
    dealer_group.add_argument("--dealer-id", help="dealer UUID")
    dealer_group.add_argument("--all", action="store_true", help="all eligible image assets")
    parser.add_argument("--force", action="store_true", help="reindex existing images")
    args = parser.parse_args()
    try:
        dealer = None if args.all else await _resolve_dealer(args.dealer_id, args.dealer)
        count = await index_semantic_images(
            dealer_id=dealer["id"] if dealer else None,
            force=args.force,
        )
        if dealer:
            print(f"dealer: {dealer['official_name']} ({dealer['id']})")
        else:
            print("scope: all internal/confidential image assets")
        print(f"multimodal images indexed: {count}")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
