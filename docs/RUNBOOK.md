# Runbook

## Start a task

```powershell
git switch main
git pull --ff-only
git worktree add -b codex/<task> ..\..\cdoeX-worktrees\<task> main
```

Work only inside the new worktree. Never use `.env` values in logs or commits.

## Local checks

```powershell
pytest
python -m compileall -q app scripts tests
docker compose config --quiet
```

For API and queue changes also run:

```powershell
docker compose up -d redis
python run_api.py
```

Check `/health/ready`, then call one authenticated `/v1/dealers` request. A
successful upload must have a PostgreSQL `processing_job` row with
`dispatch_status='sent'`; Redis queue depth alone is never success evidence.

Before a production cutover, run the external provider probe inside the
candidate container with the production environment:

```powershell
python -m app.cli.check_providers
```

Both text and image providers must report `dim=1024`. A failed provider probe
blocks the cutover even when `/health/ready` passes.

Use a disposable database for schema and ingestion checks. Confirm extension,
table count, source count, and sample retrieval results before calling a sync
successful.

## Processing recovery

Run exactly one scheduler alongside the worker. Every minute it redelivers up
to 100 pending jobs whose last dispatch is older than ten minutes. PostgreSQL
session locks exclude live executions; a fenced run token prevents superseded
workers from publishing chunks. Interrupted jobs exhaust their existing retry
budget rather than retrying forever. Failed inputs remain stored for review.

Check `/health/ingestion` as well as `/health/ready`. Ingestion health returns
503 if no worker answers, the oldest pending job exceeds one hour, or its
dependencies are unavailable. Large imports may be degraded while progressing;
compare counts over time. Database readiness alone is not ETL health.

PDF extraction uses PDFium text first and bounded OCR only on blank pages.
The limit is 200 pages, 2000 pixels per OCR edge, and two million extracted
characters. Split oversized documents at source; no automatic truncation.

## Metrics and backup

- Scrape the loopback-only `/metrics` endpoint. Alert on readiness failure, HTTP 5xx, and sustained latency growth.
- Run `docker compose -f docker-compose.production.yml --profile ops run --rm backup` daily.
- Backups are custom-format PostgreSQL archives, written atomically, checked with `pg_restore --list`, permissioned `0600`, and retained for 14 runs by default.
- Perform a real restore into a disposable PostgreSQL database at least monthly. An archive-list check does not replace a restore drill.
- Keep `DATA_HUB_BACKUP_DIR_HOST` outside the repository and copy backups to a second encrypted storage location.

## Incident response

1. Stop the failing sync or deployment.
2. Record commit, source code, time window, and affected row counts.
3. Preserve failed input and logs outside Git.
4. Restore from the last known-good database backup or rerun an idempotent sync
   after correcting the source.
5. Add a regression test and update `PROGRESS.md`.
