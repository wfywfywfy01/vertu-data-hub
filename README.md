# dealer-knowledge-hub

公司级经销商知识底座：统一管理经销商主表、文档、图片、音视频和结构化资料，完成版本化、ETL、脱敏、检索与审计。PDCA 工作台通过私有 API 使用本服务；浏览器不直连。

目标架构见 `docs/ARCHITECTURE.md`，领域词汇见 `CONTEXT.md`，分阶段交付见 `docs/IMPLEMENTATION_PLAN.md`。现有 CLI 是继承的早期数据底座，尚未代表全部目标能力。

## 本地启动

```bash
docker compose up -d                     # 起本地 pgvector（端口 5434）
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env                   # 按需改 embedding key 等
python scripts/init_db.py                # 建表（幂等）
python -m app.cli.register_source        # 注册初始数据源（幂等）
python -m app.cli.sync --all             # 跑一遍同步
python -m app.cli.preload_ocr             # 联网构建阶段预热 OCR 模型
python run_api.py                         # 私有 API（默认 127.0.0.1:8080）
celery -A app.queue:celery_app worker -Q documents,images --pool=solo  # Windows worker
```

API 需要 PDCA 服务令牌密钥。开发环境在 `.env` 设置至少 32 字节的
`SERVICE_TOKEN_SECRET`；生产环境只能挂载 `SERVICE_TOKEN_KEY_FILE`。Redis 本地启动：

```bash
docker compose up -d redis
```

开发环境接口文档：`http://127.0.0.1:8080/docs`。当前已支持经销商主表、OSS
签名直传、上传完成校验、资产版本、Celery 路由，以及 PDF/DOCX/PPTX/XLSX/
CSV/TXT/Markdown 文档解析、分块、向量化和页码引用。图片与扫描件 OCR 已交付；
音视频 worker 尚未交付。

图片 worker 支持 PNG/JPEG/WebP/HEIC 的真实格式校验、本地 OCR、检索前脱敏和
图片向量；扫描 PDF 在没有数字文本时自动 OCR。默认不向第三方发送原图。只有显式
设置 `ALLOW_EXTERNAL_IMAGE_PROCESSING=true`，且资料敏感级别为 `internal`，才会
调用云图片 embedding；`confidential/restricted` 始终本地处理。Arabic/Persian 和
Russian OCR 模型应在联网构建环境预热后再部署到离线生产 worker。

## 新增一个数据源

1. 在 `app/cli/register_source.py` 的 `SOURCES` 列表加一项（或写一次性脚本调用
   `app.catalog.registry.upsert_data_source`），不需要改任何表结构或 connector 代码。
2. `python -m app.cli.sync --source <code>`。

四种 `source_type`（file/skill/db/mcp）的 config 格式见 `app/catalog/models.py`。

## 加新门店陈列图/政策文档

本地开发可把文件放进 `WATCHED_ROOT` 对应子目录，再运行
`python -m app.cli.sync --all`。生产试点把原文件放进私有 OSS 的
`raw/docs/`、`raw/images/`、`raw/sales/` 前缀，再运行
`python -m app.cli.sync_oss --all`。未变化文件会跳过，重复运行安全。

## 测试

```bash
pytest
```

## 工程流程

本仓库与 `PDCA-agent` 是同一项目的两个独立服务。`PDCA-agent` 拥有页面、身份和业务流程，本仓库拥有知识数据与检索。旧 `vertu-store-agent` 仅作为待迁移代码来源，不是长期运行依赖。

生产代码只来自 `main`。每个需求使用独立 `codex/<task>` 分支和 worktree，经评审后合并。

Before a change, read `AGENTS.md`, `CLAUDE.md`, the relevant schema and tests.
Before review, run `pytest`, `python -m compileall -q app scripts tests`, and
`docker compose config --quiet` with non-production environment values.

Pilot deployment, OSS upload, acceptance, and rollback: `docs/PILOT_RUNBOOK.md`.
