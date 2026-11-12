"""Automatic Drive sync: a small scheduler that polls the configured Drive folder and imports only what is new or changed.

How it fits together
  * Settings (enabled, interval, source, options) live in the database (AppSetting) so admins change them in the UI.
  * `tick()` is the whole decision: not enabled / not due / an import already running / source unusable -> do nothing;
    otherwise create an auto ImportJob and run it. It is safe to call as often as you like.
  * The importer is incremental (sync ledger), so each cycle downloads only new or changed files, runs QC on new pairs,
    AI-edits RAWs that stayed without an edited file for `ai_edit_after_hours`, and notifies admins.
  * Drive is polled (default every 5 minutes). Push notifications from Drive would need a public HTTPS webhook; polling
    needs nothing but read access.
Run ONE scheduler per deployment (one web worker, or DRIVE_SCHEDULER_ENABLED=false on the others).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import SessionLocal, utcnow
from ..models import AppSetting, ImportJob, User
from ..permissions import has_perm
from . import drive_import
from .drive_sources import LocalSource, SourceError, drive_status

log = logging.getLogger("msai.drive_sync")
KEY = "drive_auto_sync"
SOURCES = ("google_drive", "local")


def defaults() -> dict:
    s = get_settings()
    return {"enabled": bool(s.drive_auto_sync), "interval_minutes": max(1, s.drive_sync_interval_minutes), "source": "google_drive",
            "folder_id": s.google_drive_folder_id, "local_path": None, "ai_edit_missing": False, "ai_edit_after_hours": 24, "run_qc": True,
            "max_forms_per_cycle": 200, "enabled_by": None, "last_run_at": None, "next_run_at": None, "last_job_id": None,
            "last_error": None, "last_result": None}


def load(db: Session) -> dict:
    row = db.get(AppSetting, KEY)
    return {**defaults(), **(row.value if row else {})}


def save(db: Session, patch: dict) -> dict:
    cfg = {**load(db), **patch}
    row = db.get(AppSetting, KEY)
    if row is None:
        db.add(AppSetting(key=KEY, value=cfg))
    else:
        row.value = cfg
    db.commit()
    return cfg


def ensure_default_on() -> bool:
    """DRIVE_AUTO_SYNC=true means "always on": at start-up the sync is switched on (and QC with it) without anybody clicking, also after
    a database change or when the account that once enabled it is gone. Set DRIVE_AUTO_SYNC=false to leave it to the admin page."""
    if not get_settings().drive_auto_sync:
        return False
    db = SessionLocal()
    try:
        cfg = load(db)
        owner = db.get(User, cfg["enabled_by"]) if cfg["enabled_by"] else None
        owner_ok = owner is not None and owner.is_active and has_perm(owner, "integrations.manage")
        if cfg["enabled"] and cfg["run_qc"] and (owner_ok or not cfg["enabled_by"]):
            return False
        save(db, {"enabled": True, "run_qc": True, "last_error": None, "next_run_at": None, "enabled_by": cfg["enabled_by"] if owner_ok else None})
        return True
    finally:
        db.close()


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def validate_source(cfg: dict) -> str | None:
    """Return a user-facing problem with the configured source, or None when it is usable."""
    if cfg["source"] == "google_drive":
        st = drive_status()
        if not st["configured"]:
            return st["help"]
        if not (cfg.get("folder_id") or "").strip():
            return "Set the Drive folder id."
    elif cfg["source"] == "local":
        if not (cfg.get("local_path") or "").strip():
            return "Set the local folder path."
        try:
            LocalSource(cfg["local_path"]).close()
        except SourceError as exc:
            return str(exc)
    else:
        return "Unknown source."
    return None


def _job_for(cfg: dict, user_id: int, *, auto: bool) -> ImportJob:
    opts = {"auto": auto, "ai_edit_missing": cfg["ai_edit_missing"], "ai_edit_after_hours": cfg["ai_edit_after_hours"], "run_qc": cfg["run_qc"],
            "limit": cfg["max_forms_per_cycle"], "apply_rules": True, "import_tracker": True}
    if cfg["source"] == "google_drive":
        return ImportJob(source=f"Google Drive folder {cfg['folder_id']}" + (" (auto)" if auto else ""), kind="google_drive",
                         options={**opts, "folder_id": cfg["folder_id"]}, created_by=user_id)
    return ImportJob(source=f"Local folder {cfg['local_path']}" + (" (auto)" if auto else ""), kind="local",
                     options={**opts, "path": cfg["local_path"]}, created_by=user_id)


STALE_JOB_HOURS = 3     # an import that has been "running" this long lost its worker (crash, killed process)


def _fail_interrupted(job: ImportJob, why: str) -> None:
    job.status, job.error, job.finished_at = "failed", why, utcnow()


def running_job(db: Session) -> ImportJob | None:
    """The import that is genuinely in progress. A job whose worker died would otherwise block every later sync forever."""
    cutoff = utcnow() - timedelta(hours=STALE_JOB_HOURS)
    live = None
    for job in db.execute(select(ImportJob).where(ImportJob.status.in_(["queued", "running"]))).scalars():
        if job.created_at and job.created_at < cutoff:
            _fail_interrupted(job, f"Import stopped responding after {STALE_JOB_HOURS} hours and was closed. A new sync will pick up the remaining files.")
        else:
            live = live or job
    db.commit()
    return live


def recover_interrupted_jobs() -> int:
    """On startup nothing can still be running: close imports a stopped server left open and let the next tick sync at once."""
    db = SessionLocal()
    try:
        rows = db.execute(select(ImportJob).where(ImportJob.status.in_(["queued", "running"]))).scalars().all()
        for job in rows:
            _fail_interrupted(job, "Interrupted by a server restart. The next sync continues from where it stopped.")
        if rows:
            row = db.get(AppSetting, KEY)
            if row is not None:
                row.value = {**row.value, "next_run_at": None}
        db.commit()
        return len(rows)
    finally:
        db.close()


def _record_result(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(ImportJob, job_id)
        st = {k: v for k, v in (job.stats or {}).items() if isinstance(v, int)}
        save(db, {"last_error": job.error if job.status == "failed" else None,
                  "last_result": {"job_id": job.id, "status": job.status, "finished_at": job.finished_at.isoformat() if job.finished_at else None, "stats": st}})
    finally:
        db.close()


def tick(now: datetime | None = None) -> int | None:
    """One scheduler decision. Returns the id of the import job it ran, or None when there was nothing to do."""
    now = now or utcnow()
    db = SessionLocal()
    try:
        cfg = load(db)
        if not cfg["enabled"]:
            return None
        nxt = _dt(cfg["next_run_at"])
        if nxt and now < nxt:
            return None
        if running_job(db):
            return None
        interval = timedelta(minutes=max(1, int(cfg["interval_minutes"])))
        user = db.get(User, cfg["enabled_by"]) if cfg["enabled_by"] else db.execute(
            select(User).where(User.role == "manager", User.is_active.is_(True)).order_by(User.id)).scalars().first()
        if user is None or not user.is_active or not has_perm(user, "integrations.manage"):
            save(db, {"enabled": False, "last_error": "Auto-sync was switched off: the account that enabled it is no longer an active user with Drive import access."})
            return None
        problem = validate_source(cfg)
        if problem:
            save(db, {"last_error": problem, "last_run_at": now.isoformat(), "next_run_at": (now + interval).isoformat()})
            return None
        job = _job_for(cfg, user.id, auto=True)
        db.add(job)
        db.flush()
        save(db, {"last_run_at": now.isoformat(), "next_run_at": (now + interval).isoformat(), "last_job_id": job.id, "last_error": None})
        audit(db, user, "import.started", entity_type="import_job", entity_id=job.id, detail={"source": job.source, "kind": job.kind, "auto": True})
        db.commit()
        job_id = job.id
    finally:
        db.close()
    drive_import.run_import_job(job_id)     # synchronous: this is the scheduler's own thread
    _record_result(job_id)
    return job_id


def public_status(db: Session) -> dict:
    cfg = load(db)
    job = running_job(db)
    nxt = _dt(cfg["next_run_at"])
    return {**{k: v for k, v in cfg.items() if k != "enabled_by"},
            "enabled_by": (db.get(User, cfg["enabled_by"]).name if cfg["enabled_by"] and db.get(User, cfg["enabled_by"]) else None),
            "running_job_id": job.id if job else None, "seconds_until_next": max(0, int((nxt - utcnow()).total_seconds())) if (nxt and cfg["enabled"]) else None,
            "scheduler_running": scheduler.running, "google": drive_status()}


class Scheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, poll_seconds: float = 20.0, first_delay: float = 5.0) -> None:
        if self.running:
            return
        self._stop.clear()

        def loop() -> None:
            if self._stop.wait(first_delay):
                return
            while True:
                try:
                    tick()
                except Exception:  # noqa: BLE001
                    log.exception("Drive auto-sync tick failed")
                if self._stop.wait(poll_seconds):
                    return

        self._thread = threading.Thread(target=loop, name="drive-sync", daemon=True)
        self._thread.start()
        log.info("Drive auto-sync scheduler started")

    def stop(self) -> None:
        self._stop.set()


scheduler = Scheduler()
