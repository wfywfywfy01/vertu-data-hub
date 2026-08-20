# Changelog

## Unreleased

- Added synchronous local dealer-file ingestion into the versioned searchable pipeline with managed originals and idempotent retries.
- Added evidence-gated OpenRouter answers with validated citations, sensitivity blocking, output redaction, and hash-only audits.
- Added dealer-scoped hybrid full-text/vector search with RRF, cited redacted results, and hash-only query auditing.
- Added local multilingual image OCR, scanned-PDF fallback, HEIC support, image vectors, retrieval-layer redaction, and fail-closed external image processing.
- Added document workers for verified OSS download, Docling/PDFium extraction, cited chunks, embeddings, derived Markdown, and retry-aware job status.
- Added the private FastAPI service, short-lived PDCA JWT authorization, OSS direct-upload verification, and Celery routing with durable dispatch status.
- Added a psycopg-compatible Windows API launcher and Redis Compose service.
- Added dealer master records, multilingual alias search, explicit ownership, immutable asset versions, processing jobs, and append-only audit events.
- Fixed direct execution of `scripts/init_db.py` from the repository root.
- Reframed the service as the enterprise dealer knowledge hub behind PDCA.
- Added domain language, system ADRs, API and data contracts, and the five-stage delivery plan.
- Established enterprise Git, worktree, testing, and deployment governance.
- Added cloud cutover, remote database, worker inbox, and acceptance runbook.
- Added private OSS incremental mirroring and one-command ingestion.
- Added fail-closed production configuration and failed-sync exit handling.
