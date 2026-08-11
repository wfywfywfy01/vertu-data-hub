"""文件类数据源：监听 settings.watched_root 下的子目录，按 config.handler 分流处理。

handler:
- doc_rag       : 政策/产品文档 → 切片 → doc_chunk
- image         : 陈列/装修图片 → embedding → media_asset
- tabular       : 表格数据（不预设列名）→ 整行 JSONB → structured_record
- unclassified  : 不自动入库，只记录发现了哪些文件，等人工分类
"""
import logging
from pathlib import Path

from app.catalog import registry
from app.chunking import DOCLING_SUFFIXES, PLAIN_SUFFIXES
from app.config import settings
from app.connectors.base import SyncResult
from app.db import execute

logger = logging.getLogger(__name__)

DOC_SUFFIXES = PLAIN_SUFFIXES | DOCLING_SUFFIXES
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
TABULAR_SUFFIXES = {".xlsx", ".xls", ".csv"}

SUFFIXES_BY_HANDLER = {
    "doc_rag": DOC_SUFFIXES,
    "image": IMAGE_SUFFIXES,
    "tabular": TABULAR_SUFFIXES,
}


class FileConnector:
    async def sync(self, source: dict) -> SyncResult:
        config = source["config"]
        handler = config["handler"]
        root = Path(settings.watched_root) / config["path"]
        result = SyncResult()

        if not root.exists():
            result.errors.append(f"watched path not found: {root}")
            return result

        if handler == "unclassified":
            return await self._log_only(source, root, result)

        suffixes = SUFFIXES_BY_HANDLER[handler]
        files = [p for p in sorted(root.iterdir()) if p.is_file() and p.suffix.lower() in suffixes]

        for path in files:
            external_key = path.name
            data = path.read_bytes()
            new_hash = registry.content_hash(data)
            existing = await registry.get_source_item(source["id"], external_key)
            if existing and existing["content_hash"] == new_hash and existing["status"] == "ingested":
                continue  # 未变化，跳过

            try:
                if handler == "doc_rag":
                    await self._ingest_doc(path, source, config)
                elif handler == "image":
                    await self._ingest_image(data, path, source, config)
                elif handler == "tabular":
                    await self._ingest_tabular(path, source, config)
                await registry.upsert_source_item(source["id"], external_key, new_hash, status="ingested")
                result.items_processed += 1
            except Exception as exc:  # 单个文件失败不影响其他文件
                await registry.upsert_source_item(
                    source["id"], external_key, new_hash, status="failed", error=str(exc)
                )
                result.errors.append(f"{path.name}: {exc}")

        return result

    async def _log_only(self, source: dict, root: Path, result: SyncResult) -> SyncResult:
        files = [p.name for p in sorted(root.iterdir()) if p.is_file()]
        if files:
            logger.info(
                "data_source=%s 发现 %d 个待人工分类文件: %s",
                source["code"], len(files), ", ".join(files),
            )
        result.items_processed = 0
        return result

    async def _ingest_doc(self, path: Path, source: dict, config: dict) -> None:
        from app.ingestion.doc_ingest import ingest_file

        item = await registry.get_source_item(source["id"], path.name)
        await ingest_file(
            path,
            data_source_id=source["id"],
            source_item_id=item["id"] if item else None,
            tags=config.get("default_tags", {}),
        )

    async def _ingest_image(self, data: bytes, path: Path, source: dict, config: dict) -> None:
        from psycopg.types.json import Jsonb

        from app.embeddings.image import get_image_embedder
        from app.embeddings.text import vector_literal

        vec = await get_image_embedder().embed_image(data)
        tags = config.get("default_tags", {})
        item = await registry.get_source_item(source["id"], path.name)

        await execute("DELETE FROM media_asset WHERE data_source_id = %s AND url = %s", (source["id"], str(path)))
        await execute(
            "INSERT INTO media_asset (data_source_id, source_item_id, url, tags, embedding)"
            " VALUES (%s, %s, %s, %s, %s::vector)",
            (source["id"], item["id"] if item else None, str(path), Jsonb(tags), vector_literal(vec)),
        )

    async def _ingest_tabular(self, path: Path, source: dict, config: dict) -> None:
        import openpyxl

        dataset_code = config.get("dataset_code") or source["code"]
        item = await registry.get_source_item(source["id"], path.name)

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(rows_iter)]

        for idx, row in enumerate(rows_iter):
            record = {header[i]: _cell_to_str(v) for i, v in enumerate(row) if i < len(header) and header[i]}
            if not any(record.values()):
                continue
            natural_key = f"{path.name}:{idx}"
            await registry.upsert_structured_record(
                data_source_id=source["id"],
                dataset_code=dataset_code,
                natural_key=natural_key,
                data=record,
                record_kind="row",
                source_item_id=item["id"] if item else None,
                row_date=_find_date(record),
            )
        wb.close()


def _cell_to_str(value):
    from datetime import date, datetime

    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _find_date(record: dict):
    """在行数据里找一个像日期的字段值，找不到就返回 None（row_date 允许为空）。"""
    from datetime import date, datetime

    for value in record.values():
        if isinstance(value, str) and len(value) == 10 and value.count("-") == 2:
            try:
                return date.fromisoformat(value)
            except ValueError:
                continue
    return None
