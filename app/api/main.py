from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.errors import ApiError, api_error_handler
from app.api.routes import router
from app import db
from app.config import settings, validate_production_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_production_settings()
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(
    title="Dealer Knowledge Hub",
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.add_exception_handler(ApiError, api_error_handler)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    supplied = request.headers.get("X-Request-ID", "").strip()
    request.state.request_id = supplied[:200] if supplied else str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, _exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_failed",
            "message": "Request validation failed",
            "request_id": request.state.request_id,
        },
    )


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    row = await db.fetch_one("SELECT 1 AS ready")
    return {"status": "ok" if row and row["ready"] == 1 else "failed"}


app.include_router(router)
