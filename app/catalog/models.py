"""各 source_type 的 config（JSONB）校验模型。新增数据源时先用对应模型校验一遍再落库。"""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class FileSourceConfig(BaseModel):
    path: str  # 相对 settings.watched_root 的子目录，如 '政策产品文档'
    handler: Literal["doc_rag", "image", "tabular", "unclassified"]
    # doc_rag 用：默认标签，会与解析出的字段合并进 doc_chunk.tags
    default_tags: dict = Field(default_factory=dict)
    # tabular 用：写入哪个 dataset_code（structured_record.dataset_code）
    dataset_code: Optional[str] = None
    # 生产 OSS 原文件前缀；worker 下载后仍用同一 FileConnector 解析。
    oss_prefix: Optional[str] = None


class SkillSourceConfig(BaseModel):
    domain: str  # 如 'sales'
    shortcut: str  # 如 '+headline-kpi'（含前导 +）
    params: dict = Field(default_factory=dict)
    dataset_code: str  # structured_record.dataset_code，默认可用 shortcut 去掉 '+'
    summarize: bool = False  # 是否额外生成一段摘要文本 embedding 进 doc_chunk


class DbSourceConfig(BaseModel):
    dsn_env: str  # 存放真实连接串的环境变量名（不直接存密钥在 config 里）
    exposed_datasets: list[dict] = Field(default_factory=list)  # 见 structured_dataset 各字段


class McpSourceConfig(BaseModel):
    server_url: str
    tool: str


CONFIG_MODEL_BY_TYPE = {
    "file": FileSourceConfig,
    "skill": SkillSourceConfig,
    "db": DbSourceConfig,
    "mcp": McpSourceConfig,
}


def validate_config(source_type: str, config: dict) -> dict:
    model = CONFIG_MODEL_BY_TYPE.get(source_type)
    if model is None:
        raise ValueError(f"unknown source_type '{source_type}'")
    return model(**config).model_dump()
