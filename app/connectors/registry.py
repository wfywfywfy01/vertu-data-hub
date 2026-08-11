"""Connector 注册表：source_type → 具体实现类。

新增一种 source_type 才需要改这里；新增一个具体数据源（如第 3 个 skill 取数）
只需要 `python -m app.cli.register_source`，不改任何代码。
"""
from app.connectors.db_connector import DbConnector
from app.connectors.file_connector import FileConnector
from app.connectors.mcp_connector import McpConnector
from app.connectors.skill_connector import SkillConnector

CONNECTOR_REGISTRY: dict[str, type] = {
    "file": FileConnector,
    "skill": SkillConnector,
    "db": DbConnector,
    "mcp": McpConnector,
}
