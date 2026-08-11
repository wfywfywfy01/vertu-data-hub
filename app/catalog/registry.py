"""data_source / source_item / structured_dataset / ingestion_run 的 CRUD。

所有 connector 与 CLI 都通过这里读写目录表，不直接手写 SQL 散落各处。
"""
import hashlib
from typing import Optional

from psycopg.types.json import Jsonb

from app import db
from app.catalog.models import validate_config


# ---------- data_source ----------

async def upsert_data_source(
    code: str,
    source_type: str,
    display_name: str,
    config: dict,
    description: str | None = None,
    enabled: bool = True,
) -> dict:
    validate_config(source_type, config)  # 校验失败直接抛错，不落库脏配置
    return await db.execute_returning(
        """
        INSERT INTO data_source (code, source_type, display_name, description, config, enabled)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (code) DO UPDATE SET
            source_type = EXCLUDED.source_type,
            display_name = EXCLUDED.display_name,
            description = EXCLUDED.description,
            config = EXCLUDED.config,
            enabled = EXCLUDED.enabled,
            updated_at = now()
        RETURNING *
        """,
        (code, source_type, display_name, description, Jsonb(config), enabled),
    )


async def get_data_source(code: str) -> Optional[dict]:
    return await db.fetch_one("SELECT * FROM data_source WHERE code = %s", (code,))


async def list_data_sources(enabled_only: bool = False) -> list[dict]:
    if enabled_only:
        return await db.fetch_all("SELECT * FROM data_source WHERE enabled ORDER BY id")
    return await db.fetch_all("SELECT * FROM data_source ORDER BY id")


async def mark_synced(data_source_id: int) -> None:
    await db.execute(
        "UPDATE data_source SET last_synced_at = now() WHERE id = %s", (data_source_id,)
    )


# ---------- source_item（同步幂等台账） ----------

def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def get_source_item(data_source_id: int, external_key: str) -> Optional[dict]:
    return await db.fetch_one(
        "SELECT * FROM source_item WHERE data_source_id = %s AND external_key = %s",
        (data_source_id, external_key),
    )


async def upsert_source_item(
    data_source_id: int,
    external_key: str,
    content_hash_value: str | None,
    status: str = "ingested",
    error: str | None = None,
) -> dict:
    return await db.execute_returning(
        """
        INSERT INTO source_item (data_source_id, external_key, content_hash, status, error, last_ingested_at)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (data_source_id, external_key) DO UPDATE SET
            content_hash = EXCLUDED.content_hash,
            status = EXCLUDED.status,
            error = EXCLUDED.error,
            last_ingested_at = now()
        RETURNING *
        """,
        (data_source_id, external_key, content_hash_value, status, error),
    )


# ---------- structured_record（结构化数据：行/快照） ----------

async def upsert_structured_record(
    data_source_id: int,
    dataset_code: str,
    natural_key: str,
    data: dict,
    record_kind: str = "row",
    source_item_id: int | None = None,
    period_start=None,
    period_end=None,
    row_date=None,
) -> dict:
    return await db.execute_returning(
        """
        INSERT INTO structured_record
            (data_source_id, source_item_id, dataset_code, record_kind, natural_key,
             period_start, period_end, row_date, data)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (data_source_id, dataset_code, natural_key) DO UPDATE SET
            data = EXCLUDED.data,
            period_start = EXCLUDED.period_start,
            period_end = EXCLUDED.period_end,
            row_date = EXCLUDED.row_date,
            source_item_id = EXCLUDED.source_item_id
        RETURNING *
        """,
        (
            data_source_id, source_item_id, dataset_code, record_kind, natural_key,
            period_start, period_end, row_date, Jsonb(data),
        ),
    )


# ---------- structured_dataset（数据字典） ----------

async def upsert_structured_dataset(
    data_source_id: int,
    dataset_code: str,
    display_name: str | None = None,
    description: str | None = None,
    columns_doc: list | None = None,
    query_hint: str | None = None,
    refresh_mode: str = "snapshot",
) -> dict:
    return await db.execute_returning(
        """
        INSERT INTO structured_dataset
            (data_source_id, dataset_code, display_name, description, columns_doc, query_hint, refresh_mode)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (data_source_id, dataset_code) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            description = EXCLUDED.description,
            columns_doc = EXCLUDED.columns_doc,
            query_hint = EXCLUDED.query_hint,
            refresh_mode = EXCLUDED.refresh_mode
        RETURNING *
        """,
        (
            data_source_id, dataset_code, display_name, description,
            Jsonb(columns_doc) if columns_doc is not None else None,
            query_hint, refresh_mode,
        ),
    )


# ---------- ingestion_run（同步日志） ----------

async def start_run(data_source_id: int) -> dict:
    return await db.execute_returning(
        "INSERT INTO ingestion_run (data_source_id) VALUES (%s) RETURNING *",
        (data_source_id,),
    )


async def finish_run(
    run_id: int, status: str, items_processed: int = 0, error: str | None = None
) -> None:
    await db.execute(
        """
        UPDATE ingestion_run
        SET status = %s, items_processed = %s, error = %s, finished_at = now()
        WHERE id = %s
        """,
        (status, items_processed, error, run_id),
    )
