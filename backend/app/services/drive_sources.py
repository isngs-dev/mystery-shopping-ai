"""Where a Drive folder tree can come from. All sources expose the same read-only tree interface.

  ZipSource          a ZIP downloaded from Drive ("Download" on the folder) - needs no credentials
  LocalSource        a folder on disk (e.g. a Google Drive for desktop mirror), restricted to IMPORT_LOCAL_ROOT
  GoogleDriveSource  the Drive REST API with a service account (read-only scope); share the folder with its e-mail

Nothing here writes to Drive.
"""
from __future__ import annotations

import json
import re
import time
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

from ..config import get_settings


class SourceError(RuntimeError):
    """Raised with a message that is safe to show the user."""


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    is_dir: bool
    size: int = 0
    mime: str = ""
    version: str = ""   # fingerprint that changes when the file's content changes


class Source(ABC):
    kind = "abstract"
    label = ""
    key = ""   # stable identity of the source (used by the sync ledger)

    @abstractmethod
    def root(self) -> Node: ...

    @abstractmethod
    def children(self, node: Node) -> list[Node]: ...

    @abstractmethod
    def read(self, node: Node) -> bytes: ...

    def close(self) -> None:
        pass


# ----------------------------------------------------------------------------------------------- ZIP
class ZipSource(Source):
    kind = "zip"

    def __init__(self, path: str, label: str | None = None):
        self.label = label or Path(path).name
        self.key = self.label
        try:
            self._zip = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise SourceError("The uploaded file is not a valid ZIP archive.") from exc
        self._files: dict[str, zipfile.ZipInfo] = {}
        self._dirs: set[str] = {""}
        for info in self._zip.infolist():
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if not parts or ".." in parts or name.startswith("/") or parts[0] == "__MACOSX" or parts[-1].startswith("._"):
                continue  # zip-slip / macOS resource forks
            for i in range(1, len(parts) if not info.is_dir() else len(parts) + 1):
                self._dirs.add("/".join(parts[:i]))
            if not info.is_dir():
                self._files[name.rstrip("/")] = info
        top = {p.split("/")[0] for p in list(self._files) + list(self._dirs) if p}
        # Drive wraps the downloaded folder in one top-level directory: unwrap it
        self._base = (next(iter(top)) + "/") if len(top) == 1 and next(iter(top)) in self._dirs and not any("/" not in f for f in self._files) else ""
        self._rootname = self._base.rstrip("/") or self.label

    def root(self) -> Node:
        return Node(self._base.rstrip("/"), self._rootname, True)

    def children(self, node: Node) -> list[Node]:
        prefix = (node.id + "/") if node.id else ""
        out: dict[str, Node] = {}
        for d in self._dirs:
            if d.startswith(prefix) and d != node.id and "/" not in d[len(prefix):] and d:
                out[d] = Node(d, d[len(prefix):], True)
        for f, info in self._files.items():
            if f.startswith(prefix) and "/" not in f[len(prefix):]:
                out[f] = Node(f, f[len(prefix):], False, info.file_size, version=f"{info.file_size}:{info.CRC}")
        return sorted(out.values(), key=lambda n: (not n.is_dir, n.name.lower()))

    def read(self, node: Node) -> bytes:
        info = self._files[node.id]
        if info.file_size > get_settings().max_upload_bytes * 4:
            raise SourceError(f"{node.name} is too large to import safely.")
        with self._zip.open(info) as fh:
            return fh.read(info.file_size + 1)

    def close(self) -> None:
        self._zip.close()


# ----------------------------------------------------------------------------------------------- local folder
class LocalSource(Source):
    kind = "local"

    def __init__(self, path: str):
        base = Path(get_settings().import_local_root).resolve()
        p = Path(path)
        p = (base / p).resolve() if not p.is_absolute() else p.resolve()
        if base != p and base not in p.parents:
            raise SourceError(f"Only folders inside {base} can be imported. Copy or unzip the Drive folder there first.")
        if not p.is_dir():
            raise SourceError(f"Folder not found: {p}")
        self._root, self.label = p, p.name
        self.key = str(p)

    def root(self) -> Node:
        return Node(str(self._root), self._root.name, True)

    def children(self, node: Node) -> list[Node]:
        out = []
        for c in sorted(Path(node.id).iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if c.name.startswith((".", "~$")) or c.is_symlink():
                continue
            st = c.stat()
            out.append(Node(str(c), c.name, c.is_dir(), st.st_size if c.is_file() else 0, version=f"{st.st_size}:{st.st_mtime_ns}" if c.is_file() else ""))
        return out

    def read(self, node: Node) -> bytes:
        p = Path(node.id).resolve()
        if self._root != p and self._root not in p.parents:
            raise SourceError("Path escapes the import folder.")
        return p.read_bytes()


# ----------------------------------------------------------------------------------------------- Google Drive API
FOLDER_MIME = "application/vnd.google-apps.folder"
EXPORTS = {"application/vnd.google-apps.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
           "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def drive_folder_id(text: str | None) -> str:
    """The folder ID from whatever was pasted: the bare ID or a Drive link such as https://drive.google.com/drive/u/0/folders/<ID>?usp=sharing."""
    s = (text or "").strip()
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", s) or re.search(r"[?&]id=([A-Za-z0-9_-]+)", s)
    return m.group(1) if m else s


class GoogleDriveSource(Source):
    kind = "google_drive"
    API = "https://www.googleapis.com/drive/v3"

    def __init__(self, folder_id: str, token_provider=None, client: httpx.Client | None = None):
        folder_id = drive_folder_id(folder_id)
        self.folder_id = folder_id
        self.label = f"Google Drive folder {folder_id}"
        self.key = folder_id
        self._token_provider = token_provider or _service_account_token_provider()
        self._http = client or httpx.Client(timeout=120)
        self._name: str | None = None

    def _get(self, path: str, **params) -> httpx.Response:
        for attempt in range(4):
            r = self._http.get(f"{self.API}{path}", params=params, headers={"Authorization": f"Bearer {self._token_provider()}"})
            if r.status_code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        if r.status_code == 404:
            raise SourceError("Drive folder/file not found, or it is not shared with the service account.")
        if r.status_code in (401, 403):
            raise SourceError(f"Google Drive refused access ({r.status_code}). Share the folder with the service account's e-mail address.")
        if r.status_code >= 400:
            raise SourceError(f"Google Drive error {r.status_code}.")
        return r

    def root(self) -> Node:
        if self._name is None:
            self._name = self._get(f"/files/{self.folder_id}", fields="name", supportsAllDrives="true").json().get("name", "Drive")
        return Node(self.folder_id, self._name, True)

    def children(self, node: Node) -> list[Node]:
        out: list[Node] = []
        token = None
        while True:
            params = {"q": f"'{node.id}' in parents and trashed = false", "fields": "nextPageToken, files(id,name,mimeType,size,md5Checksum,modifiedTime)",
                      "pageSize": 1000, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "orderBy": "folder,name"}
            if token:
                params["pageToken"] = token
            data = self._get("/files", **params).json()
            for f in data.get("files", []):
                out.append(Node(f["id"], f["name"], f["mimeType"] == FOLDER_MIME, int(f.get("size", 0) or 0), f["mimeType"],
                                version=f.get("md5Checksum") or f.get("modifiedTime", "")))
            token = data.get("nextPageToken")
            if not token:
                return out

    def read(self, node: Node) -> bytes:
        if node.mime in EXPORTS:
            return self._get(f"/files/{node.id}/export", mimeType=EXPORTS[node.mime]).content
        return self._get(f"/files/{node.id}", alt="media", supportsAllDrives="true").content

    def close(self) -> None:
        self._http.close()


def drive_status() -> dict:
    s = get_settings()
    info = _load_service_account_info(silent=True)
    return {"configured": bool(info), "service_account_email": (info or {}).get("client_email"), "default_folder_id": s.google_drive_folder_id,
            "local_import_root": s.import_local_root,
            "help": None if info else "Set GOOGLE_SERVICE_ACCOUNT_FILE (or _JSON), then share the Drive folder with the service account's e-mail address."}


def _load_service_account_info(silent: bool = False) -> dict | None:
    s = get_settings()
    try:
        if s.google_service_account_json:
            return json.loads(s.google_service_account_json)
        if s.google_service_account_file:
            return json.loads(Path(s.google_service_account_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if not silent:
            raise SourceError(f"Could not read the Google service-account key: {exc}") from exc
    return None


def _service_account_token_provider():
    info = _load_service_account_info()
    if not info:
        raise SourceError("Google Drive is not configured. Set GOOGLE_SERVICE_ACCOUNT_FILE (or GOOGLE_SERVICE_ACCOUNT_JSON) and share the folder "
                          "with the service account's e-mail. Alternatively download the folder as a ZIP and import that.")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover
        raise SourceError("Install google-auth and requests (pip install google-auth requests) to use the Google Drive API.") from exc
    creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/drive.readonly"])

    def token() -> str:
        if not creds.valid:
            creds.refresh(Request())
        return creds.token

    return token
