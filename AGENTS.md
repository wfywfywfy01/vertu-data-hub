# Engineering Rules

## Repository boundary

This repository owns the dealer knowledge foundation: dealer master data,
private object metadata, asset versions, processing jobs, ingestion, chunking,
embeddings, retrieval, redaction, and audit.

`PDCA-agent` owns identity, roles, pages, and workflows. Coordinate only through
the private API contract in `docs/API_CONTRACT.md`; never share tables or import
application code across repositories. `vertu-store-agent` is a migration source,
not a runtime dependency.

## Change workflow

- `main` is the production baseline. Do not develop directly on `main`.
- Start each task from the latest `main` in a dedicated Git worktree.
- Read `README.md`, `CLAUDE.md`, relevant schema, code, and tests before editing.
- Keep changes narrow. Add tests for ingestion, parsing, retrieval, and data
  boundary behavior.
- Run tests, compile checks, and Docker Compose validation before review.
- Never commit `.env`, API keys, database dumps, virtual environments, logs, or
  generated media.
- Production deploys use an explicit reviewed commit from `main` only.

## Data rules

- Structured values remain queryable JSONB or relational data; do not force them
  into vector search.
- Ingestion is idempotent and records source metadata and content hashes.
- Retrieval is read-only for agents. Business writes happen through the owning
  application or an explicit ingestion command.
- Do not enable a source or run a production sync without checking credentials,
  scope, and expected row counts.
