"""Lightweight LLM client used by the diagnostic engine."""

from __future__ import annotations

import json
import os
import re
from typing import Any


class EngineLLM:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.environ.get("EPIDEBUG_MODEL", "gpt-4o-mini")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete_json(self, prompt: str, system: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        try:
            from openai import OpenAI
        except ImportError:
            return None
        client = OpenAI(api_key=self.api_key)
        response = client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            max_tokens=2500,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.choices[0].message.content or ""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None
