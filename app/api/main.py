from __future__ import annotations

import uuid
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from app.api.errors import ApiError, api_error_handler
from app.api.routes import router
from app import db
from app.config import settings, validate_production_settings
from app.metrics import begin_request, end_request, render as render_metrics


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
    try:
        row = await asyncio.wait_for(db.fetch_one("SELECT 1 AS ready"), timeout=5)
        ok = bool(row and row["ready"] == 1)
    except Exception:
        ok = False
    return JSONResponse({"status": "ok" if ok else "failed"}, status_code=200 if ok else 503)


@app.get("/health/ingestion")
async def ingestion_health():
    from app.queue import celery_app
    try:
        backlog = await asyncio.wait_for(db.fetch_one(
            """SELECT count(*) FILTER (WHERE status = 'queued') AS queued,
                      count(*) FILTER (WHERE status = 'running') AS running,
                      count(*) FILTER (WHERE status = 'failed') AS failed,
                      coalesce(extract(epoch FROM now() - min(created_at)
                        FILTER (WHERE status IN ('queued', 'running'))), 0)::bigint AS oldest_pending_seconds
               FROM processing_job"""), timeout=5)
        workers = await asyncio.wait_for(asyncio.to_thread(
            lambda: celery_app.control.inspect(timeout=3).ping()), timeout=5)
        ok = bool(workers) and backlog["oldest_pending_seconds"] < 3600
        return JSONResponse({"status": "ok" if ok else "degraded", "workers_online": bool(workers),
                             **backlog}, status_code=200 if ok else 503)
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(render_metrics(), media_type="text/plain; version=0.0.4")


app.include_router(router)
