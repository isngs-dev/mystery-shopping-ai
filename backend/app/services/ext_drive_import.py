"""Import the guideline / checklist documents of a Google Drive folder into the editing rules, without anyone uploading files by hand.

A Manager names a folder (a Drive link, or just its name, looked up under the platform's Drive folder) in the extension's Rules tab:
  * mode "client"  : every file is a client's checklist; the client is read from the file name ("CXE_Checklist" -> CXE, "Alta 360_Checklist" -> Alta 360);
                     a client the platform does not know yet is created; the rules apply to every form of that client;
  * mode "general" : every file holds guidance for ALL clients (writing standards, the editor's role ...); its rules carry no client.
Each file goes through the same AI step as an upload (dedupe by content, draft rules, nothing counted in QC before approval). The run is slow (an AI call per
file), so it happens in the background and the extension polls `status(job)`.
"""
from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from ..audit import audit
from ..config import get_settings
from ..db import SessionLocal
from ..models import User
from . import ext_guidelines
from .drive_sources import FOLDER_MIME, GoogleDriveSource, Node, SourceError, drive_folder_id
from .ext_text import _key

SHEET, DOC = "application/vnd.google-apps.spreadsheet", "application/vnd.google-apps.document"
SUPPORTED = ("application/pdf", DOC, SHEET, "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
MAX_FILE_BYTES = 40_000_000
MAX_FILES = 80
JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _open_drive(folder_id: str):
    """The Drive reader (patched in tests)."""
    return GoogleDriveSource(folder_id)


def client_from_name(filename: str) -> str:
    """"CXE_Checklist" -> "CXE"; "Alta 360_Checklist" -> "Alta 360"; "HSB Checklist Modified.docx" -> "HSB"; "CX Orlando_Bar Forms_Checklist" -> "CX Orlando"."""
    base = re.sub(r"\.(docx|pdf|xlsx|txt)$", "", filename.strip(), flags=re.I)
    first = base.split("_")[0]
    first = re.sub(r"\b(checklist|check list|modified|copy of|guidelines?)\b.*$", "", first, flags=re.I)
    return re.sub(r"\s+", " ", first).strip(" -_")


def _find_folder(src: GoogleDriveSource, name: str) -> Node | None:
    """A folder named like `name` inside the platform's Drive folder (two levels deep)."""
    want = _key(name)
    root = src.root()
    level = [root]
    for _ in range(2):
        nxt: list[Node] = []
        for node in level:
            for ch in src.children(node):
                if ch.is_dir:
                    if _key(ch.name) == want:
                        return ch
                    nxt.append(ch)
        level = nxt
    return None


def _walk(src: GoogleDriveSource, folder: Node, depth: int = 3) -> list[Node]:
    out: list[Node] = []
    for ch in src.children(folder):
        if ch.is_dir:
            if depth > 1:
                out.extend(_walk(src, ch, depth - 1))
        elif ch.mime in SUPPORTED or ch.name.lower().endswith((".pdf", ".docx", ".xlsx", ".txt")):
            out.append(ch)
    return out


def start(db, user: User, folder: str, mode: str, background) -> dict:
    """Validates, then runs the import in the background. Returns the job id to poll."""
    folder = (folder or "").strip()
    if not folder:
        return {"status": "bad_request", "message": "Type the Drive folder's link or name."}
    job = uuid.uuid4().hex[:10]
    with _LOCK:
        if any(j["state"] == "running" for j in JOBS.values()):
            return {"status": "busy", "message": "Another Drive import is still running. Wait for it to finish."}
        JOBS[job] = {"job": job, "state": "running", "mode": mode, "folder": folder, "total": 0, "files": [], "message": None,
                     "started": datetime.now(timezone.utc).isoformat(), "by": user.name}
        for old in sorted(JOBS)[:-15]:
            JOBS.pop(old, None)
    background.add_task(_run, job, user.id, folder, mode)
    return {"status": "started", "job": job}


def status(job: str) -> dict | None:
    return JOBS.get(job)


def _run(job: str, user_id: int, folder: str, mode: str) -> None:
    j = JOBS[job]
    db = SessionLocal()
    src = None
    try:
        user = db.get(User, user_id)
        s = get_settings()
        if re.search(r"/folders/|^[A-Za-z0-9_-]{25,}$", folder):
            fid = drive_folder_id(folder)
            src = _open_drive(fid)
            node = src.root()
        else:
            if not s.google_drive_folder_id:
                raise SourceError("The platform has no Drive folder configured, so a folder cannot be found by name. Paste the folder's link instead.")
            src = _open_drive(drive_folder_id(s.google_drive_folder_id))
            node = _find_folder(src, folder)
            if node is None:
                raise SourceError(f"No folder named “{folder}” was found inside the platform's Drive folder (is it shared with the service account?). Paste its link instead.")
            j["folder"] = node.name
        files = _walk(src, node)[:MAX_FILES]
        j["total"] = len(files)
        if not files:
            j["message"] = "No PDF, Word, Excel or text file was found in that folder."
        for f in files:
            row = {"name": f.name, "status": "reading", "rules": 0, "client": None, "message": None}
            j["files"].append(row)
            try:
                if f.size > MAX_FILE_BYTES:
                    raise ValueError("larger than 40 MB")
                content = src.read(f)
                if mode == "general":
                    out = ext_guidelines.learn(db, user, client_hint=None, project=None, sha256="", filename=f.name, source_url=None, content=content, general=True)
                else:
                    name = client_from_name(f.name)
                    out = ext_guidelines.upload(db, user, client_name=name, client_id=None, filename=f.name, content=content, scope="client", project=None,
                                                create=True, auto=False, can_create=True)
                row.update(status=out.get("status", "error"), rules=out.get("rules", 0), client=out.get("client"), message=out.get("message"),
                           created_client=bool(out.get("created_client")))
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
                db.rollback()
                row.update(status="error", message=f"{type(exc).__name__}: {exc}"[:300])
        audit(db, user, "web_guideline.drive_import", detail={"folder": j["folder"], "mode": mode, "files": len(files),
                                                              "rules": sum(r["rules"] for r in j["files"])})
        db.commit()
        j["state"] = "done"
    except Exception as exc:  # noqa: BLE001
        j["state"], j["message"] = "failed", str(exc)[:400]
    finally:
        if src is not None:
            src.close()
        db.close()
