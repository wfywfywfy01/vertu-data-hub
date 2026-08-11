"""Connector 协议。具体实现（file/skill/db/mcp）各自 import 本模块的 SyncResult，
本模块不反过来 import 它们——注册表在 app/connectors/registry.py，避免循环 import。
"""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SyncResult:
    items_processed: int = 0
    errors: list[str] = field(default_factory=list)


class Connector(Protocol):
    async def sync(self, source: dict) -> SyncResult:
        """source 是 data_source 表的一行（dict），source['config'] 已是解析好的 JSONB dict。"""
        ...
