# Cloud Cutover

## Target

Vertu production RDS is documented as `10.140.1.83:5432`.

- Database: `vertu_data_hub`
- Extension: `vector`
- File ingestion runs on an application/worker host, not inside RDS.
- The RDS endpoint is reachable from this workstation, but credentials and the
  production extension still require explicit verification.

## Configure

Create a server-side `.env` from this shape. Do not commit it:

```text
DATABASE_URL=postgresql://<datahub_user>:<password>@10.140.1.83:5432/vertu_data_hub
WATCHED_ROOT=/srv/vertu-agent/data/inbox
EMBEDDING_PROVIDER=api
EMBEDDING_BASE_URL=<embedding_api_base_url>
EMBEDDING_API_KEY=<embedding_api_key>
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_DIM=1024
IMAGE_EMBEDDING_PROVIDER=api
IMAGE_EMBEDDING_BASE_URL=<image_embedding_api_base_url>
IMAGE_EMBEDDING_API_KEY=<image_embedding_api_key>
IMAGE_EMBEDDING_MODEL=multimodal-embedding-v1
IMAGE_EMBEDDING_DIM=1024
```

Use `hash` providers only for local or acceptance testing. They are not
production semantic embeddings.

## Initialize

Run from the reviewed `main` commit on the cloud worker:

```bash
python scripts/init_db.py
python -m app.cli.register_source
```

Before running these commands, verify the target with a least-privilege account:

```sql
SELECT current_database(), inet_server_addr();
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

## Put files in

The worker host must provide these directories under `WATCHED_ROOT`:

```text
/srv/vertu-agent/data/inbox/
├── 政策产品文档/
├── 陈列装修图片/
├── 销售历史数据/
└── 其他-待定/
```

Copy official files to the worker host, then run:

```bash
python -m app.cli.sync --source policy_product_docs
python -m app.cli.sync --source store_display_media
python -m app.cli.sync --source sales_history_files
```

The current connector reads a mounted filesystem. Original files stay on that
worker path; chunks and vectors go to `vertu_data_hub`. If originals must live in
OSS instead, add the object-storage connector before removing this mount.

## Acceptance

- `vector` extension exists and has the expected version.
- Source rows and ingestion runs show expected counts.
- `doc_chunk` contains source text and 1024-dimensional vectors.
- Repeating the same sync does not duplicate records.
- A sample retrieval returns the expected source file.
- Backup and rollback point are recorded before bulk ingestion.
