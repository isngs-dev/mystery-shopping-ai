import os
import stat
from collections.abc import Iterator
from pathlib import Path

from .base import StorageAdapter


class LocalStorage(StorageAdapter):
    """Development storage on the local disk (authorised local adapter)."""

    name = "local"

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root != p and self.root not in p.parents:
            raise ValueError("Illegal storage key.")
        return p

    def put_immutable(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "xb") as fh:  # 'x' => fail if the file exists; originals are never overwritten
            fh.write(data)
        try:
            os.chmod(p, stat.S_IREAD)
        except OSError:
            pass

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str = "") -> Iterator[str]:
        base = self._path(prefix) if prefix else self.root
        if not base.exists():
            return
        for p in base.rglob("*"):
            if p.is_file():
                yield p.relative_to(self.root).as_posix()

    def health(self) -> dict:
        return {"adapter": self.name, "configured": True, "root": str(self.root), "writable": os.access(self.root, os.W_OK)}
