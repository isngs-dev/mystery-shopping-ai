from collections.abc import Iterator

from .base import StorageAdapter, StorageNotConfigured


class UnconfiguredStorage(StorageAdapter):
    """Placeholder for shared-drive / cloud providers. NOT IMPLEMENTED: it needs approved credentials
    and an agreed provider. Selecting it makes every operation fail loudly instead of pretending."""

    def __init__(self, name: str):
        self.name = name

    def _fail(self):
        raise StorageNotConfigured(
            f"Storage backend '{self.name}' is selected but not implemented/configured. "
            "Implement StorageAdapter for the approved provider and supply credentials via environment variables."
        )

    def put_immutable(self, key: str, data: bytes) -> None:
        self._fail()

    def get(self, key: str) -> bytes:
        self._fail()

    def exists(self, key: str) -> bool:
        self._fail()

    def list_keys(self, prefix: str = "") -> Iterator[str]:
        self._fail()
        yield ""

    def health(self) -> dict:
        return {"adapter": self.name, "configured": False,
                "detail": "Requires approved credentials and an adapter implementation."}
