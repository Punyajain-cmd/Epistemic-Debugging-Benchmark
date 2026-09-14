"""Lightweight LLM client used by the diagnostic engine and chat loop.

Credentials are read only from the environment (or constructor args for tests).
Never hardcode secrets.

Env vars
--------
OPENAI_API_KEY         OpenAI (preferred when both keys exist)
ANTHROPIC_API_KEY      optional Anthropic
EPIDEBUG_MODEL         default ``gpt-4o-mini``; Anthropic-only default is
                       ``claude-3-5-haiku-latest``
EPIDEBUG_LLM_PROVIDER  optional ``openai`` or ``anthropic``
"""

from __future__ import annotations

import json
import os
import re
from typing import Any


DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-haiku-latest"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _looks_like_openai_model(model: str) -> bool:
    low = model.lower()
    return (
        low.startswith("gpt-")
        or low.startswith("o1")
        or low.startswith("o3")
        or low.startswith("o4")
    )


def _looks_like_anthropic_model(model: str) -> bool:
    return model.lower().startswith("claude")


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


class EngineLLM:
    """Route completions through OpenAI or Anthropic when a key is present."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        anthropic_api_key: str | None = None,
        provider: str | None = None,
    ):
        self.openai_key = (api_key if api_key is not None else _env("OPENAI_API_KEY"))
        self.anthropic_key = (
            anthropic_api_key if anthropic_api_key is not None else _env("ANTHROPIC_API_KEY")
        )
        self.provider = self._resolve_provider(provider)
        requested = (model or _env("EPIDEBUG_MODEL") or "").strip()
        self.model = requested or self._default_model()
        if self.provider == "anthropic" and requested and _looks_like_openai_model(requested):
            self.model = DEFAULT_ANTHROPIC_MODEL
        if self.provider == "openai" and requested and _looks_like_anthropic_model(requested):
            self.model = DEFAULT_OPENAI_MODEL

    def _resolve_provider(self, provider: str | None) -> str:
        pref = (provider or _env("EPIDEBUG_LLM_PROVIDER")).lower()
        if pref == "openai" and self.openai_key:
            return "openai"
        if pref == "anthropic" and self.anthropic_key:
            return "anthropic"
        if self.openai_key:
            return "openai"
        if self.anthropic_key:
            return "anthropic"
        return "none"

    def _default_model(self) -> str:
        if self.provider == "anthropic":
            return DEFAULT_ANTHROPIC_MODEL
        return DEFAULT_OPENAI_MODEL

    @property
    def api_key(self) -> str:
        """Active provider key (kept for older callers)."""
        if self.provider == "anthropic":
            return self.anthropic_key
        return self.openai_key

    @property
    def available(self) -> bool:
        return bool(self.openai_key or self.anthropic_key)

    def complete_json(self, prompt: str, system: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        text = self.complete(
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_json_object(text or "")

    def complete_chat_json(
        self,
        messages: list[dict[str, str]],
        system: str,
    ) -> dict[str, Any] | None:
        if not self.available:
            return None
        text = self.complete(system=system, messages=messages)
        return _parse_json_object(text or "")

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 2500,
        temperature: float = 0.2,
    ) -> str | None:
        if not self.available:
            return None
        chat = _to_provider_messages(messages)
        if not chat:
            return None
        if self.provider == "anthropic":
            return self._complete_anthropic(
                system=system,
                messages=chat,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        return self._complete_openai(
            system=system,
            messages=chat,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def _complete_openai(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str | None:
        try:
            from openai import OpenAI
        except ImportError:
            return None
        if not self.openai_key:
            return None
        try:
            client = OpenAI(api_key=self.openai_key)
            payload = []
            if system:
                payload.append({"role": "system", "content": system})
            payload.extend(messages)
            response = client.chat.completions.create(
                model=self.model,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=payload,
            )
            return response.choices[0].message.content or ""
        except Exception:
            return None

    def _complete_anthropic(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str | None:
        try:
            from anthropic import Anthropic
        except ImportError:
            return None
        if not self.anthropic_key:
            return None
        try:
            client = Anthropic(api_key=self.anthropic_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system or "You are an epistemic debugging assistant.",
                messages=messages,
                temperature=temperature,
            )
            chunks = []
            for block in response.content or []:
                text = getattr(block, "text", None)
                if text:
                    chunks.append(text)
            return "".join(chunks)
        except Exception:
            return None


def _to_provider_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep user/assistant turns; fold system-tool notes into user content."""
    out: list[dict[str, str]] = []
    for item in messages:
        role = (item.get("role") or "user").strip()
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if role in {"system-tool", "tool", "system"}:
            role = "user"
            content = f"[system-tool] {content}"
        if role not in {"user", "assistant"}:
            role = "user"
        if out and out[-1]["role"] == role:
            out[-1]["content"] = out[-1]["content"] + "\n\n" + content
        else:
            out.append({"role": role, "content": content})
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "(continue)"})
    return out
