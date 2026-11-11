"""Storage adapter interface.

Keys mirror the production hierarchy:  <Workspace>/<Client>/<Project or Editor>/<FORMID>_<n>.pdf
Adapters must treat stored objects as immutable: `put_immutable` never overwrites.
If a provider cannot represent the folder hierarchy, the same logical hierarchy is kept in the
database (Client -> Project -> Form -> Document) and keys may be flattened by the adapter.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator


class StorageNotConfigured(RuntimeError):
    """Raised by adapters that need approved credentials which have not been supplied."""


class StorageAdapter(ABC):
    name: str = "abstract"

    @abstractmethod
    def put_immutable(self, key: str, data: bytes) -> None:
        """Store bytes at key. Must raise FileExistsError rather than overwrite."""

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list_keys(self, prefix: str = "") -> Iterator[str]: ...

    @abstractmethod
    def health(self) -> dict: ...


_SEG = re.compile(r"[^A-Za-z0-9._\- ]+")


def safe_segment(value: str) -> str:
    """Make a single path segment filesystem-safe (no separators, no traversal)."""
    cleaned = _SEG.sub("_", value.strip()).strip(" .") or "unnamed"
    return cleaned[:120]
