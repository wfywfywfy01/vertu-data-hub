import asyncio

from app.runtime import selector_loop_factory


def test_selector_loop_factory_is_psycopg_compatible():
    loop = selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()
