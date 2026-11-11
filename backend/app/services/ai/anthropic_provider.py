"""Anthropic (Claude) provider using the official SDK. External provider: gated by AI_ALLOW_EXTERNAL."""
from __future__ import annotations

from ...config import get_settings
from .base import AIError, AIProvider, AIResult

# Models that accept `output_config.effort`.
_EFFORT_PREFIXES = ("claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-haiku-5", "claude-opus-4-8",
                    "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6")


class AnthropicProvider(AIProvider):
    name = "anthropic"
    external = True

    def __init__(self) -> None:
        s = get_settings()
        self.model = s.anthropic_model
        self._key = s.anthropic_api_key
        self._timeout = s.ai_timeout_seconds

    def analyze(self, system: str, user: str) -> AIResult:
        import anthropic

        client = anthropic.Anthropic(api_key=self._key, timeout=self._timeout)
        kwargs: dict = {}
        if self.model.startswith(_EFFORT_PREFIXES):
            kwargs["output_config"] = {"effort": "medium"}
        try:
            with client.messages.stream(
                model=self.model, max_tokens=16000, system=system,
                messages=[{"role": "user", "content": user}], **kwargs,
            ) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise AIError("Anthropic rejected the API key (authentication failed).") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIError("The Anthropic API key lacks permission for this model.") from exc
        except anthropic.NotFoundError as exc:
            raise AIError(f"Anthropic model '{self.model}' was not found.") from exc
        except anthropic.RateLimitError as exc:
            raise AIError("Anthropic rate limit reached; retry later.") from exc
        except anthropic.BadRequestError as exc:
            raise AIError(f"Anthropic rejected the request: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("Could not reach the Anthropic API (network error).") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        if msg.stop_reason == "refusal":
            raise AIError("The model declined to analyse this content (safety refusal).")
        if msg.stop_reason == "max_tokens":
            raise AIError("The model response was truncated (max_tokens); analysis discarded.")
        text = "".join(b.text for b in msg.content if b.type == "text")
        usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
        return AIResult(text=text, model=msg.model or self.model, request_id=getattr(msg, "_request_id", None), usage=usage)
