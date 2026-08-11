# vertu-data-hub — 公司数据平台（数据层）说明

## 项目背景

公司数据散落在各处：政策/产品文档、门店陈列/装修图片、结构化销售数据（历史 Excel、未来还会有更多）、
通过内部 CLI `vertu-cli`（skill/vps-service）取到的各类业务指标、以及未来会陆续接入的其他数据库、
MCP 数据源。本项目的目标是把这些**杂七杂八的数据统一清洗、切片、入库**，让 AI 以后不管是走 RAG
向量检索、还是直接结构化查询，都能从同一个地方取数。

## 当前阶段的明确取舍（不要自行加回来）

- **只做数据存储/接入/检索层，不做 agent、不做 API、不做 FastAPI 服务**。agent 是这个库建好之后的事。
- **不实现 MCP connector**：当前没有已连接的 MCP server，只留占位和扩展点。
- **不做 HR 简历的表结构/接入**：范围未定，等后续单独决策再设计，不要现在猜。
- **Odoo 数据始终只走 `vertu-cli`（skill）取数，不直连 Odoo 的 Postgres**：`db` 类型 connector 的代码要写，
  但 Odoo 这个 `data_source` 行保持 `enabled=false`。直连数据库这个通道留给以后新接的、真正需要直连
  权限的其他库。
- **"其他-待定"文件夹里分不了类的文件不自动入库**，只记日志提醒人工后续分类处理。
- **不写常驻调度进程**：同步靠手动跑 `python -m app.cli.sync`，以后要定时就用 Windows 计划任务，
  不在本项目里实现调度服务。
- 与 `vertu-store-agent`（另一个独立项目，专服务俄伊拉三国经销商门店表单）是两套系统，不共享代码、
  不共享数据库连接，只是技术选型和部分代码模式（切片算法、embedding provider 抽象）互相借鉴。

## 技术栈（复用 vertu-store-agent 的既定约定，不重新选型）

| 组件 | 选型 |
|---|---|
| 数据库 | PostgreSQL ≥14 + pgvector。开发：本地 Docker `pgvector/pgvector:pg16`，宿主机端口 **5434**
（vertu-store-agent 已占用 5433）。正式：外部云 RDS `10.140.1.83`（pgvector 0.8.5 已验证可装），
独立数据库 `vertu_data_hub`，与 vertu-store-agent 的 `vertu` 库分开，各自最小权限账号 |
| 向量存储 | pgvector（不用 Qdrant） |
| 后端 | Python 3.10+，无 Web 框架（本项目不对外提供 HTTP 服务） |
| 文档解析 | Docling（PDF/DOCX/PPTX/HTML/XLSX），MD/TXT 直接读取 |
| 文本 Embedding | OpenAI 兼容 embedding API（如通义 text-embedding-v3）/ hash（开发兜底），统一 1024 维 |
| 图片 Embedding | 多模态 embedding API（如阿里云 multimodal-embedding）/ hash（开发兜底） |
| 表格数据 | openpyxl，不预设固定列名，整行转 JSONB |
| Skill 取数 | subprocess 调用 `vertu-cli <域> +<shortcut>`，解析 JSON |

## 核心设计原则

**新增一个数据源 = 往 `data_source` 表插一行，不需要改表结构、不需要写迁移。** 所有来源专属的标签
（country/doc_type/store_id/image_type 等）统一放进 `tags JSONB`，只有跨来源都会用来做范围过滤的
字段（日期类）才是真实列并建索引。

四种"AI 以后怎么调用"对应四种存储形态：

| 需求 | 存储/机制 |
|---|---|
| 找和 X 语义相似的文本 | `doc_chunk`（pgvector 向量检索） |
| 找和 X 相似的图片 | `media_asset`（pgvector 向量检索） |
| 某次批量数据/skill 快照的精确数值 | `structured_record`（JSONB 原样存，按 dataset_code+日期精确查，不走向量） |
| 外部活库的实时精确数值 | `structured_dataset` 目录项（`refresh_mode='live'`）+ 只读直连查询，不复制源库数据 |

数据库 DDL 见 `sql/schema.sql`，以它为准。

## 数据接入约定（Connector 模式）

一个 `Connector` 协议（`app/connectors/base.py`）+ 一个注册表（`app/connectors/registry.py` 的 `CONNECTOR_REGISTRY`），行为完全由
`data_source.config`（JSONB）驱动，**不要**在共享代码里写 `if source == ...` 特判。新增数据源的标准
流程：

1. 用 `python -m app.cli.register_source` 往 `data_source` 表插一行（code/source_type/config）
2. 跑 `python -m app.cli.sync --source <code>`（或 `--all`）

四种 `source_type`：
- `file`：`config.path` + `config.handler`（`doc_rag` / `image` / `tabular` / `unclassified`）
- `skill`：`config = {domain, shortcut, params}`，subprocess 跑 `vertu-cli`
- `db`：只读直连适配器，`sync()` 只登记/刷新 `structured_dataset` 目录，不复制数据
- `mcp`：占位，未实现

## 项目结构约定

```
vertu-data-hub/
├─ CLAUDE.md
├─ docker-compose.yml
├─ sql/schema.sql
├─ app/
│  ├─ config.py          # 环境变量配置
│  ├─ db.py               # 统一异步连接池
│  ├─ embeddings/         # 文本/图片 embedding provider 抽象
│  ├─ chunking.py          # 标题/表格感知切片算法
│  ├─ catalog/            # data_source 等的 CRUD + 配置校验
│  ├─ connectors/         # file/skill/db/mcp 四个接入器
│  ├─ ingestion/           # 文档入库（doc_chunk）
│  ├─ retrieval/           # 文本/图片/结构化检索封装
│  └─ cli/                # register_source.py / sync.py 命令行入口
├─ scripts/init_db.py     # 建 extension + 跑 schema.sql，幂等
└─ tests/
```

## 编码规范

- 全部配置走环境变量（.env），代码里不出现明文密码/密钥
- 数据库访问统一用 `app/db.py`，不要每个文件各自 connect
- 新增数据源不改表结构，只改 `data_source` 数据行 + 必要时新增一个 connector 脚本
- 所有 connector 的 `sync()` 必须幂等（重复跑不产生重复数据）
