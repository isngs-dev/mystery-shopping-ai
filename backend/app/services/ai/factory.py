from __future__ import annotations

from ...config import get_settings
from ...enums import AIStatus
from .base import AIProvider

_override: AIProvider | None = None  # tests only


def set_provider_override(provider: AIProvider | None) -> None:
    global _override
    _override = provider


def resolve_provider() -> tuple[AIProvider | None, AIStatus, str]:
    """Return (provider, status, message). Status is NOT_CONFIGURED / BLOCKED_BY_POLICY when unusable."""
    s = get_settings()
    if _override is not None:
        return _override, AIStatus.COMPLETED, "override"
    if s.ai_provider == "none":
        return None, AIStatus.NOT_CONFIGURED, "No AI provider is configured (AI_PROVIDER=none). Only deterministic checks were run."
    if s.ai_provider == "anthropic":
        if not s.anthropic_api_key:
            return None, AIStatus.NOT_CONFIGURED, "AI_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set."
        from .anthropic_provider import AnthropicProvider

        p: AIProvider = AnthropicProvider()
    else:
        if not (s.openai_base_url and s.openai_model):
            return None, AIStatus.NOT_CONFIGURED, "AI_PROVIDER=openai_compatible requires OPENAI_BASE_URL and OPENAI_MODEL."
        from .openai_compat import OpenAICompatProvider

        p = OpenAICompatProvider()
    if p.external and not s.ai_allow_external:
        return None, AIStatus.BLOCKED_BY_POLICY, (
            f"Provider '{p.name}' is external. Confidential PDFs are not sent to external AI providers until an "
            "administrator sets AI_ALLOW_EXTERNAL=true after organisational approval. Only deterministic checks were run.")
    return p, AIStatus.COMPLETED, "ok"
