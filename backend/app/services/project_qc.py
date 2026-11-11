"""Project-level QC: hand a whole project to a QC lead / reviewer.

Assigning a project makes that person the reviewer of every form in it (new forms follow automatically), queues the
QC analysis for every form that has both a RAW and an edited PDF, and gives a per-form progress table + report so the
reviewer can then work through the findings by hand. Approval is never automatic: the pipeline only produces findings.
"""
from __future__ import annotations

import difflib
import io
import re
import zipfile
from collections import defaultdict
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import utcnow
from ..enums import FormStatus, Role
from ..models import (Assignment, Finding, Form, MediaLink, Project, ProjectReviewer, QCRun, ReviewAttempt, User,
                      UserProjectAccess)
from . import error_taxonomy as tx, jobs, pipeline, qc_report, qc_reports, workflow

E = qc_report.E
FINAL_STATES = {FormStatus.APPROVED.value, FormStatus.CLOSED.value}
SKIP_STATES = FINAL_STATES | {FormStatus.BLOCKED.value, FormStatus.MANUAL_REVIEW.value, FormStatus.RE_EDIT_REQUIRED.value}
STAGE_LABEL = {"missing_raw": "RAW missing", "waiting_edited": "Waiting for edited PDF", "analysing": "QC running", "qc_failed": "QC failed",
               "to_review": "To review", "ready_to_decide": "Ready to decide", "decided": "Decided"}


def _conflict(form: Form, reviewer: User) -> bool:
    """Independent review: a reviewer is never given work they edited or submitted (unless the deployment allows it)."""
    if get_settings().allow_self_review:
        return False
    ed = workflow.latest_edited(form)
    return reviewer.id == form.assigned_editor_id or bool(ed and ed.uploaded_by == reviewer.id)


def owner_of(db: Session, project_id: int) -> User | None:
    row = db.get(ProjectReviewer, project_id)
    return db.get(User, row.reviewer_id) if row else None


def folder_owner(db: Session, project_name: str) -> User | None:
    """The QC lead a Drive folder belongs to, read from its name: "Valley Bank_Geeta" -> Geeta, "Oil Change_Saradiya" -> Saradhya.
    The part after the last underscore (or the whole name) is matched against the active QC leads / managers, tolerating a spelling slip."""
    token = re.split(r"[_]", project_name.strip())[-1].strip().lower()
    if not token:
        return None
    people = db.execute(select(User).where(User.role.in_([Role.QC_REVIEWER.value, Role.MANAGER.value]), User.is_active.is_(True))).scalars().all()
    scored = sorted(((difflib.SequenceMatcher(None, token, (u.name or "").split()[0].lower()).ratio(), u) for u in people if u.name),
                    key=lambda t: -t[0])
    if not scored or scored[0][0] < 0.8 or (len(scored) > 1 and scored[1][0] >= scored[0][0] - 0.05):    # no clear match: do not guess
        return None
    return scored[0][1]


def ensure_coordinator_access(db: Session) -> int:
    """Every project is visible to every active coordinator (idempotent). Returns the number of grants added."""
    coords = db.execute(select(User.id).where(User.is_coordinator.is_(True), User.is_active.is_(True))).scalars().all()
    if not coords:
        return 0
    have = {(u, p) for u, p in db.execute(select(UserProjectAccess.user_id, UserProjectAccess.project_id)).all()}
    n = 0
    for pid in db.execute(select(Project.id)).scalars().all():
        for uid in coords:
            if (uid, pid) not in have:
                db.add(UserProjectAccess(user_id=uid, project_id=pid))
                n += 1
    if n:
        db.commit()
    return n


def adopt_folder_owner(db: Session, project: Project, actor_id: int | None) -> User | None:
    """New Drive project: record who it belongs to and let that person see it. No forms exist yet; later ones follow the owner."""
    owner = folder_owner(db, project.name)
    if owner is None or db.get(ProjectReviewer, project.id):
        return owner
    db.add(ProjectReviewer(project_id=project.id, reviewer_id=owner.id, assigned_by=actor_id))
    if db.get(UserProjectAccess, (owner.id, project.id)) is None:
        db.add(UserProjectAccess(user_id=owner.id, project_id=project.id, granted_by=actor_id))
    return owner


def queue_analysis(db: Session, user: User | None, project: Project, *, retry_failed: bool = True) -> dict:
    """Create a QC run for every form whose latest edited PDF has no analysis yet (and whose last one failed, if asked)."""
    out: dict = {"queued": 0, "already_done": 0, "running": 0, "waiting_edited": 0, "missing_raw": 0, "skipped_status": 0, "run_ids": []}
    forms = db.execute(select(Form).where(Form.project_id == project.id).order_by(Form.form_code)).scalars().all()
    for form in forms:
        if form.status in SKIP_STATES:
            out["skipped_status"] += 1
            continue
        if not any(d.kind == "raw" for d in form.documents):
            out["missing_raw"] += 1
            continue
        edited = workflow.latest_edited(form)
        if not edited:
            out["waiting_edited"] += 1
            continue
        run = workflow.current_run(db, form)
        if run and run.status in ("queued", "running"):
            out["running"] += 1
            continue
        if run and (run.status != "failed" or not retry_failed):
            out["already_done"] += 1
            continue
        out["run_ids"].append(pipeline.create_run(db, form, edited, user).id)
        out["queued"] += 1
    return out


def start_runs(run_ids: list[int]) -> None:
    for rid in run_ids:
        jobs.submit(pipeline.run_job, rid)


def assign_project(db: Session, actor: User, project: Project, reviewer: User | None, request=None) -> dict:
    """Make `reviewer` the owner of the project. None clears the owner (forms keep their current reviewers)."""
    prev = db.get(ProjectReviewer, project.id)
    if reviewer is None:
        if prev:
            db.delete(prev)
            audit(db, actor, "project.reviewer_cleared", entity_type="project", entity_id=project.id, project_id=project.id,
                  detail={"previous": prev.reviewer_id}, request=request)
        db.commit()
        return {"reviewer": None, "forms": 0, "assigned": 0, "conflicts": [], "analysis": {}}
    if not reviewer.is_active or reviewer.role not in (Role.QC_REVIEWER.value, Role.MANAGER.value):
        raise HTTPException(422, f"{reviewer.name} cannot be a project reviewer: only QC leads / reviewers (and the manager) can.")
    if not db.get(UserProjectAccess, (reviewer.id, project.id)):
        db.add(UserProjectAccess(user_id=reviewer.id, project_id=project.id, granted_by=actor.id))
        audit(db, actor, "access.changed", entity_type="user", entity_id=reviewer.id, project_id=project.id,
              detail={"granted_project": project.id, "via": "project reviewer assignment"}, request=request)
    if prev and prev.reviewer_id != reviewer.id:      # a QC lead only sees the projects handed to her: the previous one loses access
        old = db.get(User, prev.reviewer_id)
        if old and old.role == Role.QC_REVIEWER.value and not old.is_coordinator:
            acc = db.get(UserProjectAccess, (old.id, project.id))
            if acc:
                db.delete(acc)
    if prev:
        prev.reviewer_id, prev.assigned_by, prev.assigned_at = reviewer.id, actor.id, utcnow()
    else:
        db.add(ProjectReviewer(project_id=project.id, reviewer_id=reviewer.id, assigned_by=actor.id))
    forms = db.execute(select(Form).where(Form.project_id == project.id).order_by(Form.form_code)).scalars().all()
    assigned, conflicts = 0, []
    for form in forms:
        if form.status in FINAL_STATES:
            continue
        if _conflict(form, reviewer):
            conflicts.append(form.form_code)
            continue
        if form.assigned_reviewer_id == reviewer.id:
            continue
        for a in db.execute(select(Assignment).where(Assignment.form_id == form.id, Assignment.role_kind == "reviewer",
                                                     Assignment.active.is_(True))).scalars():
            a.active, a.ended_at = False, utcnow()
        db.add(Assignment(form_id=form.id, role_kind="reviewer", assignee_id=reviewer.id, assigned_by=actor.id, note="Project assigned"))
        form.assigned_reviewer_id = reviewer.id
        assigned += 1
    analysis = queue_analysis(db, actor, project)
    audit(db, actor, "project.reviewer_assigned", entity_type="project", entity_id=project.id, project_id=project.id,
          detail={"reviewer_id": reviewer.id, "previous": prev.reviewer_id if prev else None, "forms": len(forms), "assigned": assigned,
                  "conflicts": conflicts, "qc_queued": analysis["queued"]}, request=request)
    workflow.notify(db, reviewer.id, "assignment",
                    f"Project {project.client.name} / {project.name} was assigned to you: {len(forms)} form(s), QC started on {analysis['queued']}.")
    db.commit()
    qc_reports.mark_dirty(project.id)                 # the QC Member column changes
    start_runs(analysis["run_ids"])
    return {"reviewer": {"id": reviewer.id, "name": reviewer.name}, "forms": len(forms), "assigned": assigned, "conflicts": conflicts, "analysis": analysis}


# ----------------------------------------------------------------------------- progress
def _stage(form: Form, raw, edited, run: QCRun | None, decided: bool, pending: int) -> str:
    if not raw:
        return "missing_raw"
    if not edited:
        return "waiting_edited"
    if run is None or run.status in ("queued", "running"):
        return "analysing"
    if run.status == "failed":
        return "qc_failed"
    if decided or form.status in FINAL_STATES:
        return "decided"
    return "to_review" if pending else "ready_to_decide"


def summary(db: Session, project: Project) -> dict:
    forms = db.execute(select(Form).where(Form.project_id == project.id).order_by(Form.form_code)).scalars().all()
    ids = [f.id for f in forms]
    runs: dict[int, list[QCRun]] = defaultdict(list)
    attempts: dict[int, ReviewAttempt] = {}
    sev: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rev: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_sub: dict[int, dict[tuple, int]] = defaultdict(lambda: defaultdict(int))
    table: dict[str, dict[str, int]] = {x.key: {"confirmed": 0, "pending": 0} for x in tx.SUBTYPES}
    media: dict[int, int] = {}
    if ids:
        for r in db.execute(select(QCRun).where(QCRun.form_id.in_(ids)).order_by(QCRun.id)).scalars():
            runs[r.form_id].append(r)
        for a in db.execute(select(ReviewAttempt).where(ReviewAttempt.form_id.in_(ids)).order_by(ReviewAttempt.id)).scalars():
            attempts[a.form_id] = a
        for run_id, s, rs, n in db.execute(select(Finding.run_id, Finding.severity, Finding.review_status, func.count())
                                           .where(Finding.form_id.in_(ids)).group_by(Finding.run_id, Finding.severity, Finding.review_status)).all():
            sev[run_id][s] += n
            rev[run_id][rs] += n
        for run_id, sub, rs, n in db.execute(select(Finding.run_id, Finding.error_subtype, Finding.review_status, func.count())
                                             .where(Finding.form_id.in_(ids)).group_by(Finding.run_id, Finding.error_subtype, Finding.review_status)).all():
            by_sub[run_id][(sub, rs)] += n
        media = dict(db.execute(select(MediaLink.form_id, func.count()).where(MediaLink.form_id.in_(ids)).group_by(MediaLink.form_id)).all())
    rows, totals = [], defaultdict(int)
    for f in forms:
        raw = next((d for d in f.documents if d.kind == "raw"), None)
        edited = workflow.latest_edited(f)
        run = next((r for r in reversed(runs[f.id]) if edited and r.edited_document_id == edited.id), None)
        attempt = attempts.get(f.id)
        decided = bool(attempt and run and attempt.run_id == run.id)
        s, r = (sev[run.id], rev[run.id]) if run else ({}, {})
        stage = _stage(f, raw, edited, run, decided, r.get("pending", 0))
        total = sum(s.values())
        conf_subs = [k for (sub, rs), n in (by_sub[run.id].items() if run else []) if rs in ("accepted", "edited") for k in [sub] * n]
        for (sub, rs), n in (by_sub[run.id].items() if run else []):
            if sub in table and rs in ("accepted", "edited"):
                table[sub]["confirmed"] += n
            elif sub in table and rs == "pending":
                table[sub]["pending"] += n
        sc = tx.score(conf_subs)
        errors = {c.key: sum(1 for k in conf_subs if tx.category_of(k) and tx.category_of(k).key == c.key) for c in tx.CATEGORIES}
        rows.append({"score": sc["score"] if run else None, "errors": errors, "form_id": f.id, "form_code": f.form_code, "status": f.status, "stage": stage, "stage_label": STAGE_LABEL[stage],
                     "editor": f.assigned_editor.name if f.assigned_editor else None,
                     "reviewer": f.assigned_reviewer.name if f.assigned_reviewer else None,
                     "run_id": run.id if run else None, "run_status": run.status if run else None, "run_error": run.error if run else None,
                     "findings": {"total": total, **{k: s.get(k, 0) for k in ("critical", "major", "minor", "info")}},
                     "review": {"pending": r.get("pending", 0), "confirmed": r.get("accepted", 0) + r.get("edited", 0),
                                "rejected": r.get("rejected", 0), "other": r.get("not_applicable", 0) + r.get("escalated", 0)},
                     "decision": attempt.decision if decided else None, "recordings": media.get(f.id, 0)})
        totals[stage] += 1
        if run and run.status in ("completed", "completed_with_warnings"):
            totals["scored"] += 1
            totals["score_sum"] += sc["score"]
            if decided:
                totals["decided_scored"] += 1
                totals["decided_score_sum"] += sc["score"]
        totals["findings"] += total
        totals["pending_findings"] += r.get("pending", 0)
    owner = owner_of(db, project.id)
    return {"project": {"id": project.id, "name": project.name, "client": project.client.name, "path": f"{project.client.name} / {project.name}"},
            "reviewer": {"id": owner.id, "name": owner.name} if owner else None, "forms": rows,
            "error_table": [{"category": c.label, "marks": c.points, "errors": [{"key": x.key, "label": x.label, "confirmed": table[x.key]["confirmed"],
                                                                                "pending": table[x.key]["pending"], "reviewer_checked": x.manual_only}
                                                                               for x in tx.SUBTYPES if x.category == c.key]} for c in tx.CATEGORIES],
            "totals": {"forms": len(forms), **{k: totals.get(k, 0) for k in STAGE_LABEL}, "findings": totals["findings"],
                       "pending_findings": totals["pending_findings"],
                       "average_score": round(totals["score_sum"] / totals["scored"], 1) if totals["scored"] else None,
                       "average_score_decided": round(totals["decided_score_sum"] / totals["decided_scored"], 1) if totals["decided_scored"] else None,
                       "progress": round(totals["decided"] / len(forms), 3) if forms else 0.0}}


def overview(db: Session, projects: list[Project], user: User, mine: bool) -> list[dict]:
    owners = {o.project_id: o.reviewer_id for o in db.execute(select(ProjectReviewer)).scalars()}
    users = {u.id: u for u in db.execute(select(User)).scalars()}
    out = []
    for p in projects:
        if mine and owners.get(p.id) != user.id:
            continue
        sm = summary(db, p)
        owner = users.get(owners.get(p.id))
        hint = None if owner else folder_owner(db, p.name)
        out.append({**sm["project"], "reviewer": {"id": owner.id, "name": owner.name} if owner else None, **sm["totals"], "is_sample": p.is_sample,
                    "suggested_reviewer": {"id": hint.id, "name": hint.name} if hint else None})
    return out


# ----------------------------------------------------------------------------- reports
def project_report(db: Session, project: Project, generated_by: User) -> tuple[bytes, str]:
    sm = summary(db, project)
    t = sm["totals"]
    when = datetime.utcnow()
    h = [f"<h1>Project QC Report — {E(project.name)}</h1><p class='muted'>{E(project.client.name)} · generated {when.strftime('%d %b %Y %H:%M UTC')} by {E(generated_by.name)}</p>",
         f"<p><b>QC lead / reviewer:</b> {E(sm['reviewer']['name']) if sm['reviewer'] else 'not assigned'}</p>",
         "<h2>Progress</h2>",
         f"<p>{t['forms']} form(s): {t['decided']} decided · {t['to_review'] + t['ready_to_decide']} awaiting the reviewer · {t['analysing']} being analysed · "
         f"{t['qc_failed']} QC failed · {t['waiting_edited']} waiting for the edited PDF · {t['missing_raw']} without RAW. "
         f"{t['findings']} finding(s) in total, {t['pending_findings']} not yet reviewed ({t['progress']:.0%} of forms decided).</p>",
         ]
    avg = lambda v: "—" if v is None else f"{v:g} / {tx.TOTAL_SCORE}"        # noqa: E731
    h.append(f"<p><b>Average QC score:</b> {avg(t['average_score'])} over {sum(1 for r in sm['forms'] if r['score'] is not None)} analysed form(s) "
             f"· {avg(t['average_score_decided'])} over forms already decided. Scores count confirmed errors only"
             + (f" — provisional: {t['pending_findings']} finding(s) are not reviewed yet." if t["pending_findings"] else ".") + "</p>")
    h.append("<h2>Errors by the QC error table</h2><table><tr><th>Category (marks)</th><th>Error</th><th>Confirmed</th><th>Not reviewed</th></tr>")
    for cat in sm["error_table"]:
        for e in cat["errors"]:
            h.append(f"<tr><td>{E(cat['category'])} (−{cat['marks']})</td><td>{E(e['label'])}{' *' if e['reviewer_checked'] else ''}</td>"
                     f"<td>{e['confirmed'] or '–'}</td><td>{e['pending'] or '–'}</td></tr>")
    h.append("</table><p class='muted'>* needs the video, audio, call log or picture: added by the reviewer.</p>")
    h.append("<h2>Forms</h2><table><tr><th>Form</th><th>Stage</th><th>Score</th><th>Grammar</th><th>Missed instr.</th><th>Contradiction</th><th>Wrong loc.</th>"
             "<th>To review</th><th>Rejected</th><th>Decision</th></tr>")
    for r in sm["forms"]:
        v, e = r["review"], r["errors"]
        err = f" — {E(r['run_error'][:90])}" if r["run_error"] else ""
        h.append(f"<tr><td>{E(r['form_code'])}</td><td>{E(r['stage_label'])}{err}</td><td>{'—' if r['score'] is None else r['score']}</td>"
                 f"<td>{e['grammar'] or '–'}</td><td>{e['missed_instruction'] or '–'}</td><td>{e['contradiction'] or '–'}</td><td>{e['wrong_location'] or '–'}</td>"
                 f"<td>{v['pending']}</td><td>{v['rejected']}</td><td>{E((r['decision'] or '—').replace('_', ' '))}</td></tr>")
    h.append("</table><p class='muted' style='margin-top:12pt'>QC checks only the fixed error table (Grammar Mistake 5 · Missed Instruction 15 · Contradiction 20 · "
             "Wrong Location / Put on Different category 25). Automated findings are suggestions; each form's own QC report holds the quoted evidence, "
             "and a form is final only after a named human reviewer has decided it.</p>")
    data = qc_report.render_pdf("".join(h), lambda i, n: f"Project QC Report {project.name} · page {i} of {n}")
    safe = "".join(c if c.isalnum() else "_" for c in project.name)
    return data, f"Project_QC_{safe}_{when.strftime('%Y%m%d-%H%M')}"


def report_bundle(db: Session, project: Project, generated_by: User, limit: int = 300) -> tuple[bytes, str]:
    """ZIP: the project summary plus one QC report per form that has been analysed."""
    summ, base = project_report(db, project, generated_by)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("00_Project_summary.pdf", summ)
        for r in summary(db, project)["forms"][:limit]:
            if r["run_status"] in ("completed", "completed_with_warnings"):
                data, name = qc_report.build_report(db, db.get(Form, r["form_id"]), generated_by)
                z.writestr(name, data)
    return buf.getvalue(), base + ".zip"
