import uuid

import pytest

from app.answers import service
from app.answers.openrouter import ModelAnswer


def _evidence(*, sensitivity="internal", lexical_score=0.8):
    asset_id = uuid.uuid4()
    return {
        "asset_id": asset_id,
        "text": "Safiran Hamrah inventory is 12 units. Contact frank.fu@vertu.cn.",
        "sensitivity": sensitivity,
        "lexical_score": lexical_score,
        "semantic_similarity": 0.8,
        "citation": {
            "asset_id": str(asset_id),
            "title": "Inventory",
            "original_name": "inventory.pdf",
            "page_start": 2,
            "page_end": 2,
        },
    }


class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def generate(self, query, evidence):
        self.calls.append((query, evidence))
        return self.answer


def _patch_dependencies(monkeypatch, results):
    audits = []

    async def search(*_args, **_kwargs):
        return results

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(service, "search_knowledge", search)
    monkeypatch.setattr(service, "_audit_answer", audit)
    return audits


async def test_answer_refuses_without_sufficient_evidence(monkeypatch):
    audits = _patch_dependencies(monkeypatch, [])
    client = FakeClient(None)

    result = await service.answer_question(
        "库存？",
        dealer_ids=[],
        actor_id="sales",
        client=client,
    )

    assert result["status"] == "insufficient_evidence"
    assert result["answer"] == service.NO_EVIDENCE
    assert client.calls == []
    assert audits[-1]["status"] == "insufficient_evidence"


async def test_answer_blocks_sensitive_evidence_from_external_model(monkeypatch):
    evidence = _evidence(sensitivity="confidential")
    audits = _patch_dependencies(monkeypatch, [evidence])
    client = FakeClient(None)

    result = await service.answer_question(
        "库存？",
        dealer_ids=None,
        actor_id="sales",
        client=client,
    )

    assert result["status"] == "sensitive_evidence_blocked"
    assert result["citations"] == [evidence["citation"]]
    assert client.calls == []
    assert audits[-1]["status"] == "sensitive_evidence_blocked"


async def test_answer_redacts_query_and_output_and_validates_citation(monkeypatch):
    evidence = _evidence()
    audits = _patch_dependencies(monkeypatch, [evidence])
    client = FakeClient(
        ModelAnswer(
            answer="请联系 frank.fu@vertu.cn.",
            cited_indices=[1, 1],
            model="openai/gpt-4.1-mini",
            usage={"total_tokens": 25},
        )
    )

    result = await service.answer_question(
        "frank.fu@vertu.cn 的库存？",
        dealer_ids=None,
        actor_id="sales",
        client=client,
    )

    assert result["status"] == "answered"
    assert "frank.fu" not in result["answer"]
    assert "[REDACTED_EMAIL]" in result["answer"]
    assert "frank.fu" not in client.calls[0][0]
    assert result["citations"] == [evidence["citation"]]
    assert audits[-1]["status"] == "answered"


async def test_answer_rejects_invalid_model_citation(monkeypatch):
    _patch_dependencies(monkeypatch, [_evidence()])
    client = FakeClient(
        ModelAnswer(
            answer="库存为 12 台。",
            cited_indices=[2],
            model="openai/gpt-4.1-mini",
            usage={},
        )
    )

    with pytest.raises(service.InvalidModelAnswerError):
        await service.answer_question(
            "库存？",
            dealer_ids=None,
            actor_id="sales",
            client=client,
        )


async def test_answer_honors_model_no_evidence_even_with_citation(monkeypatch):
    evidence = _evidence()
    _patch_dependencies(monkeypatch, [evidence])
    client = FakeClient(
        ModelAnswer(
            answer=service.NO_EVIDENCE,
            cited_indices=[1],
            model="openai/gpt-4.1-mini",
            usage={},
        )
    )

    result = await service.answer_question(
        "库存？",
        dealer_ids=None,
        actor_id="sales",
        client=client,
    )

    assert result["status"] == "insufficient_evidence"
    assert result["citations"] == []
