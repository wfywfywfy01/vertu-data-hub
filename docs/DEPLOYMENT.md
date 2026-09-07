# Deployment

## Release rule

Deploy only a reviewed commit on `main`. Do not deploy a personal worktree or
an uncommitted directory.

## Preflight

- Confirm target PostgreSQL has `vector` extension and required privileges.
- Back up schema and data before DDL or bulk ingestion.
- Provide secrets through the deployment environment, never Git.
- Use the external Docker volume `dealer-knowledge-secrets` for `dealer-knowledge-jwt.key`;
  mount it read-only into PDCA and data-hub and never place it in either image.
- Deploy the exact `ghcr.io/wfywfywfy01/vertu-data-hub:<main-commit>` image published
  only after the `main` tests and container smoke test succeed. Overlay release
  workflows are retired: they did not include schema or dependency changes.
- Apply the additive `processing_job.run_token` migration before deploying the
  new API, worker, and scheduler together. Stop old workers before enabling the
  scheduler, because old workers do not participate in execution locking.
- The worker runs one task at a time, with a 15-minute hard task limit, bounded
  child recycling, and a 1800 MiB container memory limit. Size these limits from
  measured production inputs; oversized documents fail explicitly, never truncate.
- Run schema validation and a bounded sync in a controlled window.
- Record commit, migration result, source counts, retrieval smoke result, and
  rollback point in `PROGRESS.md` or the release record.

## Rollback

Application rollback uses the previous reviewed `main` commit. Data rollback
uses the database backup or an idempotent corrective sync. Schema changes must
be backward compatible before application rollout.
