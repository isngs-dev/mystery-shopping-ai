"""Always-current Excel QC report of every project, kept on disk without anybody pressing a button.

    backend/data/qc-reports/<Client>/<Project>.xlsx      (override with QC_REPORTS_DIR)

Whenever something changes — a form is synced from Drive, a QC analysis finishes, a reviewer accepts or rejects a finding, a project is
assigned — the project is marked dirty; a background worker waits a few seconds (so one sync of 100 forms means one rewrite per project)
and rewrites that project's workbook in the layout of the team's tracker (see project_excel). Downloads from the UI are always built
fresh, so they never depend on this worker; the files are the "live sheet" that fills itself.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from sqlalchemy import select

from ..config import BACKEND_DIR, get_settings
from ..db import SessionLocal
from ..models import Project

log = logging.getLogger("msai.qc_reports")
DEBOUNCE_SECONDS = 4.0
RETRY_SECONDS = 20.0


def reports_dir() -> Path:
    d = get_settings().qc_reports_dir or str(BACKEND_DIR / "data" / "qc-reports")
    return Path(d)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in " ._-" else "_" for c in (name or "").strip()).strip(" .") or "project"


def project_file(project: Project) -> Path:
    return reports_dir() / _safe(project.client.name) / f"{_safe(project.name)}.xlsx"


def write_project(project_id: int) -> Path | None:
    """Rebuild one project's workbook (own DB session). Raises if the file cannot be written (e.g. it is open in Excel)."""
    from .project_excel import build_project_excel

    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None or not project.is_active:
            return None
        data, _ = build_project_excel(db, project, None)
        path = project_file(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".xlsx.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)            # atomic: a reader never sees a half-written file
        return path
    finally:
        db.close()


class Worker:
    def __init__(self) -> None:
        self._dirty: set[int] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def mark_dirty(self, project_id: int | None) -> None:
        if project_id is None or not self.running:
            return
        with self._lock:
            self._dirty.add(project_id)
        self._wake.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def pending(self) -> int:
        with self._lock:
            return len(self._dirty)

    def _take(self) -> list[int]:
        with self._lock:
            ids, self._dirty = sorted(self._dirty), set()
        return ids

    def run_once(self) -> int:
        """Rewrite every dirty project; failures stay dirty for the next round. Returns the number written."""
        done = 0
        for pid in self._take():
            try:
                if write_project(pid):
                    done += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("QC report of project %s could not be written (%s); will retry", pid, exc)
                with self._lock:
                    self._dirty.add(pid)
        return done

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                self._wake.wait(timeout=60)
                if self._stop.is_set():
                    return
                self._wake.clear()
                if self._stop.wait(DEBOUNCE_SECONDS):      # let a burst of changes finish
                    return
                self.run_once()
                if self.pending() and self._stop.wait(RETRY_SECONDS):
                    return
                if self.pending():
                    self._wake.set()

        self._thread = threading.Thread(target=loop, name="qc-reports", daemon=True)
        self._thread.start()
        db = SessionLocal()
        try:
            for pid in db.execute(select(Project.id).where(Project.is_active.is_(True))).scalars():
                self.mark_dirty(pid)                       # make sure every project has an up-to-date file after a restart
        finally:
            db.close()
        log.info("QC report worker started; files go to %s", reports_dir())

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()


worker = Worker()


def mark_dirty(project_id: int | None) -> None:
    worker.mark_dirty(project_id)
