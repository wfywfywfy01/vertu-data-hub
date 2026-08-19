# Progress

## 2026-08-19：企业知识库架构基线

- 确认 PDCA 与知识库使用私有 API 边界和独立数据库账号。
- 建立经销商主表、资产版本、异步任务、权限、脱敏和审计目标模型。
- 固化私有 OSS 前缀、Redis/Celery 职责、API 契约和五阶段实施计划。
- 当前仅完成架构基线；新数据模型、API、worker 和 PDCA 页面尚未实现，不得标记为生产可用。

## 2026-08-19

- Added private OSS inbox mirroring for documents, images, sales files, and the
  quarantine prefix.
- Preserved nested source paths and linked first-ingest chunks/assets to their
  source item.
- Added production configuration validation and non-zero sync failure exits.
- Added controlled pilot upload, acceptance, and rollback runbook.

## Verification

- `python -m pytest -q`: 16 passed against isolated `vertu_data_hub_test`.
- `python -m compileall -q app scripts tests`: passed.
- `git diff --check`: passed.
- Local pgvector test container healthy on port 5434.
- RDS credentials, cloud `vector`, OSS credentials, and real embedding calls
  remain target-environment acceptance items.

## 2026-08-11

- Inherited Claude-built data foundation and RAG support.
- Added enterprise repository rules and release documents.
- Git baseline and worktree setup are being established.
- Functional verification remains required before production use.

## Previous verification

- `python -m pytest -q`: 12 passed.
- `python -m compileall -q app scripts tests`: passed.
- `docker compose config --quiet`: passed.
- Local pgvector container healthy on port 5434; schema fixture applied.

## Cloud cutover status

- RDS endpoint `10.140.1.83:5432` is network reachable.
- Production database credentials and `vector` extension are not verified in
  this workstation environment.
- Cloud cutover is blocked until those two checks pass.

## Acceptance evidence

Record command, commit, result, and environment here after each milestone.
Do not mark a production milestone complete from unit tests alone; verify the
database extension, ingestion, retrieval, and rollback path in the target
environment.
