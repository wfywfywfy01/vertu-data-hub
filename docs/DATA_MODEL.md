# 目标数据模型

## 主表与权限边界

| 表 | 关键内容 |
|---|---|
| `dealer` | 稳定 ID、正式名称、国家、城市、语言、状态、确认信息 |
| `dealer_alias` | 别名、标准化值、来源、有效状态；用于模糊匹配 |
| `dealer_contact` | 加密联系人字段、类型和有效期 |
| `store` | 门店 ID、经销商 ID、名称、地址、状态 |
| `dealer_owner` | 经销商与 PDCA 用户/团队的显式负责人关系及有效期 |

经销商合并保留旧 ID 的重定向记录。销售可提交建议，只有管理员可确认正式名称、合并或停用。

## 资产与版本

| 表 | 关键内容 |
|---|---|
| `source_object` | OSS bucket/key、内容哈希、大小、媒体类型、上传者 |
| `knowledge_asset` | 范围类型/键、可选经销商/门店、类别、敏感等级、生命周期状态 |
| `asset_version` | 不可变版本号、源对象、前一版本、语言、有效时间 |
| `derived_artifact` | 文本、OCR、转写、关键帧或摘要的位置与流水线版本 |
| `content_chunk` | 可引用文本、页码/时间戳、全文索引字段 |
| `embedding` | chunk/asset、provider、model、dimension、pipeline version、向量 |
| `image_embedding` | 图片向量、尺寸、真实格式、OCR 语言/行数/置信度和流水线版本 |

`source_object.content_hash` 用于完全去重；逻辑版本由人工选择或规则识别，不因同名文件自动覆盖。
`scope_type + scope_key` 是资料所有权边界：经销商键为 UUID，部门键为 PDCA
稳定 `team_key`，公司键固定为 `vertu`。共享资料的 `dealer_id` 必须为空。

## 处理、确认和审计

| 表 | 关键内容 |
|---|---|
| `processing_job` | 类型、状态、进度、幂等键、重试次数、错误、输入输出 |
| `classification_decision` | 模型建议、置信度、最终结论、决定者 |
| `review_case` | 原因、优先级、负责人、截止时间、结果 |
| `retrieval_audit` | 查询者、范围、查询摘要、命中文档、回答与引用 |
| `sensitive_export` | 重新认证、原因、范围、水印、过期时间、下载审计 |
| `audit_event` | 追加式主体、动作、对象、结果、请求 ID 和时间 |

审计事件不允许业务 API 更新或删除。一般登录、查询、上传、分类、下载和 AI 记录保留 1 年；敏感导出、主表修改和删除记录保留 3 年。

## 数据库规则

- 生产数据库名 `dealer_knowledge`，与 `pdca_workbench` 使用不同账号和 schema 权限。
- 所有业务表使用 UUID、UTC 时间、创建者和更新时间；可变记录使用乐观锁版本号。
- 向量索引必须与 `provider + model + dimension + pipeline_version` 绑定。重建完成并通过评测前不替换旧索引。
- 删除资产只改变生命周期状态；物理清理由保留策略和受审计任务执行。

