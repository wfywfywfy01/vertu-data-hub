"""统一数据库连接模块（异步连接池）。所有模块通过这里访问数据库，不各自 connect。

连接目标完全由 .env 的 DATABASE_URL 决定，代码不区分开发/正式环境。
"""
import asyncio
import sys
from typing import Any, Optional

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

# Windows 下 psycopg 异步模式不支持 ProactorEventLoop（仅影响本地开发，Docker/Linux 无此问题）
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_pool: Optional[AsyncConnectionPool] = None


async def _configure(conn) -> None:
    # 注册 pgvector 类型；schema 尚未执行（无 vector 扩展）时容忍失败
    try:
        from pgvector.psycopg import register_vector_async

        await register_vector_async(conn)
    except Exception:
        pass


async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=10,
            open=False,
            configure=_configure,
            kwargs={"row_factory": dict_row},
        )
        await _pool.open()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def fetch_all(query: str, params: Any = None) -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(query, params)
        return await cur.fetchall()


async def fetch_one(query: str, params: Any = None) -> Optional[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(query, params)
        return await cur.fetchone()


async def execute(query: str, params: Any = None) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(query, params)


async def execute_returning(query: str, params: Any = None) -> Optional[dict]:
    """执行 INSERT ... RETURNING 等语句并返回一行。"""
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(query, params)
        return await cur.fetchone()
