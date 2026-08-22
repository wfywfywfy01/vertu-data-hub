# Changelog

## Unreleased

- Added pinned local Chinese-CLIP text-to-image retrieval, automatic image indexing, quality-aware ranking, visual labels, social caption drafts, scoped API routing, and human UI results.
- Added a loopback-only pilot query UI with dealer/category filters, managed image previews, redacted cited results, responsive layouts, and production fail-closed access.
- Added dealer, department, and company knowledge scopes with scoped ingestion, storage, API authorization, retrieval, and backward-compatible migration.
- Added guarded Chinese bigram fallback retrieval for compound dealer questions when strict full-text search returns no rows.
- Fixed valid ISO dates being misclassified as phone numbers during redaction.
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

### Verification

- `python -m pytest -q`: 84 passed.
- VMG: 52/52 images indexed; social-media query top four are `image12.png`, `image14.png`, `image09.png`, and `image08.png`.
- Playwright desktop and 390px mobile: no horizontal overflow, 8/8 images loaded, zero console errors.
