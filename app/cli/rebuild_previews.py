"""Bounded, local-OCR-only backfill of safe image previews; dry-run by default."""
import argparse
import asyncio
import hashlib
import json

from app import db
from app.knowledge import assets
from app.processing.images import extract_image
from app.processing.sensitivity import high_sensitivity_reasons
from app.storage import LocalStorage, get_storage
from app.workers.execution import job_execution
from app.workers.image import save_safe_preview


async def rebuild(*, limit=10, apply=False):
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    jobs = await db.fetch_all(
        """SELECT DISTINCT ON (v.id) j.id FROM processing_job j
           JOIN asset_version v ON v.id = j.asset_version_id
           JOIN knowledge_asset a ON a.id = v.asset_id
           JOIN source_object s ON s.id = v.source_object_id
           WHERE a.status = 'searchable' AND v.is_current AND j.status = 'succeeded'
             AND s.content_type LIKE 'image/%%'
             AND NOT EXISTS (SELECT 1 FROM derived_artifact d WHERE d.asset_version_id = v.id
                             AND d.artifact_type = 'safe_preview' AND d.pipeline_version = 'safe-preview-v1')
           ORDER BY v.id, j.created_at LIMIT %s""", (limit,))
    result = {"selected": len(jobs), "rebuilt": 0, "failed": 0, "dry_run": not apply}
    if not apply:
        return result
    for job in jobs:
        try:
            async with job_execution(job["id"]) as acquired:
                if not acquired:
                    continue
                context = await assets.get_job_context(job["id"])
                storage = LocalStorage() if context["bucket"] == "local-inbox" else get_storage()
                source = await asyncio.to_thread(storage.download_bytes, context["object_key"])
                if len(source) != context["byte_size"] or hashlib.sha256(source).hexdigest() != context["content_hash"]:
                    raise ValueError("source integrity mismatch")
                extracted = await asyncio.to_thread(extract_image, source, context["language_code"])
                reasons = high_sensitivity_reasons(extracted.text, filename=context["original_name"],
                                                   sensitivity=context["sensitivity"])
                await save_safe_preview(context, source, extracted, storage, restricted=bool(reasons))
                await db.execute(
                    """INSERT INTO audit_event (actor_id, action, object_type, object_id, payload)
                       VALUES ('system-worker', 'asset.safe_preview_rebuilt', 'knowledge_asset', %s,
                               jsonb_build_object('asset_version_id', %s::text))""",
                    (context["asset_id"], str(context["asset_version_id"])),
                )
                result["rebuilt"] += 1
        except Exception:
            result["failed"] += 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    async def run():
        try:
            return await rebuild(limit=args.limit, apply=args.apply)
        finally:
            await db.close_pool()
    result = asyncio.run(run())
    print(json.dumps(result))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
