import uuid

import pytest

from app import db
from app.ingestion.local_inbox import ingest_local_path
from app.knowledge.scopes import resolve_scope
from app.retrieval.knowledge_search import search_knowledge
from app.storage import LocalStorage


async def _delete_assets(ids: list[uuid.UUID]) -> None:
    if ids:
        source_rows = await db.fetch_all(
            "SELECT DISTINCT source_object_id FROM asset_version WHERE asset_id = ANY(%s)",
            (ids,),
        )
        source_ids = [row["source_object_id"] for row in source_rows]
        await db.execute(
            "DELETE FROM content_chunk WHERE asset_version_id IN "
            "(SELECT id FROM asset_version WHERE asset_id = ANY(%s))",
            (ids,),
        )
        await db.execute(
            "DELETE FROM derived_artifact WHERE asset_version_id IN "
            "(SELECT id FROM asset_version WHERE asset_id = ANY(%s))",
            (ids,),
        )
        await db.execute(
            "DELETE FROM processing_job WHERE asset_version_id IN "
            "(SELECT id FROM asset_version WHERE asset_id = ANY(%s))",
            (ids,),
        )
        await db.execute("DELETE FROM asset_version WHERE asset_id = ANY(%s)", (ids,))
        await db.execute("DELETE FROM knowledge_asset WHERE id = ANY(%s)", (ids,))
        if source_ids:
            await db.execute(
                "DELETE FROM source_object WHERE id = ANY(%s) AND NOT EXISTS "
                "(SELECT 1 FROM asset_version WHERE source_object_id = source_object.id)",
                (source_ids,),
            )


def test_scope_validation_rejects_fake_or_unsafe_owners():
    department = resolve_scope(scope_type="department", scope_key="海外销售部")

    assert department.storage_prefix == "departments/海外销售部"
    with pytest.raises(ValueError, match="must not include dealer_id"):
        resolve_scope(
            dealer_id=uuid.uuid4(),
            scope_type="department",
            scope_key="overseas-sales",
        )
    with pytest.raises(ValueError, match="unsafe path"):
        resolve_scope(scope_type="department", scope_key="../finance")


async def test_department_and_company_files_are_ingested_and_authorized(tmp_path):
    department_key = f"overseas-sales-{uuid.uuid4().hex[:8]}"
    company_marker = f"Aurelia{uuid.uuid4().hex[:8]}"
    department_dir = tmp_path / "department"
    company_dir = tmp_path / "company"
    department_dir.mkdir()
    company_dir.mkdir()
    (department_dir / "warranty.md").write_text(
        "# Zafiro warranty\n\nZafiro dealer warranty claims use the shared approval form.",
        encoding="utf-8",
    )
    (company_dir / f"brand-{uuid.uuid4().hex[:8]}.md").write_text(
        f"# {company_marker} brand rule\n\n"
        f"{company_marker} is the company-wide approved brand wording.",
        encoding="utf-8",
    )
    storage = LocalStorage(tmp_path / "objects")
    created_asset_ids = []

    try:
        department = await ingest_local_path(
            department_dir,
            scope_type="department",
            scope_key=department_key,
            sensitivity="confidential",
            actor_id="pytest-admin",
            storage=storage,
        )
        company = await ingest_local_path(
            company_dir,
            scope_type="company",
            sensitivity="internal",
            actor_id="pytest-admin",
            storage=storage,
        )

        assert department["succeeded"] == 1, department
        assert company["succeeded"] == 1, company
        created_asset_ids = [
            uuid.UUID(department["items"][0]["asset_id"]),
            uuid.UUID(company["items"][0]["asset_id"]),
        ]
        rows = await db.fetch_all(
            "SELECT scope_type, scope_key, dealer_id FROM knowledge_asset WHERE id = ANY(%s)",
            (created_asset_ids,),
        )
        assert {row["scope_type"] for row in rows} == {"department", "company"}
        assert all(row["dealer_id"] is None for row in rows)

        allowed = await search_knowledge(
            "Zafiro warranty approval",
            dealer_ids=[],
            team_keys=[department_key],
            actor_id="pytest-manager",
        )
        assert any(row["scope_type"] == "department" for row in allowed)

        denied = await search_knowledge(
            "Zafiro warranty approval",
            dealer_ids=[],
            team_keys=["another-team"],
            actor_id="pytest-manager",
        )
        assert all(row["scope_type"] != "department" for row in denied)

        company_results = await search_knowledge(
            f"{company_marker} approved brand wording",
            dealer_ids=[],
            team_keys=[],
            actor_id="pytest-viewer",
        )
        assert company_results[0]["scope_type"] == "company"
        assert company_results[0]["scope_key"] == "vertu"
    finally:
        await _delete_assets(created_asset_ids)
