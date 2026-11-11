"""Recordings (video/audio) linked to forms by ID.

The platform never copies recordings: it keeps a pointer (Drive file id, or a path inside the synced folder) and streams the
bytes on demand through an authenticated endpoint, so permissions follow the form's access rules and nothing is duplicated.

Where links come from
  * files in the synced Drive folder named by form ID (3353564.mp4, 3353564_call.m4a, ...) - found by the sync
  * Drive links in a tracker/sheet column (video / recording / audio / link ...) next to the ID
  * a link pasted on the form page
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Form, MediaLink, TrackerEntry, User
from .drive_sources import GoogleDriveSource, SourceError, _service_account_token_provider

VIDEO_EXT = {"mp4", "m4v", "mov", "webm", "mkv", "avi"}
AUDIO_EXT = {"mp3", "m4a", "wav", "ogg", "aac", "flac", "opus"}
MIME = {"mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "mkv": "video/x-matroska", "avi": "video/x-msvideo",
        "mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav", "ogg": "audio/ogg", "aac": "audio/aac", "flac": "audio/flac", "opus": "audio/ogg"}
DRIVE_HOSTS = {"drive.google.com", "docs.google.com"}
URL_RE = re.compile(r"https?://(?:drive|docs)\.google\.com/[^\s\"'<>]+", re.I)
MEDIA_HEADER = re.compile(r"video|record|audio|media|call|link|url", re.I)
MEDIA_DIR = re.compile(r"video|audio|record|media|call", re.I)


def ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_media_name(name: str) -> bool:
    return ext_of(name) in VIDEO_EXT | AUDIO_EXT


def kind_of(name: str) -> str:
    return "audio" if ext_of(name) in AUDIO_EXT else "video"


def parse_media_name(name: str) -> str | None:
    """Form ID from a recording's file name: '3353564.mp4', '3353564_call.m4a', 'FORM-001 interview.mp3'."""
    if not is_media_name(name):
        return None
    stem = name.rsplit(".", 1)[0].strip()
    m = re.match(r"^(\d{4,})", stem) or re.match(r"^([A-Za-z0-9\-]+?)(?:[_\s]|$)", stem)
    return m.group(1).upper() if m else None


def drive_file_id(url: str) -> str | None:
    """File id from a Drive share link (…/file/d/<id>/view, …open?id=<id>, …uc?id=<id>). Folder links are rejected."""
    try:
        u = urlparse(url.strip())
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in DRIVE_HOSTS or "/folders/" in u.path:
        return None
    m = re.search(r"/(?:file|document|presentation|spreadsheets)/d/([A-Za-z0-9_\-]{10,})", u.path)
    if m:
        return m.group(1)
    q = parse_qs(u.query).get("id")
    return q[0] if q and re.fullmatch(r"[A-Za-z0-9_\-]{10,}", q[0]) else None


def attach(db: Session, form: Form, *, title: str, kind: str, source: str, origin_key: str, mime: str = "", size: int = 0,
           drive_id: str | None = None, local_path: str | None = None, url: str | None = None, user: User | None = None) -> tuple[MediaLink, bool]:
    row = db.execute(select(MediaLink).where(MediaLink.form_id == form.id, MediaLink.origin_key == origin_key)).scalar_one_or_none()
    if row:
        return row, False
    row = MediaLink(form_id=form.id, title=title[:255], kind=kind, mime=mime, size=size, drive_file_id=drive_id, local_path=local_path, url=url,
                    source=source, origin_key=origin_key[:500], created_by=user.id if user else None)
    db.add(row)
    db.flush()
    return row, True


def reconcile_sheet_links(db: Session) -> int:
    """Attach Drive links found in imported tracker rows to the forms with the same ID (all projects that have that ID)."""
    added = 0
    rows = db.execute(select(TrackerEntry).where(TrackerEntry.shop_id.is_not(None))).scalars().all()
    wanted = {r.shop_id.upper(): r for r in rows if (r.data or {}).get("media_urls")}
    if not wanted:
        return 0
    forms = db.execute(select(Form).where(Form.form_code.in_(list(wanted)))).scalars().all()
    for f in forms:
        for url in wanted[f.form_code.upper()].data["media_urls"]:
            fid = drive_file_id(url)
            if not fid:
                continue
            _, new = attach(db, f, title=f"Recording for {f.form_code}", kind="video", source="sheet", origin_key=f"drive:{fid}", drive_id=fid, url=url)
            added += int(new)
    db.commit()
    return added


def media_urls_from_cells(headers: dict[int, str], cells: list) -> list[str]:
    """Drive links in cells whose column header suggests a recording."""
    out: list[str] = []
    for i, h in headers.items():
        if i < len(cells) and cells[i] and MEDIA_HEADER.search(h):
            out += [m.group(0).rstrip(".,;)") for m in URL_RE.finditer(str(cells[i]))]
    return list(dict.fromkeys(out))[:10]


# ---------------------------------------------------------------------------------------------- streaming
def open_drive_stream(file_id: str, range_header: str | None) -> tuple[httpx.Client, httpx.Response]:
    """Open a streaming, Range-capable download of a Drive file with the service account (read-only)."""
    token = _service_account_token_provider()()          # raises SourceError when Google is not configured
    client = httpx.Client(timeout=httpx.Timeout(30.0, read=None))
    headers = {"Authorization": f"Bearer {token}"}
    if range_header:
        headers["Range"] = range_header
    req = client.build_request("GET", f"{GoogleDriveSource.API}/files/{file_id}", params={"alt": "media", "supportsAllDrives": "true"}, headers=headers)
    try:
        resp = client.send(req, stream=True)
    except httpx.HTTPError as exc:
        client.close()
        raise SourceError(f"Could not reach Google Drive: {exc}") from exc
    return client, resp


def resolve_local(path: str) -> Path:
    """A synced-folder recording must live inside IMPORT_LOCAL_ROOT's tree (or the configured sync path); never an arbitrary file."""
    from ..config import get_settings

    base = Path(get_settings().import_local_root).resolve()
    p = Path(path).resolve()
    if base != p and base not in p.parents:
        raise SourceError("Recording is outside the allowed import folder.")
    if not p.is_file():
        raise SourceError("Recording file not found (was it removed from Drive?).")
    return p
