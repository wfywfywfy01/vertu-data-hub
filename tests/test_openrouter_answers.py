import json

import httpx
import pytest

from app.answers.openrouter import OpenRouterClient


def _configure(monkeypatch):
    from app.answers import openrouter

    monkeypatch.setattr(openrouter.settings, "allow_external_text_generation", True)
    monkeypatch.setattr(openrouter.settings, "answer_provider", "openrouter")
    monkeypatch.setattr(openrouter.settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(
        openrouter.settings,
        "openrouter_base_url",
        "https://openrouter.ai/api/v1",
    )
    monkeypatch.setattr(openrouter.settings, "openrouter_model", "openai/gpt-4.1-mini")
    monkeypatch.setattr(openrouter.settings, "openrouter_http_referer", "")
    monkeypatch.setattr(openrouter.settings, "openrouter_app_title", "Dealer Knowledge")


def _configure_deepseek(monkeypatch):
    from app.answers import openrouter

    monkeypatch.setattr(openrouter.settings, "allow_external_text_generation", True)
    monkeypatch.setattr(openrouter.settings, "answer_provider", "deepseek")
    monkeypatch.setattr(openrouter.settings, "deepseek_api_key", "deepseek-test-key")
    monkeypatch.setattr(openrouter.settings, "deepseek_model", "deepseek-chat")


async def test_openrouter_requests_strict_structured_grounded_answer(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    async def handler(request):
        captured["request"] = request
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-4.1-mini",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"answer": "库存为 12 台。", "cited_indices": [1]},
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "ignored": "secret",
                },
            },
        )

    client = OpenRouterClient(transport=httpx.MockTransport(handler))
    result = await client.generate(
        "当前库存？",
        [
            {
                "text": "Safiran Hamrah 当前库存为 12 台。",
                "citation": {
                    "title": "库存周报",
                    "original_name": "inventory.pdf",
                    "page_start": 2,
                },
            }
        ],
    )

    request = captured["request"]
    body = captured["body"]
    assert request.url.path == "/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["provider"] == {"require_parameters": True}
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "untrusted" in body["messages"][0]["content"]
    assert result.answer == "库存为 12 台。"
    assert result.cited_indices == [1]
    assert result.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_openrouter_rejects_non_official_endpoint(monkeypatch):
    _configure(monkeypatch)
    from app.answers import openrouter

    monkeypatch.setattr(
        openrouter.settings,
        "openrouter_base_url",
        "https://openrouter.ai.example/api/v1",
    )

    with pytest.raises(RuntimeError, match="OPENROUTER_BASE_URL"):
        OpenRouterClient()


async def test_deepseek_uses_fixed_endpoint_and_json_output(monkeypatch):
    _configure_deepseek(monkeypatch)
    captured = {}

    async def handler(request):
        captured["request"] = request
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "deepseek-chat",
                "choices": [{
                    "message": {
                        "content": json.dumps(
                            {"answer": "库存为 12 台。", "cited_indices": [1]},
                            ensure_ascii=False,
                        )
                    }
                }],
            },
        )

    client = OpenRouterClient(transport=httpx.MockTransport(handler))
    result = await client.generate(
        "库存？",
        [{
            "text": "库存为 12 台。",
            "citation": {
                "title": "库存",
                "original_name": "inventory.pdf",
                "page_start": 1,
            },
        }],
    )

    assert captured["request"].url == "https://api.deepseek.com/v1/chat/completions"
    assert captured["request"].headers["authorization"] == "Bearer deepseek-test-key"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "provider" not in captured["body"]
    assert result.model == "deepseek-chat"


async def test_openrouter_rejects_coerced_citation_index(monkeypatch):
    _configure(monkeypatch)

    async def handler(_request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"answer": "库存为 12 台。", "cited_indices": ["1"]},
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    client = OpenRouterClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="invalid structured answer"):
        await client.generate(
            "库存？",
            [
                {
                    "text": "库存为 12 台。",
                    "citation": {
                        "title": "库存",
                        "original_name": "inventory.pdf",
                        "page_start": 2,
                    },
                }
            ],
        )
