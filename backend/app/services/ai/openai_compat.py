"""Provider for any OpenAI-compatible chat endpoint (e.g. a private/in-house model server).
Treated as internal only when OPENAI_IS_INTERNAL=true, otherwise it is gated like any external provider."""
from __future__ import annotations

import httpx

from ...config import get_settings
from .base import AIError, AIProvider, AIResult


class OpenAICompatProvider(AIProvider):
    name = "openai_compatible"

    def __init__(self) -> None:
        s = get_settings()
        self.model = s.openai_model or ""
        self.external = not s.openai_is_internal
        self._base = (s.openai_base_url or "").rstrip("/")
        self._key = s.openai_api_key
        self._timeout = s.ai_timeout_seconds

    def analyze(self, system: str, user: str) -> AIResult:
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        body = {"model": self.model, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            r = httpx.post(f"{self._base}/chat/completions", json=body, headers=headers, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise AIError(f"Could not reach the AI endpoint: {exc}") from exc
        if r.status_code >= 400:
            raise AIError(f"AI endpoint returned HTTP {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise AIError("AI endpoint returned an unexpected response shape.") from exc
        return AIResult(text=text, model=data.get("model", self.model), usage=data.get("usage", {}))
