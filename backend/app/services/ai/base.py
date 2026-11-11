from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class AIError(RuntimeError):
    """Raised when the AI provider is configured but the call failed. Message is shown to users."""


@dataclass
class AIResult:
    text: str
    model: str
    request_id: str | None = None
    usage: dict = field(default_factory=dict)


class AIProvider(ABC):
    name: str = "abstract"
    model: str = ""
    external: bool = True  # does document text leave the organisation's boundary?

    @abstractmethod
    def analyze(self, system: str, user: str) -> AIResult: ...
