"""Demo data. Everything created here is flagged is_sample=True and is pushed through the REAL ingestion,
extraction, comparison and review code paths - nothing is faked in the dashboard.

    python -m app.seed             # create sample users, clients, projects and ~16 forms
    python -m app.seed --samples   # also write sample PDFs to ../samples
Refuses to run when ENV=production.
"""
from __future__ import annotations

import argparse
import os
import random
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .db import Base, SessionLocal, engine, utcnow
from .main import bootstrap
from .models import (Assignment, AuditEvent, Client, Document, Finding, Form, Project, QCRun, ReviewAttempt, User,
                     UserProjectAccess)
from .sample_docs import make_pair
from .security import hash_password
from .services import pipeline, repository, workflow
from .services.rules_service import create_rule
from .storage import get_storage

DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "Demo-Passw0rd!2026")
DOMAIN = "demo.mysteryshop-ai.com"
USERS = [
    ("superadmin", "Sam Super (Super Admin)", "super_admin"), ("admin", "Ava Admin", "admin"),
    ("manager", "Mia Manager (Editor & Manager)", "manager"), ("editor1", "Ed Editor", "editor"),
    ("editor2", "Ellie Editor", "editor"), ("editor3", "Eli Editor", "editor"), ("qc1", "Quinn QC Lead", "qc_reviewer"),
    ("qc2", "Quincy QC Reviewer", "qc_reviewer"), ("ailead", "Aria AI Lead", "ai_lead"),
    ("owner", "Olive Process Owner", "process_owner"), ("sponsor", "Owen Ops Sponsor", "ops_sponsor"),
    ("client", "Cleo Client POC", "client_poc"), ("reporting", "Rhea Reporting", "reporting"),
]
# (code, scenario, end state)
PLAN = [
    ("DEMO001", "clean", "approved_first"), ("DEMO002", "clean", "approved_first"), ("DEMO003", "mixed", "reedit_approved"),
    ("DEMO004", "mixed", "reedit_approved"), ("DEMO005", "pii", "reedit_approved"), ("DEMO006", "mixed", "awaiting_qc"),
    ("DEMO007", "mixed", "awaiting_qc"), ("DEMO008", "missing", "awaiting_qc"), ("DEMO009", "mixed", "re_edit"),
    ("DEMO010", "pii", "re_edit"), ("DEMO011", "clean", "in_progress"), ("DEMO012", "clean", "pending"),
    ("DEMO013", "clean", "pending"), ("DEMO014", "mixed", "manual"), ("DEMO015", "clean", "blocked"),
    ("DEMO016", "clean", "approved_first"),
]


def main(write_samples: bool = False) -> None:
    s = get_settings()
    if s.env == "production":
        raise SystemExit("Refusing to seed demo data in production.")
    Base.metadata.create_all(engine)
    bootstrap()
    db = SessionLocal()
    rnd = random.Random(42)
    storage = get_storage()
    if write_samples:
        out = Path(__file__).resolve().parent.parent.parent / "samples"
        out.mkdir(exist_ok=True)
        for scen in ("clean", "mixed", "pii", "missing"):
            raw, edited, _ = make_pair(1, scen)
            (out / f"SAMPLE-{scen}_1.pdf").write_bytes(raw)
            (out / f"SAMPLE-{scen}_2.pdf").write_bytes(edited)
        print(f"Wrote sample PDFs to {out}")
    if db.execute(select(Form).where(Form.is_sample.is_(True)).limit(1)).first():
        print("Sample data already present; nothing to do.")
        return

    users: dict[str, User] = {}
    for key, name, role in USERS:
        email = f"{key}@{DOMAIN}"
        u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if not u:
            u = User(email=email, name=name, role=role, password_hash=hash_password(DEMO_PASSWORD), is_sample=True)
            db.add(u)
            db.flush()
        users[key] = u
    clients = {}
    for name in ("Demo Retail Co (SAMPLE)", "Demo Airlines (SAMPLE)"):
        c = Client(name=name, folder=name.split(" (")[0].replace(" ", "_"), is_sample=True)
        db.add(c)
        db.flush()
        clients[name] = c
    projects = [
        Project(client_id=clients["Demo Retail Co (SAMPLE)"].id, name="Store Visits - Ed Editor", folder="Store_Visits_Ed",
                editor_user_id=users["editor1"].id, is_sample=True),
        Project(client_id=clients["Demo Retail Co (SAMPLE)"].id, name="Store Visits - Ellie Editor", folder="Store_Visits_Ellie",
                editor_user_id=users["editor2"].id, is_sample=True),
        Project(client_id=clients["Demo Airlines (SAMPLE)"].id, name="Airport Experience", folder="Airport_Experience", is_sample=True),
    ]
    db.add_all(projects)
    db.flush()
    for key in ("admin", "manager", "qc1", "qc2", "client", "reporting", "owner", "sponsor"):
        for p in projects:
            db.add(UserProjectAccess(user_id=users[key].id, project_id=p.id, granted_by=users["superadmin"].id))
    # sample client-specific rules, published directly (sample baseline)
    create_rule(db, users["owner"], rule_key="DEMO-RETAIL-TONE", published=True, name="No promotional wording (Demo Retail)",
                category="grammar", error_subtype="terminology", severity="minor", client_id=clients["Demo Retail Co (SAMPLE)"].id,
                check_type="forbidden_terms", config={"terms": ["amazing", "awesome", "best ever"], "suggestion": "Use neutral, factual wording."},
                description="Reports for Demo Retail must not contain promotional adjectives. SAMPLE RULE.", effective_date=date.today() - timedelta(days=60))
    db.commit()

    store_projects = projects[:2]
    for i, (code, scen, end) in enumerate(PLAN):
        proj = store_projects[i % 2] if i < 14 else projects[2]
        raw, edited, _ = make_pair(i + 1, scen)
        editor = users["editor1"] if proj.editor_user_id == users["editor1"].id else users["editor2"] if proj.editor_user_id else users["editor3"]
        qc = users["qc1"] if i % 3 else users["qc2"]
        res = repository.ingest_pdf(db, storage, users["manager"], project=proj, form_code=code, kind="raw",
                                    original_filename=f"{code}_1.pdf", data=raw, is_sample=True)
        form = res.form
        form.is_sample = True
        pipeline.extract_document_job(res.document.id)
        if end == "pending" and i % 2:
            db.commit()
            continue
        workflow.assign(db, users["manager"], form, editor_id=editor.id, reviewer_id=qc.id,
                        due_at=utcnow() + timedelta(days=rnd.randint(-2, 6)))
        db.commit()
        if end in ("pending",):
            continue
        workflow.change_status(db, editor, form, "in_progress", "Editing started")
        db.commit()
        if end == "in_progress":
            continue
        if end == "blocked":
            workflow.change_status(db, users["manager"], form, "blocked", "Shopper audio file missing; awaiting client.")
            db.commit()
            continue
        sub = repository.ingest_pdf(db, storage, editor, project=proj, form_code=code, kind="edited",
                                    original_filename=f"{code}_2.pdf", data=edited, is_sample=True)
        run = pipeline.create_run(db, form, sub.document, editor)
        pipeline.execute_run(db, run.id)
        db.refresh(form)
        run = db.get(QCRun, run.id)
        if end == "awaiting_qc":
            continue
        findings = list(run.findings)
        if end == "manual":
            workflow.change_status(db, qc, form, "manual_review_required", "Escalated: disputed edit of reported amount")
            db.commit()
            continue
        if end == "approved_first":
            for f in findings:
                f.review_status, f.reviewer_id, f.reviewed_at = "rejected", qc.id, utcnow()
            db.flush()
            workflow.decide(db, qc, form, "approved", summary="Verified; no issues.", feedback=None, acknowledge_warnings=True)
            db.commit()
            continue
        # re-edit paths: confirm genuine findings (reject the low-confidence ones)
        for f in findings:
            ok = (f.confidence or 1) >= 0.7
            f.review_status, f.reviewer_id, f.reviewed_at = ("accepted" if ok else "rejected"), qc.id, utcnow()
            f.reviewer_comment = "Confirmed against RAW." if ok else "False positive."
        db.flush()
        workflow.decide(db, qc, form, "re_edit_required", summary="Corrections required.",
                        feedback="Please fix the confirmed findings listed and restore any changed figures.")
        db.commit()
        if end == "re_edit":
            continue
        workflow.change_status(db, editor, form, "in_progress", "Editing started")
        raw2, clean, _ = make_pair(i + 1, "clean")
        sub2 = repository.ingest_pdf(db, storage, editor, project=proj, form_code=code, kind="edited",
                                     original_filename=f"{code}_2.pdf", data=clean, is_sample=True, note="Corrections applied.")
        run2 = pipeline.create_run(db, form, sub2.document, editor)
        pipeline.execute_run(db, run2.id)
        db.refresh(form)
        for f in db.get(QCRun, run2.id).findings:
            f.review_status, f.reviewer_id, f.reviewed_at = "rejected", qc.id, utcnow()
        db.flush()
        workflow.decide(db, qc, form, "approved", summary="Corrections verified.", feedback=None, acknowledge_warnings=True)
        db.commit()

    # Spread the sample history over the last ~6 weeks so trends are meaningful
    forms = db.execute(select(Form).where(Form.is_sample.is_(True))).scalars().all()
    for f in forms:
        shift = timedelta(days=rnd.randint(2, 40), hours=rnd.randint(0, 20))
        f.created_at -= shift
        f.updated_at = utcnow() - shift / 2
        if f.first_submitted_at:
            f.first_submitted_at -= shift
        if f.approved_at:
            f.approved_at = f.created_at + timedelta(hours=rnd.randint(20, 120))
        for d in f.documents:
            d.uploaded_at -= shift
        for a in db.execute(select(ReviewAttempt).where(ReviewAttempt.form_id == f.id)).scalars():
            a.decided_at -= shift * 0.6
        for r in db.execute(select(QCRun).where(QCRun.form_id == f.id)).scalars():
            r.created_at -= shift
            r.started_at = (r.started_at or r.created_at) - shift
            r.finished_at = (r.finished_at or r.created_at) - shift
    db.commit()
    print(f"Seeded {len(forms)} SAMPLE forms. Demo logins: <role>@{DOMAIN} / password from DEMO_PASSWORD (default in docs).")
    print("Users:", ", ".join(f"{k}@{DOMAIN}" for k, *_ in USERS))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", action="store_true", help="also write sample PDFs to ../samples")
    main(ap.parse_args().samples)
