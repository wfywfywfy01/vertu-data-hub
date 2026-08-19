# vertu-data-hub

公司数据平台的数据层：把政策文档、门店图片、结构化销售数据、`vertu-cli` skill 取数等统一清洗/切片/入库，
供以后的 agent 通过 RAG 向量检索或精确结构化查询调用。范围/取舍见 `CLAUDE.md`。

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

## Engineering workflow

This repository is one half of the Vertu Store Agent project. The companion
repository is `vertu-store-agent`; it owns forms and agent APIs. Keep production
code on `main`, create one `codex/<task>` branch per task in a dedicated worktree,
and deploy only reviewed commits from `main`.

Before a change, read `AGENTS.md`, `CLAUDE.md`, the relevant schema and tests.
Before review, run `pytest`, `python -m compileall -q app scripts tests`, and
`docker compose config --quiet` with non-production environment values.

Pilot deployment, OSS upload, acceptance, and rollback: `docs/PILOT_RUNBOOK.md`.
