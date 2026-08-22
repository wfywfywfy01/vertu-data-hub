"""Index processed images for local text-to-image retrieval."""
import argparse
import asyncio

from app import db
from app.cli.ingest_local import _resolve_dealer
from app.semantic_images import index_semantic_images


async def main() -> None:
    parser = argparse.ArgumentParser(description="Index semantic image vectors")
    dealer_group = parser.add_mutually_exclusive_group(required=True)
    dealer_group.add_argument("--dealer", help="exact dealer name")
    dealer_group.add_argument("--dealer-id", help="dealer UUID")
    parser.add_argument("--force", action="store_true", help="reindex existing images")
    args = parser.parse_args()
    try:
        dealer = await _resolve_dealer(args.dealer_id, args.dealer)
        count = await index_semantic_images(dealer_id=dealer["id"], force=args.force)
        print(f"dealer: {dealer['official_name']} ({dealer['id']})")
        print(f"semantic images indexed: {count}")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
