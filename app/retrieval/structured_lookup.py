"""structured_record 精确查询封装：按 dataset_code + 日期范围/JSONB 过滤，不走向量相似。"""
import json
from datetime import date

from app import db


async def lookup_records(
    dataset_code: str,
    data_source_id: int | None = None,
    record_kind: str | None = None,
    row_date_from: date | None = None,
    row_date_to: date | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    data_contains: dict | None = None,
    limit: int = 100,
) -> list[dict]:
    conditions = ["dataset_code = %s"]
    params: list = [dataset_code]
    if data_source_id is not None:
        conditions.append("data_source_id = %s")
        params.append(data_source_id)
    if record_kind:
        conditions.append("record_kind = %s")
        params.append(record_kind)
    if row_date_from:
        conditions.append("row_date >= %s")
        params.append(row_date_from)
    if row_date_to:
        conditions.append("row_date <= %s")
        params.append(row_date_to)
    if period_start:
        conditions.append("period_start >= %s")
        params.append(period_start)
    if period_end:
        conditions.append("period_end <= %s")
        params.append(period_end)
    if data_contains:
        conditions.append("data @> %s::jsonb")
        params.append(json.dumps(data_contains))
    params.append(limit)

    return await db.fetch_all(
        f"""
        SELECT id, data_source_id, dataset_code, record_kind, natural_key,
               period_start, period_end, row_date, data, created_at
        FROM structured_record
        WHERE {' AND '.join(conditions)}
        ORDER BY COALESCE(row_date, period_start) DESC NULLS LAST, id DESC
        LIMIT %s
        """,
        params,
    )


async def list_datasets(data_source_id: int | None = None) -> list[dict]:
    """给未来 agent 做取数定位用：有哪些数据集、口径说明是什么。"""
    if data_source_id is not None:
        return await db.fetch_all(
            "SELECT * FROM structured_dataset WHERE data_source_id = %s ORDER BY dataset_code",
            (data_source_id,),
        )
    return await db.fetch_all("SELECT * FROM structured_dataset ORDER BY dataset_code")
