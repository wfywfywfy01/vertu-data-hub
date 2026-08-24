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

Use a disposable database for schema and ingestion checks. Confirm extension,
table count, source count, and sample retrieval results before calling a sync
successful.

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
