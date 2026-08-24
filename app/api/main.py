from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from app.api.errors import ApiError, api_error_handler
from app.api.routes import router
from app import db
from app.config import settings, validate_production_settings
from app.embeddings.chinese_clip import get_chinese_clip
from app.metrics import begin_request, end_request, render as render_metrics
from app.model_contract import require_api_models


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_production_settings()
    if settings.app_env == "production":
        await asyncio.to_thread(require_api_models)
    if settings.semantic_image_preload:
        await asyncio.to_thread(get_chinese_clip().embed_texts, ["图片检索预热"])
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
    started = begin_request()
    supplied = request.headers.get("X-Request-ID", "").strip()
    request.state.request_id = supplied[:200] if supplied else str(uuid.uuid4())
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request.state.request_id
        return response
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        end_request(request.method, route_path, status, started)


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


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(render_metrics(), media_type="text/plain; version=0.0.4")


app.include_router(router)
