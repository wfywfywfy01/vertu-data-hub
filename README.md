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
```

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
