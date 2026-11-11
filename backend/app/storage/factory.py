from ..config import get_settings
from .base import StorageAdapter
from .local import LocalStorage
from .unconfigured import UnconfiguredStorage

_instance: StorageAdapter | None = None


def get_storage() -> StorageAdapter:
    global _instance
    if _instance is None:
        s = get_settings()
        _instance = LocalStorage(s.storage_root) if s.storage_backend == "local" else UnconfiguredStorage(s.storage_backend)
    return _instance


def reset_storage() -> None:
    global _instance
    _instance = None
