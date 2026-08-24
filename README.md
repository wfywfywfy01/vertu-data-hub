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
python -m app.cli.preload_ocr             # 联网构建阶段预热 OCR 模型
python -m app.cli.preload_semantic_images # 联网构建阶段预热本地图文模型
python -m app.cli.preload_media            # 联网构建阶段预热本地语音模型
python run_api.py                         # 私有 API（默认 127.0.0.1:8080）
celery -A app.queue:celery_app worker -Q documents,images,videos --pool=solo  # Windows worker
```

API 需要 PDCA 服务令牌密钥。开发环境在 `.env` 设置至少 32 字节的
`SERVICE_TOKEN_SECRET`；生产环境只能挂载 `SERVICE_TOKEN_KEY_FILE`。Redis 本地启动：

```bash
docker compose up -d redis
```

正式查询页由 PDCA 提供：`/app/knowledge`。本仓库只提供私有 `/v1` 数据 API、
带水印图片预览、脱敏片段、相关度及原始文件引用。用户浏览器不直接访问本服务。

开发环境接口文档：`http://127.0.0.1:8080/docs`。当前已支持经销商主表、OSS
签名直传、上传完成校验、资产版本、Celery 路由，以及 PDF/DOCX/PPTX/XLSX/
CSV/TXT/Markdown 文档解析、分块、向量化和页码引用。图片与扫描件 OCR 已交付；
音频和视频 worker 已支持本地转写、视频关键帧、时间码引用和派生物。

图片 worker 支持 PNG/JPEG/WebP/HEIC 的真实格式校验、本地 OCR、检索前脱敏和
图片向量；扫描 PDF 在没有数字文本时自动 OCR。图片同时使用固定版本的本地
Chinese-CLIP 生成 512 维语义向量、画面标签和质量分，用于中文文字搜图。默认不向第三方发送原图。只有显式
设置 `ALLOW_EXTERNAL_IMAGE_PROCESSING=true`，且资料敏感级别为 `internal`，才会
调用云图片 embedding；`confidential/restricted` 始终本地处理。Arabic/Persian 和
Russian OCR 模型应在联网构建环境预热后再部署到离线生产 worker。

本地导入会自动补齐当前范围内尚未建立的图片语义索引。存量资料可显式执行：

```powershell
python -m app.cli.index_semantic_images --dealer "VMG Communication and Technology Joint Stock Company"
```

`POST /v1/search` 遇到图片分类或图片意图时自动切换到
本地图文检索，返回画面匹配度、质量分、标签、原图引用及待人工确认的社媒配文草稿；
没有语义索引时回退到原文字检索。默认在 API 启动时加载模型，模型缺失会阻止
readiness，避免把冷启动时间留给首个用户请求。

`POST /v1/search` 已提供经销商权限内的全文 + 向量混合检索。请求体示例：

```json
{"query":"Safiran Hamrah 当前库存", "dealer_id":"<dealer-uuid>", "top_k":5}
```

响应返回脱敏片段、RRF 分数和包含资产、版本、文件名、页码的引用。中文长问题在
严格全文检索无结果时启用受控双字词兜底，至少命中三个词，避免单词误命中。

`POST /v1/answers` 在同一权限范围内检索并生成有引用回答。默认关闭外部模型；启用
OpenRouter 时设置：

```dotenv
ANSWER_PROVIDER=openrouter
ALLOW_EXTERNAL_TEXT_GENERATION=true
OPENROUTER_API_KEY=<secret>
OPENROUTER_MODEL=openai/gpt-4.1-mini
```

服务只向 OpenRouter 官方 HTTPS API 发送脱敏查询和 `internal` 证据。无足够证据时
返回“无可靠证据”；命中 `confidential/restricted` 资料时不调用外部模型。模型引用
索引必须对应实际检索证据，否则整次回答失败。查询审计只保存 SHA-256。

## 新增一个数据源

1. 在 `app/cli/register_source.py` 的 `SOURCES` 列表加一项（或写一次性脚本调用
   `app.catalog.registry.upsert_data_source`），不需要改任何表结构或 connector 代码。
2. `python -m app.cli.sync --source <code>`。

四种 `source_type`（file/skill/db/mcp）的 config 格式见 `app/catalog/models.py`。

## 加新门店陈列图/政策文档

本地资料按所有权分为经销商、部门公用、公司公用三类：

```text
D:\vertu-agent-数据待处理\
├── VMG\
├── Safiran Hamrah\
├── _部门公用\海外销售部\
└── _公司公用\
```

经销商必须先存在于人工确认主表；目录内支持
PDF、DOCX、PPTX、XLSX、CSV、TXT、Markdown、JPG、PNG、WebP 和 HEIC：

```powershell
python -m app.cli.ingest_local `
  --dealer "Safiran Hamrah" `
  --path "D:\vertu-agent-数据待处理\Safiran Hamrah" `
  --category unclassified `
  --sensitivity confidential `
  --language fa
```

部门和公司资料分别使用：

```powershell
python -m app.cli.ingest_local `
  --department "overseas-sales" `
  --path "D:\vertu-agent-数据待处理\_部门公用\海外销售部" `
  --category unclassified `
  --sensitivity confidential `
  --language zh

python -m app.cli.ingest_local `
  --company `
  --path "D:\vertu-agent-数据待处理\_公司公用" `
  --category unclassified `
  --sensitivity internal `
  --language zh
```

`--department` 使用 PDCA 令牌中的稳定 `team_keys`，不要使用人员姓名。经销商、
部门和公司参数互斥，系统不会用假经销商承载共享资料。

命令把原文件复制到 `WATCHED_ROOT/.knowledge-objects` 托管区，登记资产和不可变
版本，然后同步复用现有文档/图片 worker 写入 `content_chunk`，不依赖 Redis 或
OSS。重复运行按文件路径和 SHA-256 跳过；同路径内容变化生成新版本。默认使用
`confidential`，因此资料不会发送给外部回答模型。混合资料可先用 `unclassified`，
整理后再按目录分别指定类别。MP4/MOV/MP3/M4A/WAV 会进入本地媒体流水线；
部署前必须预热 configured faster-whisper 模型。

`GET /v1/assets/{asset_id}/content` 只返回安全预览：图片缩到 1280、移除元数据并
加内部水印，文档与音视频返回再次脱敏的文字。原文件只能由管理员调用
`POST /v1/exports`，提交不少于 10 字的原因并确认 `export-original` 后流式导出；
预览和原件导出都写入追加式审计。

旧 `python -m app.cli.sync --all` 只维护继承的数据源目录和旧表，不用于新版问答。
生产试点把原文件放进私有 OSS 的
`raw/docs/`、`raw/images/`、`raw/sales/` 前缀，再运行
`python -m app.cli.sync_oss --all`。未变化文件会跳过，重复运行安全。

## 测试

```bash
pytest
```

生产监控使用私网 `/metrics`。每日数据库备份：

```bash
docker compose -f docker-compose.production.yml --profile ops run --rm backup
```

备份先写临时文件，使用 `pg_restore --list` 校验后再原子发布；每月至少恢复到临时库演练一次。

## 工程流程

本仓库与 `PDCA-agent` 是同一项目的两个独立服务。`PDCA-agent` 拥有页面、身份和业务流程，本仓库拥有知识数据与检索。旧 `vertu-store-agent` 仅作为待迁移代码来源，不是长期运行依赖。

生产代码只来自 `main`。每个需求使用独立 `codex/<task>` 分支和 worktree，经评审后合并。

Before a change, read `AGENTS.md`, `CLAUDE.md`, the relevant schema and tests.
Before review, run `pytest`, `python -m compileall -q app scripts tests`, and
`docker compose config --quiet` with non-production environment values.

Pilot deployment, OSS upload, acceptance, and rollback: `docs/PILOT_RUNBOOK.md`.
