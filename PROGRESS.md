# Progress

## 2026-08-21：里程碑 4F 本机试点查询页

- 新增 `/ui` 经销商知识查询页，支持经销商、资料分类、自然语言问题和引用结果。
- 页面展示可检索资产数、脱敏片段、词法/语义相关度、文件名、版本、分类和敏感级别。
- 图片结果展示延迟加载缩略图；点击后通过受管资产接口查看原图，不开放任意文件路径。
- 本机页面不持有服务令牌，只在 `development` 且 loopback 请求下开放；生产环境关闭。
- 完成 1440×900 和 390×844 浏览器验收，无横向溢出、控制台错误或警告。

### 验证

- `python -m pytest -q`：80 passed。
- 真实 VMG 数据加载 52 项可检索资产；键盘提交 `VERTU Vietnam` 返回带引用关键词结果。
- 真实查询加载 8 张缩略图；首张原图接口返回 JPEG `3536×2431`，桌面与手机均通过。
- 编译、Git diff 检查和 Docker Compose 配置检查通过。

## 2026-08-21：里程碑 4E 部门与公司公用资料

- 新增 `dealer / department / company` 三种明确资料范围，禁止使用假经销商。
- 现有经销商数据幂等迁移；共享资料使用独立对象前缀且保留原版本、ETL、引用和审计链。
- 本地导入支持 `--department` 与 `--company`；API 令牌支持可选 `team_keys`。
- 查询自动合并授权经销商、所属部门和公司公用资料；部门之间保持隔离。

### 验证

- `python -m pytest -q`：74 passed。
- 现有试点库迁移通过；临时空库连续建库两次通过。
- 部门和公司 Markdown 实际导入、处理、检索通过。
- 部门 PDF API 登记、经理授权、跨部门拒绝和普通销售拒绝通过。

## 2026-08-20：里程碑 4D 中文复合问题召回

- 保留 PostgreSQL 严格全文检索；无结果时按英文词和中文双字词执行经销商范围内兜底。
- 复合问题至少命中三个词才形成词法证据，继续限制为当前、可检索版本。
- Safiran Hamrah 真实长问已命中当前资料版本并返回引用；机密证据仍禁止外发。

### 验证

- `python -m pytest -q`：71 passed。
- 编译、Compose 配置、依赖检查和两次幂等建库通过。
- 真实问题“库存由谁录入，多久更新一次，哪天更新”返回 1 条版本 2 引用。

## 2026-08-20：里程碑 4C 本地资料直入新版知识库

- 新增 `python -m app.cli.ingest_local`，按经销商、类别和敏感级别批量导入本地文件。
- 原文件复制到受控本地对象区，执行路径穿越、扩展名、MIME、大小和 SHA-256 校验。
- 文档和图片同步复用现有新版 worker，不依赖 Redis/OSS，成功后进入 `content_chunk`。
- 默认 `confidential + unclassified`；重复导入跳过，同路径内容变化生成不可变新版本。
- 音视频仍未实现，命令明确拒绝，不生成无法处理的排队任务。

### 验证

- `python -m pytest -q`：70 passed。
- Markdown 真实导入：资产、托管原件、派生物、chunk 和检索引用通过。
- 同文件二次导入 0 新版本；内容变化生成版本 2，只有一个当前版本。
- Windows 长路径、托管区路径穿越和不支持文件类型测试通过。
- 真实库存在 Safiran 负责人记录时测试仍隔离；ISO 日期不会误脱敏为电话号码。

## 2026-08-19：里程碑 4B 有引用回答

- 实现 `POST /v1/answers`，复用经销商权限内混合检索和引用。
- OpenRouter 使用严格 JSON Schema 输出；模型引用索引必须映射到实际证据。
- 无可靠证据时拒答；`confidential/restricted` 证据禁止发送外部模型。
- 查询、证据和最终回答执行脱敏；OpenRouter 地址限制为官方 HTTPS API。
- 追加 `knowledge.answer` 哈希审计，记录状态、模型、Token 用量和引用资产，不保存原始问答。
- OpenRouter 真实 API 冒烟取决于部署环境密钥；50 问业务 RAG 回归集尚未建立。

### 验证

- `python -m pytest -q`：65 passed。
- 真实 PostgreSQL/pgvector：空权限拒答、越权 403、哈希审计通过。
- OpenRouter MockTransport：官方端点、严格结构化输出、引用映射和 Token 用量过滤通过。

## 2026-08-19：里程碑 4A 权限内混合检索

- 实现 `POST /v1/search`，支持 query、经销商、类别和 Top-K 过滤。
- 全文与向量两路 SQL 均强制调用者经销商范围、当前版本和 `searchable` 状态。
- 使用 RRF 融合候选，返回脱敏片段、语义/词法分数和资产/版本/页码引用。
- 查询发往 embedding 前脱敏；响应再次脱敏，兼容尚未重跑的旧流水线数据。
- 追加 `knowledge.search` 审计，仅保存查询 SHA-256、命中数和资产 ID。
- 自然语言回答、重排模型和 50 问 RAG 回归集尚未实现。

### 验证

- `python -m pytest -q`：55 passed。
- 真实 PostgreSQL/pgvector API：范围内命中、范围外 0 命中、显式越权 403。
- 引用文件/版本/页码、RRF 双路融合、查询脱敏和哈希审计通过。

## 2026-08-19：里程碑 3B 图片、扫描件 OCR 与脱敏

- 实现 PNG/JPEG/WebP/HEIC 解码、像素上限和扩展名/真实格式一致性校验。
- 实现本地 RapidOCR，支持默认中英文、Arabic/Persian 和 Russian 模型路由。
- 实现扫描 PDF 无数字文本时按页 OCR，保留页码引用并限制最多 200 页。
- 实现图片向量、OCR 分块、派生 Markdown、处理状态和 Celery `images` 路由。
- 文档与 OCR 内容进入检索和派生预览前统一遮蔽邮箱、国际/本地电话号码。
- 脱敏文档流水线升级为 `document-v2`，使用新派生对象 key，避免混用旧向量。
- 默认禁止原图外发；只有显式授权的 `internal` 资料可调用云图片 embedding。

### 验证

- `python -m pytest -q`：49 passed。
- 真实 PNG、HEIF 和纯图片 PDF：OCR 通过，扫描 PDF 页码引用通过。
- Persian/Arabic OCR 模型：本机 ONNX 推理通过。
- 音视频 worker、云 OSS/Redis 端到端和生产镜像模型预热仍未完成。

## 2026-08-19：里程碑 3A 文档处理 worker

- 实现源文件下载、大小与 SHA-256 完整性校验、派生 Markdown 上传。
- 实现 PDF/DOCX/PPTX/XLSX/CSV/TXT/Markdown 解析、分块、向量化和页码引用。
- PostgreSQL 保存派生物、内容分块、embedding 身份和处理流水线版本。
- 实现 Celery 文档任务、最多 3 次尝试、永久错误与可重试错误分类。
- PDF 在 Docling 不可用时回退到 PDFium 按页提取数字文本。
- 图片、音视频和扫描件 OCR 仍未实现。

### 验证

- `python -m pytest -q`：38 passed。
- PDF 实文件：19 chunks、18,821 chars、页码引用通过。
- DOCX/XLSX 实文件：解析通过。
- `python -m compileall -q app scripts tests`：通过。
- `python scripts/init_db.py` 连续执行两次：通过。
- `docker compose config --quiet`：通过。

## 2026-08-19：里程碑 2B 私有 API 与任务路由

- 实现最长 5 分钟的 PDCA HS256 服务令牌校验、角色校验和经销商范围二次授权。
- 实现经销商、资产、任务、OSS 签名直传和上传完成 API。
- 完成文件类型/大小、OSS key 前缀、对象大小、MIME 和 SHA-256 元数据校验。
- 实现 Celery 队列路由及 PostgreSQL `pending/sent/failed` 权威投递状态。
- 实现投递失败后使用同一幂等键重试，保证资料已保存且不重复生成版本。
- 增加 Redis Compose 服务、API readiness 检查和 Windows Selector loop 启动入口。
- 多模态 worker 尚未实现；任务当前只完成可靠入队。

### 验证

- `python -m pytest -q`：31 passed。
- Uvicorn 真实进程：`GET /health/ready` 200，JWT `GET /v1/dealers` 200。
- `python -m compileall -q app scripts tests`：通过。
- `python scripts/init_db.py` 连续执行两次：通过。
- `docker compose config --quiet`：通过。
- 真实 Redis 冒烟受本机 Docker Hub 凭证错误阻塞；Celery 路由已通过适配器和 API 故障重试测试。

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
