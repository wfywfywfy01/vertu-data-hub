"""Runtime helpers that must be selected before Uvicorn creates its event loop."""
import asyncio


def selector_loop_factory():
    return asyncio.SelectorEventLoop()

