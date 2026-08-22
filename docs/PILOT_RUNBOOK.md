# Pilot Runbook

This runbook is for a controlled dealer PDCA pilot. Raw files live in a private
OSS bucket; PostgreSQL stores metadata, normalized rows, chunks, and vectors.

## 1. Prerequisites

- Use a reviewed `main` commit on a cloud worker with Python 3.10+.
- RDS `10.140.1.83:5432` is reachable from that worker.
- The `vertu_data_hub` database exists and has the `vector` extension.
- Create one private OSS bucket in the same region as the worker when possible.
- Give the worker RAM credentials limited to read/list on the pilot prefixes.
- Keep `.env` on the worker only. Never upload or commit it.

Required production values:

```text
APP_ENV=production
DATABASE_URL=postgresql://<user>:<password>@10.140.1.83:5432/vertu_data_hub
WATCHED_ROOT=/srv/vertu-agent/inbox
OSS_ACCESS_KEY_ID=<ram_access_key_id>
OSS_ACCESS_KEY_SECRET=<ram_access_key_secret>
OSS_ENDPOINT=oss-cn-<region>.aliyuncs.com
OSS_BUCKET=<private_bucket>
EMBEDDING_PROVIDER=api
EMBEDDING_BASE_URL=<openai_compatible_url>
EMBEDDING_API_KEY=<key>
EMBEDDING_MODEL=text-embedding-v3
IMAGE_EMBEDDING_PROVIDER=hash
ALLOW_EXTERNAL_IMAGE_PROCESSING=false
SEMANTIC_IMAGE_BATCH_SIZE=4
```

## 2. Upload Pilot Files

In the Alibaba Cloud OSS console, open the private bucket and upload files under
these exact prefixes:

```text
raw/docs/pilot-202608/       PDF, DOCX, MD, TXT
raw/images/pilot-202608/     JPG, PNG, WEBP
raw/sales/pilot-202608/      XLSX, XLS, CSV
quarantine/pilot-202608/     files not yet classified
```

Subfolders are preserved. Do not put passwords, identity documents, payment
card data, or unrelated customer PII into the pilot bucket.

## 3. Initialize And Sync

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python scripts/init_db.py
python -m app.cli.preload_semantic_images
python -m app.cli.register_source
python -m app.cli.sync_oss --all
```

The command downloads only changed objects to `WATCHED_ROOT`, then runs the
existing idempotent parsers. A parser failure exits non-zero and is recorded in
`ingestion_run` and `source_item`.

## 4. Acceptance

```sql
SELECT current_database(), inet_server_addr();
SELECT extversion FROM pg_extension WHERE extname = 'vector';
SELECT code, last_synced_at FROM data_source ORDER BY code;
SELECT status, count(*) FROM source_item GROUP BY status;
SELECT status, items_processed, error, finished_at
FROM ingestion_run ORDER BY id DESC LIMIT 20;
SELECT count(*) FROM doc_chunk;
SELECT count(*) FROM media_asset;
SELECT count(*) FROM image_embedding WHERE semantic_embedding IS NOT NULL;
SELECT count(*) FROM structured_record;
```

Run `python -m app.cli.sync_oss --all` again. The second run must report zero
processed items for unchanged files.

## 5. Rollback

Record the RDS snapshot identifier and deployed Git commit before first ingest.
Keep pilot files under `pilot-202608/` so they can be isolated. Do not delete OSS
objects during rollback; disable the worker, restore the snapshot if required,
and retain originals for audit.
