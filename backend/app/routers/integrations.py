"""Google Drive import (ZIP / local folder / Drive API) and the read-only operations tracker."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form as FormField, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..deps import needs
from ..models import Form, ImportJob, MediaLink, TrackerEntry, User
from ..permissions import form_scope_clause
from ..serializers import iso
from ..services import drive_import, drive_sync, jobs
from ..services.drive_sources import SourceError, drive_status

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


def job_out(j: ImportJob) -> dict:
    return {"id": j.id, "source": j.source, "kind": j.kind, "status": j.status,
            "options": {k: v for k, v in (j.options or {}).items() if k != "zip_path"},
            "stats": j.stats or {}, "log": j.log or [], "error": j.error, "created_at": iso(j.created_at), "finished_at": iso(j.finished_at)}


def _opts(ai_edit_missing: bool, run_qc: bool, limit: int | None, apply_rules: bool = True) -> dict:
    return {"ai_edit_missing": ai_edit_missing, "run_qc": run_qc, "limit": limit, "apply_rules": apply_rules, "import_tracker": True}


def _start(db: Session, user: User, request: Request, *, source: str, kind: str, options: dict) -> ImportJob:
    busy = drive_sync.running_job(db)
    if busy:
        raise HTTPException(409, f"Import #{busy.id} is still running. Wait for it to finish.")
    job = ImportJob(source=source[:300], kind=kind, options=options, created_by=user.id)
    db.add(job)
    db.flush()
    audit(db, user, "import.started", entity_type="import_job", entity_id=job.id, detail={"source": source, "kind": kind}, request=request)
    db.commit()
    jobs.submit(drive_import.run_import_job, job.id)
    db.refresh(job)
    return job


@router.get("/drive/status")
def status(user: User = Depends(needs("integrations.manage"))):
    return drive_status()


@router.post("/drive/import-zip", status_code=202)
async def import_zip(request: Request, file: UploadFile = File(...), ai_edit_missing: bool = FormField(False), run_qc: bool = FormField(True),
                     limit: int | None = FormField(None), user: User = Depends(needs("integrations.manage")), db: Session = Depends(get_db)):
    """Import a ZIP downloaded from Drive (right-click the folder -> Download)."""
    s = get_settings()
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(422, "Upload the .zip file that Google Drive created when you downloaded the folder.")
    dest_dir = Path(s.import_local_root).resolve() / "_uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex}.zip"
    size, cap = 0, s.max_import_mb * 1024 * 1024
    with open(dest, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > cap:
                out.close()
                os.remove(dest)
                raise HTTPException(413, f"ZIP is larger than {s.max_import_mb} MB.")
            out.write(chunk)
    try:
        return job_out(_start(db, user, request, source=f"ZIP: {file.filename}", kind="zip",
                              options={**_opts(ai_edit_missing, run_qc, limit), "zip_path": str(dest), "filename": file.filename}))
    except HTTPException:
        os.remove(dest)
        raise


class LocalIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    ai_edit_missing: bool = False
    run_qc: bool = True
    limit: int | None = Field(default=None, ge=1, le=5000)


@router.post("/drive/import-local", status_code=202)
def import_local(body: LocalIn, request: Request, user: User = Depends(needs("integrations.manage")), db: Session = Depends(get_db)):
    """Import a folder placed inside IMPORT_LOCAL_ROOT (e.g. an unzipped or Drive-for-desktop copy of the folder)."""
    from ..services.drive_sources import LocalSource

    try:
        LocalSource(body.path).close()
    except SourceError as exc:
        raise HTTPException(422, str(exc)) from exc
    return job_out(_start(db, user, request, source=f"Local folder: {body.path}", kind="local",
                          options={**_opts(body.ai_edit_missing, body.run_qc, body.limit), "path": body.path}))


class SyncIn(BaseModel):
    folder_id: str | None = Field(default=None, max_length=200)
    ai_edit_missing: bool = False
    run_qc: bool = True
    limit: int | None = Field(default=None, ge=1, le=5000)


@router.post("/drive/sync", status_code=202)
def sync_google(body: SyncIn, request: Request, user: User = Depends(needs("integrations.manage")), db: Session = Depends(get_db)):
    """Pull the Drive folder through the Google Drive API (needs a service account with access to the folder)."""
    st = drive_status()
    folder = (body.folder_id or st["default_folder_id"] or "").strip()
    if not st["configured"]:
        raise HTTPException(409, st["help"])
    if not folder:
        raise HTTPException(422, "Provide the Drive folder id (or set GOOGLE_DRIVE_FOLDER_ID).")
    return job_out(_start(db, user, request, source=f"Google Drive folder {folder}", kind="google_drive",
                          options={**_opts(body.ai_edit_missing, body.run_qc, body.limit), "folder_id": folder}))


class AutoSyncIn(BaseModel):
    enabled: bool
    interval_minutes: int = Field(default=5, ge=1, le=1440)
    source: str = Field(default="google_drive", pattern="^(google_drive|local)$")
    folder_id: str | None = Field(default=None, max_length=200)
    local_path: str | None = Field(default=None, max_length=500)
    ai_edit_missing: bool = False
    ai_edit_after_hours: int = Field(default=24, ge=0, le=720)
    run_qc: bool = True
    max_forms_per_cycle: int = Field(default=200, ge=1, le=2000)


@router.get("/drive/auto")
def auto_status(user: User = Depends(needs("integrations.manage")), db: Session = Depends(get_db)):
    return drive_sync.public_status(db)


@router.put("/drive/auto")
def auto_configure(body: AutoSyncIn, request: Request, user: User = Depends(needs("integrations.manage")), db: Session = Depends(get_db)):
    """Turn automatic Drive sync on/off and set how it runs. Enabling validates the source first and starts a sync right away."""
    patch = body.model_dump()
    probe = {**drive_sync.load(db), **patch}
    if body.enabled:
        problem = drive_sync.validate_source(probe)
        if problem:
            raise HTTPException(409, problem)
        patch.update(enabled_by=user.id, next_run_at=None, last_error=None)    # due immediately
    drive_sync.save(db, patch)
    audit(db, user, "drive_sync.configured", entity_type="setting", entity_id=drive_sync.KEY,
          detail={k: v for k, v in patch.items() if k in ("enabled", "interval_minutes", "source", "ai_edit_missing", "ai_edit_after_hours", "run_qc")}, request=request)
    db.commit()
    return drive_sync.public_status(db)


@router.post("/drive/auto/run-now", status_code=202)
def auto_run_now(request: Request, user: User = Depends(needs("integrations.manage")), db: Session = Depends(get_db)):
    """Run one sync cycle immediately with the saved settings (works even when the schedule is switched off)."""
    cfg = drive_sync.load(db)
    problem = drive_sync.validate_source(cfg)
    if problem:
        raise HTTPException(409, problem)
    if drive_sync.running_job(db):
        raise HTTPException(409, "An import is already running.")
    job = drive_sync._job_for(cfg, user.id, auto=False)
    job.options = {**job.options, "auto": True}      # a manual cycle behaves like an automatic one (incremental, notifies admins)
    db.add(job)
    db.flush()
    audit(db, user, "import.started", entity_type="import_job", entity_id=job.id, detail={"source": job.source, "kind": job.kind, "manual_cycle": True}, request=request)
    db.commit()
    jobs.submit(drive_import.run_import_job, job.id)
    return job_out(job)


@router.get("/drive/jobs")
def list_jobs(user: User = Depends(needs("integrations.manage")), db: Session = Depends(get_db)):
    return [job_out(j) for j in db.execute(select(ImportJob).order_by(ImportJob.id.desc()).limit(20)).scalars()]


@router.get("/drive/jobs/{job_id}")
def get_job(job_id: int, user: User = Depends(needs("integrations.manage")), db: Session = Depends(get_db)):
    j = db.get(ImportJob, job_id)
    if not j:
        raise HTTPException(404, "Import job not found.")
    return job_out(j)


# ------------------------------------------------------------------------------------------------ tracker
@router.post("/tracker/import")
async def import_tracker_file(file: UploadFile = File(...), user: User = Depends(needs("integrations.manage")), db: Session = Depends(get_db)):
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(422, "Upload an .xlsx tracker.")
    data = await file.read(60 * 1024 * 1024)
    try:
        added, sheets = drive_import.import_tracker(db, file.filename, data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Could not read the workbook: {exc}") from exc
    audit(db, user, "tracker.imported", entity_type="tracker", entity_id=file.filename, detail={"rows_added": added, "sheets": sheets})
    db.commit()
    return {"rows_added": added, "sheets": sheets}


def _entry(e: TrackerEntry, form_ids: dict[str, int]) -> dict:
    return {"id": e.id, "sheet": e.sheet, "input_date": e.input_date, "form": e.form_name, "location": e.location, "shop_id": e.shop_id,
            "shop_date": e.shop_date, "editor": e.editor, "ops_status": e.ops_status, "comments_to_shopper": e.comments_to_shopper,
            "qc_member": e.qc_member, "qc_status": e.qc_status, "feedback": e.feedback, "error_category": e.error_category,
            "extra": e.data or {}, "form_id": form_ids.get(e.shop_id or "")}


@router.get("/tracker")
def tracker(q: str | None = None, sheet: str | None = None, form: str | None = None, editor: str | None = None, ops_status: str | None = None,
            limit: int = Query(50, le=200), offset: int = 0, user: User = Depends(needs("tracker.view")), db: Session = Depends(get_db)):
    stmt = select(TrackerEntry)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(TrackerEntry.shop_id.like(like), TrackerEntry.location.ilike(like), TrackerEntry.form_name.ilike(like),
                              TrackerEntry.comments_to_shopper.ilike(like)))
    for col, val in ((TrackerEntry.sheet, sheet), (TrackerEntry.form_name, form), (TrackerEntry.editor, editor), (TrackerEntry.ops_status, ops_status)):
        if val:
            stmt = stmt.where(col == val)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(TrackerEntry.id).limit(limit).offset(offset)).scalars().all()
    codes = {r.shop_id for r in rows if r.shop_id}
    form_ids = ({f.form_code: f.id for f in db.execute(select(Form).where(Form.form_code.in_(codes)).where(form_scope_clause(db, user))).scalars()}
                if codes else {})
    with_media = {fid for (fid,) in db.execute(select(MediaLink.form_id).where(MediaLink.form_id.in_(list(form_ids.values())))).all()} if form_ids else set()
    items = [_entry(r, form_ids) for r in rows]
    for it in items:
        it["has_media"] = it["form_id"] in with_media
    return {"total": total, "items": items}


@router.get("/tracker/summary")
def tracker_summary(user: User = Depends(needs("tracker.view")), db: Session = Depends(get_db)):
    def group(col):
        return [{"name": n or "—", "count": c} for n, c in
                db.execute(select(col, func.count()).group_by(col).order_by(func.count().desc()).limit(15)).all()]

    total = db.execute(select(func.count()).select_from(TrackerEntry)).scalar_one()
    return {"total": total, "by_status": group(TrackerEntry.ops_status), "by_form": group(TrackerEntry.form_name),
            "by_editor": group(TrackerEntry.editor), "by_sheet": group(TrackerEntry.sheet),
            "sources": [r[0] for r in db.execute(select(TrackerEntry.source_file).distinct()).all()]}
