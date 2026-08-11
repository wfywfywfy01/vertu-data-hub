# Deployment

## Release rule

Deploy only a reviewed commit on `main`. Do not deploy a personal worktree or
an uncommitted directory.

## Preflight

- Confirm target PostgreSQL has `vector` extension and required privileges.
- Back up schema and data before DDL or bulk ingestion.
- Provide secrets through the deployment environment, never Git.
- Run schema validation and a bounded sync in a controlled window.
- Record commit, migration result, source counts, retrieval smoke result, and
  rollback point in `PROGRESS.md` or the release record.

## Rollback

Application rollback uses the previous reviewed `main` commit. Data rollback
uses the database backup or an idempotent corrective sync. Schema changes must
be backward compatible before application rollout.
