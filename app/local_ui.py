"""Loopback-only pilot UI for querying dealer knowledge."""
from __future__ import annotations

from ipaddress import ip_address
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import db
from app.config import settings
from app.knowledge import assets, dealers
from app.retrieval.knowledge_search import search_knowledge
from app.storage import LocalStorage, ObjectNotFoundError


router = APIRouter(include_in_schema=False)
STATIC_ROOT = Path(__file__).resolve().parent / "static"


class LocalSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    dealer_id: UUID
    category: str | None = Field(default=None, max_length=40)
    top_k: int = Field(default=8, ge=1, le=20)


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    try:
        is_loopback = ip_address(host).is_loopback
    except ValueError:
        is_loopback = host == "testclient"
    if settings.app_env != "development" or not is_loopback:
        raise HTTPException(status_code=404)


@router.get("/")
async def home(request: Request):
    _require_local(request)
    return RedirectResponse("/ui", status_code=307)


@router.get("/ui")
async def query_page(request: Request):
    _require_local(request)
    return FileResponse(STATIC_ROOT / "query.html", media_type="text/html")


@router.get("/ui/query.css")
async def query_styles(request: Request):
    _require_local(request)
    return FileResponse(STATIC_ROOT / "query.css", media_type="text/css")


@router.get("/ui/query.js")
async def query_script(request: Request):
    _require_local(request)
    return FileResponse(STATIC_ROOT / "query.js", media_type="text/javascript")


@router.get("/ui/api/dealers")
async def local_dealers(request: Request):
    _require_local(request)
    rows = await db.fetch_all(
        """
        SELECT d.id, d.official_name, d.country_code, d.city,
               count(a.id) FILTER (WHERE a.status = 'searchable') AS asset_count
        FROM dealer d
        LEFT JOIN knowledge_asset a ON a.dealer_id = d.id
        WHERE d.status = 'active'
        GROUP BY d.id
        ORDER BY d.official_name
        """
    )
    return {"items": rows, "count": len(rows)}


@router.post("/ui/api/search")
async def local_search(body: LocalSearchRequest, request: Request):
    _require_local(request)
    if body.category and body.category not in assets.CATEGORIES:
        raise HTTPException(status_code=422, detail="不支持该资料分类")
    dealer = await dealers.list_dealers([body.dealer_id])
    if not dealer or dealer[0]["status"] != "active":
        raise HTTPException(status_code=404, detail="经销商不存在")
    rows = await search_knowledge(
        body.query,
        dealer_ids=[body.dealer_id],
        actor_id="local-pilot-ui",
        request_id=request.state.request_id,
        dealer_id=body.dealer_id,
        category=body.category,
        top_k=body.top_k,
    )
    return {
        "items": rows,
        "count": len(rows),
        "dealer": {
            "id": dealer[0]["id"],
            "official_name": dealer[0]["official_name"],
        },
    }


@router.get("/ui/api/assets/{asset_id}/content")
async def local_asset_content(asset_id: UUID, request: Request):
    _require_local(request)
    row = await db.fetch_one(
        """
        SELECT a.status, s.bucket, s.object_key, s.content_type
        FROM knowledge_asset a
        JOIN asset_version v ON v.asset_id = a.id AND v.is_current
        JOIN source_object s ON s.id = v.source_object_id
        WHERE a.id = %s
        """,
        (asset_id,),
    )
    if (
        not row
        or row["status"] != "searchable"
        or row["bucket"] != "local-inbox"
        or not row["content_type"].startswith("image/")
    ):
        raise HTTPException(status_code=404, detail="图片不存在")
    try:
        path = LocalStorage().get_file_path(row["object_key"])
    except (ObjectNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="图片不存在") from None
    return FileResponse(
        path,
        media_type=row["content_type"],
        headers={"Cache-Control": "private, max-age=300"},
    )
