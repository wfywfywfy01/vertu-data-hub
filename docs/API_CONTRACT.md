# PDCA 与知识库 API 契约

## 调用规则

- 基础路径：`/v1`；仅允许 PDCA 后端和运维服务通过私网访问。
- PDCA 服务令牌必须包含：`iss`、`aud`、`sub`、`user_id`、`role`、`scope`、`dealer_ids`、`iat`、`exp`、`jti`。
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
| `GET` | `/v1/assets/{asset_id}/content` | 获取脱敏预览或短期下载链接 |
| `POST` | `/v1/search` | 混合检索，返回片段和引用 |
| `POST` | `/v1/answers` | 生成有引用、默认脱敏的回答 |
| `GET` | `/v1/reviews` | 获取调用者有权处理的待确认项 |
| `POST` | `/v1/reviews/{review_id}/decision` | 提交分类、归属或主表确认 |
| `GET/POST` | `/v1/dealers` | 查询或建议经销商主表记录 |
| `PATCH` | `/v1/dealers/{dealer_id}` | 管理员确认正式名称、合并或停用 |
| `POST` | `/v1/exports` | 管理员创建受审计的敏感导出任务 |
| `GET` | `/v1/jobs/{job_id}` | 查询上传、处理或导出进度 |

## 权限

| 角色 | 默认范围 | 能力 |
|---|---|---|
| `sales` | 已分配经销商 | 上传、查询、提出主表修改、处理本人范围待确认 |
| `manager` | 明确配置的团队 | 查询团队、处理团队待确认 |
| `admin` | 全部 | 主表最终确认、合并、停用、敏感导出和审计 |
| `viewer/dealer` | 无或显式分配 | 仅脱敏读取；默认不开放上传和确认 |

服务端必须对路径对象、请求体中的 `dealer_id` 和检索结果分别校验，不能依赖前端隐藏按钮。

## 状态与分类

资产状态：`received`、`identifying`、`awaiting_review`、`processing`、`searchable`、`failed`、`deleted`。

- 置信度 `>= 0.90`：自动归类，法律资料除外。
- `0.70 <= confidence < 0.90`：进入人工确认。
- `< 0.70`：进入隔离区。
- 法律、授权、合同和合规资料首次出现时始终人工确认。

普通文档和图片目标 5 分钟内可检索；两小时以内视频目标 30 分钟内完成。失败任务保留原因和重试记录。

## 兼容性

`/v1` 内只做向后兼容变更。删除字段、收紧语义或修改状态含义需要新 API 版本和迁移窗口。

