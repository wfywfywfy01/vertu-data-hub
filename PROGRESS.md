# Progress

## 2026-08-19：里程碑 2A 数据核心

- 实现人工确认的经销商主表、Unicode/波斯语归一化别名和模糊查询。
- 实现 PDCA principal 到经销商的显式负责人关系。
- 实现私有 OSS 源对象元数据、SHA-256 去重、逻辑资产和不可变版本。
- 实现 PostgreSQL 权威处理任务状态、幂等键隔离和追加式审计。
- 修复 `python scripts/init_db.py` 从仓库根目录无法导入 `app` 的启动缺陷。
- 当前未实现 HTTP API、OSS 直传和 Celery worker；这些属于里程碑 2B。

### 验证

- `python -m pytest -q`：23 passed。
- `python -m compileall -q app scripts tests`：通过。
- `python scripts/init_db.py` 连续执行两次：通过。
- `docker compose config --quiet`：通过。

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
