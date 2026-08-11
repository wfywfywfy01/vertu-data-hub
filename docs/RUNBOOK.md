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

Use a disposable database for schema and ingestion checks. Confirm extension,
table count, source count, and sample retrieval results before calling a sync
successful.

## Incident response

1. Stop the failing sync or deployment.
2. Record commit, source code, time window, and affected row counts.
3. Preserve failed input and logs outside Git.
4. Restore from the last known-good database backup or rerun an idempotent sync
   after correcting the source.
5. Add a regression test and update `PROGRESS.md`.
