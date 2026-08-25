# Changelog

## Unreleased

- Added private Qwen vision analysis for image descriptions and labels, reusing text Embedding for low-memory semantic image retrieval with fail-safe fallback.
- Replaced local Chinese-CLIP/PyTorch image retrieval with DashScope multimodal embeddings, strict sensitivity routing, API fallback, and idempotent backfill.
- Added fast lexical retrieval fallback when the embedding API is unavailable and a fixed-endpoint DeepSeek answer provider for restricted cloud networks.
- Added low-cardinality Prometheus HTTP metrics and verified atomic PostgreSQL backups with retention.
- Added scoped metadata-free image and redacted text previews plus reason-confirmed, admin-only original exports with append-only audits.
- Added local audio transcription, video keyframes, time-coded citations, media-derived artifacts, Celery/local ingestion routing, and model preloading.
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

- `python -m pytest -q`: 122 passed.
- Compilation, dependency, repeatable schema, development Compose, and production Compose checks passed.
- Real Qwen image acceptance remains pending recovery of the configured model upstream, which currently returns HTTP 502 after successful gateway authentication.
