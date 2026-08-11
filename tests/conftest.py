import asyncio
from pathlib import Path

import pytest

from app import db

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


@pytest.fixture(scope="session", autouse=True)
def _apply_schema():
    async def _run():
        pool = await db.get_pool()
        async with pool.connection() as conn:
            await conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        await db.close_pool()

    asyncio.run(_run())
