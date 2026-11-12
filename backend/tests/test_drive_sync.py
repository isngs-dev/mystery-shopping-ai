"""Automatic Drive sync: new / changed files in the Drive folder show up in the platform without anyone clicking anything."""
import shutil
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import (Client, Document, DriveFile, ExceptionItem, Form, ImportJob, Notification, Project, ProjectReviewer, QCRun,
                        UserProjectAccess)
from app.services import drive_sync

from .test_drive_import import ED_N, RAW_N, review_pdf

NARR_V2 = "The teller did not greet me and she charged $100 today. She said, hi welcome to Valley. She was polite."


def assign_to_qc(ctx, assigned=True):
    """The Drive project “APC / Valley Bank_Geeta” exists and (by default) belongs to the QC lead before the first sync."""
    db = SessionLocal()
    client = Client(name="APC", folder="APC")
    db.add(client)
    db.flush()
    p = Project(client_id=client.id, name="Valley Bank_Geeta", folder="Valley_Bank_Geeta")
    db.add(p)
    db.flush()
    for who in ("manager", "admin"):                      # what a Drive import grants to the working team
        db.add(UserProjectAccess(user_id=ctx["users"][who], project_id=p.id))
    if assigned:
        db.add(UserProjectAccess(user_id=ctx["users"]["qc"], project_id=p.id))
        db.add(ProjectReviewer(project_id=p.id, reviewer_id=ctx["users"]["qc"]))
    db.commit()
    pid = p.id
    db.close()
    return pid


@pytest.fixture
def drive(ctx):
    """A 'Drive folder' on disk (what Google Drive for desktop mirrors) inside IMPORT_LOCAL_ROOT."""
    root = Path(get_settings().import_local_root) / "drive_a"
    shutil.rmtree(root, ignore_errors=True)
    proj = root / "APC" / "Valley Bank_Geeta"
    proj.mkdir(parents=True)

    def put(name: str, sid: str, narrative: str) -> None:
        (proj / name).write_bytes(review_pdf(sid, narrative))
        time.sleep(0.02)

    put("9910001_1.pdf", "9910001", RAW_N)
    put("9910001_2.pdf", "9910001", ED_N)
    put("9910002_1.pdf", "9910002", RAW_N)
    put("9910002_2.pdf", "9910002", ED_N)
    put("9910003_1.pdf", "9910003", RAW_N)            # RAW only: its edited file has not arrived yet
    put.proj, put.root = proj, root
    assign_to_qc(ctx)                                  # QC only runs for projects that were handed to a QC lead
    return put


def enable(sa, **kw):
    body = {"enabled": True, "interval_minutes": 5, "source": "local", "local_path": "drive_a", "run_qc": True, "ai_edit_missing": False, **kw}
    r = sa.put("/api/integrations/drive/auto", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def job(jid):
    db = SessionLocal()
    try:
        j = db.get(ImportJob, jid)
        return {"status": j.status, "stats": j.stats, "error": j.error, "options": j.options}
    finally:
        db.close()


def test_new_and_changed_files_flow_in_automatically(api, ctx, drive):
    sa = api("manager")       # only the manager runs Drive sync
    st = enable(sa)
    assert st["enabled"] and st["enabled_by"] == "MANAGER" and st["source"] == "local"
    t0 = utcnow()

    j1 = job(drive_sync.tick(t0))                                           # --- first cycle: everything is new
    assert j1["status"] == "completed" and j1["options"]["auto"] is True
    assert j1["stats"]["raw_imported"] == 3 and j1["stats"]["edited_imported"] == 2 and j1["stats"]["qc_runs"] == 2
    assert drive_sync.tick(t0 + timedelta(minutes=1)) is None               # --- not due yet: nothing happens

    j2 = job(drive_sync.tick(t0 + timedelta(minutes=6)))                    # --- due, but nothing changed in Drive
    assert j2["stats"].get("raw_imported", 0) == 0 and j2["stats"]["already_imported"] == 3 and j2["stats"].get("qc_runs", 0) == 0

    drive("9910004_1.pdf", "9910004", RAW_N)                                # --- a new pair + the late edited file of 9910003
    drive("9910004_2.pdf", "9910004", ED_N)
    drive("9910003_2.pdf", "9910003", ED_N)
    j3 = job(drive_sync.tick(t0 + timedelta(minutes=12)))
    assert j3["stats"]["raw_imported"] == 1 and j3["stats"]["edited_imported"] == 2 and j3["stats"]["qc_runs"] == 2 and j3["stats"]["already_imported"] == 2
    db = SessionLocal()
    forms = {f.form_code: f for f in db.scalars(select(Form))}
    assert set(forms) == {"9910001", "9910002", "9910003", "9910004"} and all(f.status == "qc_review" for f in forms.values())
    assert [d.kind for d in forms["9910003"].documents].count("edited") == 1
    db.close()

    drive("9910001_2.pdf", "9910001", NARR_V2)                              # --- the editor revised an edited PDF in Drive
    j4 = job(drive_sync.tick(t0 + timedelta(minutes=18)))
    assert j4["stats"]["edited_resubmitted"] == 1 and j4["stats"]["qc_runs"] == 1 and j4["stats"].get("raw_imported", 0) == 0
    d = sa.get(f"/api/forms/{forms['9910001'].id}").json()
    names = sorted(x["stored_name"] for x in d["documents"])
    assert d["edited_versions"] == 2 and names[0] == "9910001_1.pdf" and names[1] == "9910001_2.pdf" and names[2].startswith("9910001_2_r2_")   # history kept

    (drive.proj / "9910002_1.pdf").write_bytes(review_pdf("9910002", "A completely different RAW."))      # --- a RAW is tampered with in Drive
    j5 = job(drive_sync.tick(t0 + timedelta(minutes=24)))
    assert j5["stats"]["raw_changed"] == 1
    j6_id = drive_sync.tick(t0 + timedelta(minutes=30))                     # the same problem is reported once, not every cycle
    assert job(j6_id)["stats"].get("raw_changed", 0) == 0
    db = SessionLocal()
    raw = db.scalars(select(Document).where(Document.stored_name == "9910002_1.pdf")).one()
    assert "A completely different RAW" not in "".join(p.text for p in raw.pages)                          # immutable original kept
    assert db.scalars(select(ExceptionItem).where(ExceptionItem.issue_type == "raw_changed")).all().__len__() == 1
    # admins were told about the new documents
    msgs = [n.message for n in db.scalars(select(Notification).where(Notification.kind == "drive_sync"))]
    assert any("Drive sync:" in m and "new RAW" in m for m in msgs)
    cfg = drive_sync.load(db)
    assert cfg["last_result"]["status"] == "completed" and cfg["last_error"] is None and cfg["last_job_id"] == j6_id and db.scalar(select(DriveFile.id).limit(1))
    db.close()
    st = sa.get("/api/integrations/drive/auto").json()
    assert st["enabled"] and st["last_result"]["stats"]["already_imported"] >= 4 and st["running_job_id"] is None


def test_forms_are_analysed_before_the_project_is_assigned(api, ctx, drive):
    db = SessionLocal()
    db.query(ProjectReviewer).delete()                                      # the coordinator has not handed the project over yet
    db.commit()
    pid = db.query(Project).filter_by(name="Valley Bank_Geeta").one().id
    db.close()
    sa = api("manager")
    enable(sa)
    j1 = job(drive_sync.tick(utcnow()))
    assert j1["stats"]["raw_imported"] == 3 and j1["stats"]["edited_imported"] == 2 and j1["stats"]["qc_runs"] == 2       # QC does not wait
    db = SessionLocal()
    assert db.query(QCRun).count() == 2 and all(f.assigned_reviewer_id is None for f in db.scalars(select(Form)))
    db.close()
    r = sa.put(f"/api/qc/projects/{pid}/reviewer", json={"reviewer_id": ctx["users"]["qc"]})              # the coordinator assigns the project
    assert r.status_code == 200 and r.json()["analysis"]["queued"] == 0 and r.json()["analysis"]["already_done"] == 2
    db = SessionLocal()
    assert all(f.assigned_reviewer_id == ctx["users"]["qc"] for f in db.scalars(select(Form)))
    db.close()


def test_forms_without_any_analysis_are_caught_up_by_the_next_sync(api, ctx, drive):
    enable(api("manager"))
    t0 = utcnow()
    assert job(drive_sync.tick(t0))["stats"]["qc_runs"] == 2
    db = SessionLocal()
    f1 = db.scalars(select(Form).where(Form.form_code == "9910001")).one()
    for r in db.scalars(select(QCRun).where(QCRun.form_id == f1.id)).all():          # e.g. a form synced while QC was switched off for it
        for f in r.findings:
            db.delete(f)
        db.delete(r)
    db.commit()
    f1_id = f1.id
    db.close()
    j = job(drive_sync.tick(t0 + timedelta(minutes=6)))
    assert j["stats"]["qc_runs"] == 1
    db = SessionLocal()
    assert db.query(QCRun).filter(QCRun.form_id == f1_id).count() == 1
    db.close()


def test_missing_edited_file_waits_then_ai_edits_and_a_human_file_still_wins(api, ctx, drive):
    sa = api("manager")       # only the manager runs Drive sync
    enable(sa, ai_edit_missing=True, ai_edit_after_hours=24)
    t0 = utcnow()
    j1 = job(drive_sync.tick(t0))
    assert j1["stats"].get("ai_edited", 0) == 0                              # RAW 9910003 is brand new: give the human editor time
    db = SessionLocal()
    f3 = db.scalars(select(Form).where(Form.form_code == "9910003")).one()
    assert not [d for d in f3.documents if d.kind == "edited"]
    raw = next(d for d in f3.documents if d.kind == "raw")
    raw.uploaded_at = utcnow() - timedelta(hours=25)                         # 25 hours later and still no edited file
    db.commit()
    db.close()
    j2 = job(drive_sync.tick(t0 + timedelta(minutes=6)))
    assert j2["stats"]["ai_edited"] == 1
    db = SessionLocal()
    f3 = db.scalars(select(Form).where(Form.form_code == "9910003")).one()
    assert [d.origin for d in f3.documents if d.kind == "edited"] == ["ai_assisted"] and f3.status == "qc_review"
    db.close()
    drive("9910003_2.pdf", "9910003", ED_N)                                  # the human editor's file finally arrives
    j3 = job(drive_sync.tick(t0 + timedelta(minutes=12)))
    assert j3["stats"]["edited_resubmitted"] == 1 and j3["stats"].get("ai_edited", 0) == 0
    db = SessionLocal()
    f3 = db.scalars(select(Form).where(Form.form_code == "9910003")).one()
    eds = sorted((d.version_no, d.origin) for d in f3.documents if d.kind == "edited")
    assert eds == [(1, "ai_assisted"), (2, "human")]                         # AI version kept in history; the human edit is the latest
    assert db.scalars(select(QCRun).where(QCRun.form_id == f3.id)).all().__len__() >= 2
    db.close()


def test_off_busy_invalid_and_permissions(api, ctx, drive):
    sa = api("manager")       # only the manager runs Drive sync
    assert sa.get("/api/integrations/drive/auto").json()["enabled"] is False and drive_sync.tick() is None
    for who in ("admin", "sa"):
        assert api(who).put("/api/integrations/drive/auto", json={"enabled": True, "source": "local", "local_path": "drive_a"}).status_code == 403
        assert api(who).get("/api/integrations/drive/auto").status_code == 403
    r = sa.put("/api/integrations/drive/auto", json={"enabled": True, "source": "google_drive", "folder_id": "abc"})
    assert r.status_code == 409 and "service account" in r.json()["detail"].lower()                   # honest: Google is not connected
    assert sa.put("/api/integrations/drive/auto", json={"enabled": True, "source": "local", "local_path": "C:/Windows"}).status_code == 409
    assert sa.put("/api/integrations/drive/auto", json={"enabled": True, "source": "local", "local_path": "drive_a", "interval_minutes": 0}).status_code == 422
    assert sa.put("/api/integrations/drive/auto", json={"enabled": False, "source": "ftp"}).status_code == 422

    enable(sa)
    db = SessionLocal()
    busy = ImportJob(source="x", kind="local", status="running", options={}, created_by=1)
    db.add(busy)
    db.commit()
    t0 = utcnow()
    assert drive_sync.tick(t0) is None                                       # an import is running: the scheduler waits
    busy = db.get(ImportJob, busy.id)
    busy.status = "completed"
    db.commit()
    db.close()
    assert drive_sync.tick(t0) is not None                                   # then runs

    # manual "sync now" works with the saved settings, even when the schedule is off
    sa.put("/api/integrations/drive/auto", json={"enabled": False, "source": "local", "local_path": "drive_a"})
    r = sa.post("/api/integrations/drive/auto/run-now")
    assert r.status_code == 202 and job(r.json()["id"])["status"] == "completed"
    assert drive_sync.tick(t0 + timedelta(hours=1)) is None                  # still off


def test_a_dead_import_does_not_block_the_sync_forever(api, ctx, drive):
    enable(api("manager"))
    db = SessionLocal()
    old = ImportJob(source="x", kind="local", status="running", options={}, created_by=1, created_at=utcnow() - timedelta(hours=4))
    db.add(old)
    db.commit()
    assert drive_sync.running_job(db) is None                                # older than the limit: closed, not waited for
    db.refresh(old)
    assert old.status == "failed" and "stopped responding" in old.error
    assert drive_sync.tick(utcnow()) is not None                             # so the next cycle syncs

    fresh = ImportJob(source="y", kind="local", status="running", options={}, created_by=1)
    db.add(fresh)
    db.commit()
    assert drive_sync.recover_interrupted_jobs() == 1                        # a restart closes it and schedules a sync at once
    db.refresh(fresh)
    assert fresh.status == "failed" and "restart" in fresh.error
    assert drive_sync.load(db)["next_run_at"] is None
    db.close()
    assert drive_sync.tick(utcnow()) is not None


def test_auto_sync_stops_if_the_enabling_user_is_deactivated(api, ctx, drive):
    mgr = api("manager")
    assert mgr.put("/api/integrations/drive/auto", json={"enabled": True, "source": "local", "local_path": "drive_a"}).status_code == 200
    assert api("sa").patch(f"/api/users/{ctx['users']['manager']}", json={"is_active": False}).status_code == 200
    assert drive_sync.tick() is None
    db = SessionLocal()
    st = drive_sync.load(db)                    # the Super Admin cannot open the Drive page, so read the saved setting directly
    db.close()
    assert st["enabled"] is False and "no longer an active user" in st["last_error"]


def test_scheduler_thread_ticks_and_stops(monkeypatch):
    calls = []
    monkeypatch.setattr(drive_sync, "tick", lambda *a, **k: calls.append(1))
    sched = drive_sync.Scheduler()
    sched.start(poll_seconds=0.02, first_delay=0.01)
    deadline = time.time() + 3
    while len(calls) < 3 and time.time() < deadline:
        time.sleep(0.02)
    sched.stop()
    time.sleep(0.1)
    assert len(calls) >= 3 and not sched.running


def test_pdfs_lost_from_the_disk_are_fetched_again_from_drive(api, ctx, drive):
    from app.storage import get_storage

    enable(api("manager"))
    t0 = utcnow()
    assert job(drive_sync.tick(t0))["stats"]["raw_imported"] == 3
    db = SessionLocal()
    f1 = db.scalars(select(Form).where(Form.form_code == "9910001")).one()
    lost = [d for d in f1.documents]                                          # RAW + edited of one form vanish (host without a persistent disk)
    store = get_storage()

    def remove(key):                                                          # stored PDFs are read-only
        path = store._path(key)
        path.chmod(0o666)
        path.unlink()

    for d in lost:
        remove(d.storage_key)
        assert not store.exists(d.storage_key)
    db.close()
    j = job(drive_sync.tick(t0 + timedelta(minutes=6)))
    assert j["stats"]["files_restored"] == 2 and j["stats"].get("restore_failed", 0) == 0 and j["stats"]["qc_runs"] == 1
    db = SessionLocal()
    f1 = db.scalars(select(Form).where(Form.form_code == "9910001")).one()
    assert all(store.exists(d.storage_key) for d in f1.documents)
    db.close()
    # the Drive file changed after the loss: the original cannot be restored, and that is reported rather than hidden
    d0 = lost[0]
    remove(d0.storage_key)
    (drive.proj / "9910001_1.pdf").write_bytes(review_pdf("9910001", "Another narrative entirely."))
    j2 = job(drive_sync.tick(t0 + timedelta(minutes=12)))
    assert j2["stats"].get("files_restored", 0) == 0 and j2["stats"]["restore_failed"] >= 1


def test_opening_a_lost_pdf_restores_it_on_demand(api, ctx, drive, monkeypatch):
    """The PDF viewer / QC / report must not 500 when the file vanished from the disk: it is fetched again from Drive on the spot."""
    from app.services import file_restore
    from app.storage import get_storage

    enable(api("manager"))
    job(drive_sync.tick(utcnow()))
    db = SessionLocal()
    f1 = db.scalars(select(Form).where(Form.form_code == "9910001")).one()
    raw = next(d for d in f1.documents if d.kind == "raw")
    doc_id, key = raw.id, raw.storage_key
    db.close()
    store = get_storage()
    path = store._path(key)
    path.chmod(0o666)
    path.unlink()
    r = api("manager").get(f"/api/documents/{doc_id}/file")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and store.exists(key)                 # restored while serving it
    # no way back (Drive file gone): a clear 404 instead of "Unexpected server error"
    path = store._path(key)
    path.chmod(0o666)
    path.unlink()
    for p in drive.proj.glob("9910001_1.pdf"):
        p.unlink()
    r = api("manager").get(f"/api/documents/{doc_id}/file")
    assert r.status_code == 404 and "missing from storage" in r.json()["detail"]


def test_qc_that_failed_for_a_lost_pdf_is_run_again_by_the_next_sync(api, ctx, drive):
    enable(api("manager"))
    t0 = utcnow()
    assert job(drive_sync.tick(t0))["stats"]["qc_runs"] == 2
    db = SessionLocal()
    f1 = db.scalars(select(Form).where(Form.form_code == "9910001")).one()
    run = db.scalars(select(QCRun).where(QCRun.form_id == f1.id).order_by(QCRun.id.desc())).first()
    run.status, run.error = "failed", "Unexpected processing error: [Errno 2] No such file or directory: '/data/storage/x.pdf'"
    f2 = db.scalars(select(Form).where(Form.form_code == "9910002")).one()
    r2 = db.scalars(select(QCRun).where(QCRun.form_id == f2.id).order_by(QCRun.id.desc())).first()
    r2.status, r2.error = "failed", "Text could not be extracted reliably (Tesseract binary not found)"          # a real, lasting failure: not retried
    db.commit()
    f1_id, f2_id = f1.id, f2.id
    db.close()
    j = job(drive_sync.tick(t0 + timedelta(minutes=6)))
    assert j["stats"]["qc_retried"] == 1
    db = SessionLocal()
    latest = lambda fid: db.scalars(select(QCRun).where(QCRun.form_id == fid).order_by(QCRun.id.desc())).first()      # noqa: E731
    assert latest(f1_id).status in ("completed", "completed_with_warnings")
    assert latest(f2_id).status == "failed" and "Tesseract" in latest(f2_id).error
    db.close()
