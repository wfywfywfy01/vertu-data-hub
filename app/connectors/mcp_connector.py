"""MCP 数据源占位。当前没有已连接的 MCP server，不实现。

未来接入形状预期为：config = {server_url, tool}，sync() 调用对应 MCP tool 取数，
归一化后写入 doc_chunk（若为文本/文档类）或 structured_record（若为结构化返回）。
"""
from app.connectors.base import SyncResult


class McpConnector:
    async def sync(self, source: dict) -> SyncResult:
        raise NotImplementedError(
            "MCP connector 未实现：当前没有已连接的 MCP server，接入时再按具体 server/tool 设计"
        )
