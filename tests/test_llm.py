"""LLM router: env-only credentials, OpenAI preferred, Anthropic optional."""

from __future__ import annotations

from epidebug.llm import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    EngineLLM,
    _parse_json_object,
)


def test_unavailable_without_keys(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("EPIDEBUG_MODEL", raising=False)
    monkeypatch.delenv("EPIDEBUG_LLM_PROVIDER", raising=False)
    llm = EngineLLM()
    assert llm.available is False
    assert llm.provider == "none"
    assert llm.complete_json("{}", "sys") is None
    assert llm.model == DEFAULT_OPENAI_MODEL


def test_prefers_openai_when_both_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
    monkeypatch.delenv("EPIDEBUG_MODEL", raising=False)
    monkeypatch.delenv("EPIDEBUG_LLM_PROVIDER", raising=False)
    llm = EngineLLM()
    assert llm.available is True
    assert llm.provider == "openai"
    assert llm.model == DEFAULT_OPENAI_MODEL
    assert llm.api_key == "sk-test"


def test_anthropic_when_only_anthropic_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
    monkeypatch.delenv("EPIDEBUG_MODEL", raising=False)
    monkeypatch.delenv("EPIDEBUG_LLM_PROVIDER", raising=False)
    llm = EngineLLM()
    assert llm.available is True
    assert llm.provider == "anthropic"
    assert llm.model == DEFAULT_ANTHROPIC_MODEL
    assert llm.api_key == "ant-test"


def test_model_env_and_provider_override(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
    monkeypatch.setenv("EPIDEBUG_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("EPIDEBUG_MODEL", "gpt-4o-mini")
    llm = EngineLLM()
    assert llm.provider == "anthropic"
    assert llm.model == DEFAULT_ANTHROPIC_MODEL


def test_parse_json_object_fences():
    assert _parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_object("noise {\"b\": 2} trailing") == {"b": 2}
    assert _parse_json_object("not json") is None
