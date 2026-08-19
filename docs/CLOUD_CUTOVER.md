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
APP_ENV=production
DATABASE_URL=postgresql://<datahub_user>:<password>@10.140.1.83:5432/vertu_data_hub
WATCHED_ROOT=/srv/vertu-agent/data/inbox
OSS_ACCESS_KEY_ID=<ram_access_key_id>
OSS_ACCESS_KEY_SECRET=<ram_access_key_secret>
OSS_ENDPOINT=<oss_endpoint>
OSS_BUCKET=<private_bucket>
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

Upload originals to the private OSS bucket under `raw/docs/`, `raw/images/`,
`raw/sales/`, or `quarantine/`, then run:

```bash
python -m app.cli.sync_oss --all
```

The worker incrementally mirrors changed objects into `WATCHED_ROOT`; originals
remain in OSS while chunks and vectors go to `vertu_data_hub`.

## Acceptance

- `vector` extension exists and has the expected version.
- Source rows and ingestion runs show expected counts.
- `doc_chunk` contains source text and 1024-dimensional vectors.
- Repeating the same sync does not duplicate records.
- A sample retrieval returns the expected source file.
- Backup and rollback point are recorded before bulk ingestion.

Use `docs/PILOT_RUNBOOK.md` for exact prefixes, acceptance, and rollback.
