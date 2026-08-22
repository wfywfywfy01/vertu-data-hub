# PDCA 与知识库 API 契约

## 调用规则

- 基础路径：`/v1`；仅允许 PDCA 后端和运维服务通过私网访问。
- PDCA 服务令牌必须包含：`iss`、`aud`、`sub`、`user_id`、`role`、`scope`、`dealer_ids`、`iat`、`exp`、`jti`；部门成员增加可选 `team_keys`。
- 令牌有效期不超过 5 分钟。知识库校验签名、受众、过期时间，并把 `dealer_ids` 作为上限再次授权。
- 每个写请求携带 `Idempotency-Key`，每个请求携带 `X-Request-ID`。
- 列表使用不透明 cursor；错误返回稳定 `code`、安全 `message` 和 `request_id`，不得返回密钥、SQL 或原始敏感内容。

## 核心接口

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/v1/uploads/presign` | 校验范围后生成私有 OSS 上传凭证 |
| `POST` | `/v1/assets/complete` | 登记已上传对象并创建识别任务 |
| `GET` | `/v1/assets` | 按经销商、类别、状态和时间筛选资料 |
| `GET` | `/v1/assets/{asset_id}` | 读取资产、版本和处理状态 |
| `GET` | `/v1/assets/{asset_id}/content` | 获取脱敏文字或带水印图片预览 |
| `POST` | `/v1/search` | 混合检索，返回片段和引用 |
| `POST` | `/v1/answers` | 生成有引用、默认脱敏的回答 |
| `GET` | `/v1/reviews` | 获取调用者有权处理的待确认项 |
| `POST` | `/v1/reviews/{review_id}/decision` | 提交分类、归属或主表确认 |
| `GET/POST` | `/v1/dealers` | 查询或建议经销商主表记录 |
| `PATCH` | `/v1/dealers/{dealer_id}` | 管理员确认正式名称、合并或停用 |
| `POST` | `/v1/exports` | 管理员确认原因后流式导出原件 |
| `GET` | `/v1/jobs/{job_id}` | 查询上传、处理或导出进度 |

上传请求使用 `scope_type=dealer|department|company`。经销商范围传 `dealer_id`；
部门范围传稳定 `scope_key`；公司范围固定为 `vertu`。普通销售只能写授权经销商，
部门公用资料仅匹配 `team_keys` 的经理或管理员可写，公司公用资料仅管理员可写。

### 预览与原件导出

`GET /v1/assets/{asset_id}/content` 对所有角色重新执行资料范围授权。图片预览限制
最长边 1280、移除 EXIF 并写入内部水印；其他资料只返回 `content_chunk` 中再次
脱敏的文字，响应禁止缓存。原文件不通过该接口返回。

`POST /v1/exports` 仅允许 `admin`，请求必须包含 `asset_id`、至少 10 字的 `reason`
和 `confirmation=export-original`。服务在范围授权和源对象存在性校验后流式返回原件，
并记录脱敏原因、原因哈希、版本、敏感级别和字节数。非管理员即使知道资产 ID 也返回 403。

### 混合检索

`POST /v1/search` 接受 `query`、可选 `dealer_id`、可选 `category` 和 `top_k`
（1-20）。服务端在全文和向量两路 SQL 中分别强制授权经销商、部门、公司范围、当前资产版本和
`searchable` 状态，再用 RRF 融合。响应片段及标题、文件名会再次脱敏；引用包含
资产 ID、版本 ID/编号和页码。审计只保存查询 SHA-256、命中数和资产 ID，不保存
原始查询。

图片分类或查询包含图片、照片、合影、社媒等视觉意图时，同一接口改用本地
Chinese-CLIP 图文向量检索。授权范围、当前版本、`searchable` 状态和经销商过滤
与文字检索一致。图片结果增加 `retrieval_kind=image_semantic`、
`semantic_similarity`、`quality_score`、`semantic_labels` 和
`suggested_caption`；配文只作为发布前人工确认的草稿。模型在私有 worker 内运行，
原图不发送给 OpenRouter 或外部图片 API。没有图片语义索引时自动回退文字检索。

音视频转写和视频关键帧描述进入同一个 `content_chunk` 检索面；引用增加
`timestamp_start` 与 `timestamp_end`。转写使用私有 worker 内的 faster-whisper，
关键帧使用本地 Chinese-CLIP 标注，原始音视频不外发。

### 有引用回答

`POST /v1/answers` 接受与检索相同的 `query`、可选 `dealer_id`、可选 `category`
和 `top_k`（1-10）。服务先执行权限内混合检索，再按以下规则处理：

- 无足够词法或语义证据：返回 `insufficient_evidence` 和“无可靠证据”，不调用模型。
- 任一候选证据为 `confidential/restricted`：返回 `sensitive_evidence_blocked`，不调用外部模型。
- 只把已脱敏查询和 `internal` 证据发送给 OpenRouter；API 地址固定为官方 HTTPS 地址。
- 模型必须返回严格 JSON 和一基引用索引；索引越界时整次回答失败。
- 最终回答再次脱敏，只返回模型实际引用且已校验的资产、版本、文件和页码。
- `knowledge.answer` 审计只保存查询 SHA-256、状态、模型、Token 用量和引用资产 ID。

成功响应示例：

```json
{
  "status": "answered",
  "answer": "Safiran Hamrah 当前库存为 12 台。",
  "citations": [{"asset_id": "...", "title": "库存周报", "page_start": 2}],
  "model": "openai/gpt-4.1-mini",
  "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
  "evidence_count": 3
}
```

## 权限

| 角色 | 默认范围 | 能力 |
|---|---|---|
| `sales` | 已分配经销商 | 上传、查询、提出主表修改、处理本人范围待确认 |
| `manager` | 明确配置的团队 | 查询团队、处理团队待确认 |
| `admin` | 全部 | 主表最终确认、合并、停用、敏感导出和审计 |
| `viewer/dealer` | 无或显式分配 | 仅脱敏读取；默认不开放上传和确认 |

服务端必须对路径对象、请求体中的资料范围和检索结果分别校验，不能依赖前端隐藏按钮。
查询自动合并授权经销商资料、调用者 `team_keys` 对应部门资料和公司公用资料。

## 状态与分类

资产状态：`received`、`identifying`、`awaiting_review`、`processing`、`searchable`、`failed`、`deleted`。

- 置信度 `>= 0.90`：自动归类，法律资料除外。
- `0.70 <= confidence < 0.90`：进入人工确认。
- `< 0.70`：进入隔离区。
- 法律、授权、合同和合规资料首次出现时始终人工确认。

普通文档和图片目标 5 分钟内可检索；两小时以内视频目标 30 分钟内完成。失败任务保留原因和重试记录。

## 兼容性

`/v1` 内只做向后兼容变更。删除字段、收紧语义或修改状态含义需要新 API 版本和迁移窗口。

