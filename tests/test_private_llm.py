import json
from unittest.mock import Mock

import pytest
import requests
from pydantic import BaseModel

from src.agents.private_llm import PrivateLLM
from src.agents.seo_writer import SEOWriter


class Answer(BaseModel):
    answer: str


def _secret():
    return json.dumps(
        {
            "url": "https://private.example/generate",
            "headers": {"Referer": "https://private.example/"},
            "form": {
                "opaque": "private-value",
                "conversation": (
                    '[{"role":"system","content":{{SYSTEM_PROMPT_JSON}}},'
                    '{"role":"user","content":{{USER_PROMPT_JSON}}}]'
                ),
            },
            "grounded_form": {"search_switch": "1"},
            "response_text_path": "items.0.parts.*.body",
        }
    )


def test_private_transport_sends_multipart_with_escaped_prompts(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Mock(status_code=200, json=lambda: {"items": [{"parts": [{"body": None}, {"body": '{"answer":"ok"}'}]}]})

    monkeypatch.setattr(requests, "post", fake_post)
    result = PrivateLLM(_secret()).generate('Rule: say "hello"', "First\nSecond", Answer)

    assert result.answer == "ok"
    assert captured["url"] == "https://private.example/generate"
    assert captured["allow_redirects"] is False
    assert captured["files"]["opaque"] == (None, "private-value")
    conversation = json.loads(captured["files"]["conversation"][1])
    assert conversation[0]["content"].startswith('Rule: say "hello"')
    assert "matching this schema" in conversation[0]["content"]
    assert conversation[1]["content"] == "First\nSecond"


def test_private_transport_does_not_expose_endpoint_or_credentials_in_network_errors(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("https://private.example/generate?key=private-value")

    monkeypatch.setattr(requests, "post", fail)
    with pytest.raises(RuntimeError, match="network error") as exc:
        PrivateLLM(_secret()).generate("system", "user", Answer)
    assert "private.example" not in str(exc.value)
    assert "private-value" not in str(exc.value)


def test_grounded_request_adds_only_the_private_override(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs["files"])
        return Mock(status_code=200, json=lambda: {"items": [{"parts": [{"body": '{"answer":"ok"}'}]}]})

    monkeypatch.setattr(requests, "post", fake_post)
    transport = PrivateLLM(_secret())
    transport.generate("system", "user", Answer)
    transport.generate("system", "user", Answer, grounded=True)
    assert "search_switch" not in calls[0]
    assert calls[1]["search_switch"] == (None, "1")


def test_writer_uses_private_transport_without_a_standard_api_key(monkeypatch):
    monkeypatch.setattr("src.agents.seo_writer.settings.private_llm_request", _secret())
    monkeypatch.setattr("src.agents.seo_writer.settings.gemini_api_key", None)
    monkeypatch.setattr("src.agents.seo_writer.settings.unsplash_access_key", None)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: Mock(
            status_code=200,
            json=lambda: {"items": [{"parts": [{"body": '{"answer":"ok"}'}]}]},
        ),
    )

    writer = SEOWriter()
    assert writer.client is None
    assert writer._call_gemini_structured("question", Answer, max_retries=1).answer == "ok"


def test_supplied_candidate_response_shape(monkeypatch):
    config = json.loads(_secret())
    config["response_text_path"] = "candidates.0.content.parts.*.text"
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: Mock(
            status_code=200,
            json=lambda: {
                "candidates": [{"content": {"parts": [{"text": '{"answer":"ok"}'}]}}]
            },
        ),
    )
    assert PrivateLLM(json.dumps(config)).generate("system", "user", Answer).answer == "ok"


def test_private_transport_rejects_insecure_or_incomplete_templates():
    for secret in (
        "not json",
        _secret().replace("https://private.example/generate", "http://private.example/generate"),
        _secret().replace("{{USER_PROMPT_JSON}}", "missing"),
    ):
        with pytest.raises(ValueError, match="PRIVATE_LLM_REQUEST is invalid"):
            PrivateLLM(secret)
