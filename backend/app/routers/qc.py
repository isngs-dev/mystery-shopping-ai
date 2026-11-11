"""QC workspace API: runs, findings, reviewer actions and final decisions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db, utcnow
from ..deps import get_current_user, needs
from ..enums import SEVERITY_ORDER, FormStatus, ReviewStatus, Role
from ..models import Document, Finding, Form, QCRun, ReviewAttempt, User
from ..permissions import get_form_or_404, has_perm
from ..serializers import attempt_out, document_out, finding_out, form_out, run_out
from ..services import compare, jobs, pipeline, qc_report, qc_reports, workflow
from ..services import error_taxonomy as tx

router = APIRouter(prefix="/api", tags=["qc"])
SEVERITIES = ("critical", "major", "minor", "info")


def _workspace_guard(user: User) -> None:
    if user.role == Role.EDITOR.value:
        raise HTTPException(403, "Editors receive QC feedback on the form page; the QC workspace is for reviewers.")
    if not has_perm(user, "qc.view"):
        raise HTTPException(403, "Your role cannot open the QC workspace.")


CONFIRMED = (ReviewStatus.ACCEPTED.value, ReviewStatus.EDITED.value)


def form_score(findings) -> dict:
    """Score of a form from the findings a reviewer confirmed (see error_taxonomy.score)."""
    out = tx.score([f.error_subtype for f in findings if f.review_status in CONFIRMED])
    # what the automated check alone would give: every finding the reviewer has not rejected counts
    out["automated"] = tx.score([f.error_subtype for f in findings if f.review_status not in ("rejected", "not_applicable")])["score"]
    return out


@router.get("/qc/error-table")
def error_table(user: User = Depends(get_current_user)):
    """The fixed QC error table: the only categories / sub-types QC checks and scores."""
    return tx.catalog()


def _sorted_findings(findings):
    return sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.edited_page or f.raw_page or 0, f.id))


@router.get("/forms/{form_id}/qc")
def workspace(form_id: int, run_id: int | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _workspace_guard(user)
    form = get_form_or_404(db, user, form_id)
    raw = next((d for d in form.documents if d.kind == "raw"), None)
    edited_docs = sorted([d for d in form.documents if d.kind == "edited"], key=lambda d: d.version_no)
    runs = db.execute(select(QCRun).where(QCRun.form_id == form.id).order_by(QCRun.id.desc())).scalars().all()
    run = next((r for r in runs if r.id == run_id), None) if run_id else (runs[0] if runs else None)
    if run_id and not run:
        raise HTTPException(404, "Run not found.")
    edited = next((d for d in edited_docs if run and d.id == run.edited_document_id), edited_docs[-1] if edited_docs else None)
    users = {u.id: u for u in db.execute(select(User)).scalars()}
    attempts = db.execute(select(ReviewAttempt).where(ReviewAttempt.form_id == form.id).order_by(ReviewAttempt.id)).scalars().all()
    latest_run = workflow.current_run(db, form)
    return {
        "form": form_out(form), "raw_document": document_out(raw) if raw else None,
        "edited_document": document_out(edited) if edited else None,
        "edited_versions": [document_out(d) for d in edited_docs],
        "run": run_out(run) if run else None, "runs": [run_out(r, with_stats=False) for r in runs],
        "findings": [finding_out(f) for f in _sorted_findings(run.findings)] if run else [],
        "score": form_score(run.findings) if run else None,
        "attempts": [attempt_out(a, users.get(a.reviewer_id)) for a in attempts],
        "is_current_run": bool(run and latest_run and run.id == latest_run.id),
        "can_review": has_perm(user, "qc.review") and form.status == FormStatus.QC_REVIEW.value
                      and bool(run and latest_run and run.id == latest_run.id),
        "policy": {"allow_self_review": get_settings().allow_self_review,
                   "self_review_blocked": (not get_settings().allow_self_review) and bool(
                       edited and (user.id == form.assigned_editor_id or user.id == edited.uploaded_by))},
    }


@router.post("/forms/{form_id}/qc/run", status_code=202)
def rerun(form_id: int, request: Request, user: User = Depends(needs("qc.run")), db: Session = Depends(get_db)):
    form = get_form_or_404(db, user, form_id)
    edited = workflow.latest_edited(form)
    if not edited:
        raise HTTPException(409, "No edited PDF has been submitted; nothing to analyse.")
    active = db.execute(select(QCRun).where(QCRun.form_id == form.id, QCRun.status.in_(["queued", "running"]))).scalars().first()
    if active:
        raise HTTPException(409, "An analysis is already in progress for this form.")
    if form.status in (FormStatus.BLOCKED.value, FormStatus.MANUAL_REVIEW.value) and not has_perm(user, "workflow.resolve_manual"):
        raise HTTPException(409, f"Form is '{form.status}'.")
    if form.status in (FormStatus.BLOCKED.value, FormStatus.MANUAL_REVIEW.value):
        workflow.change_status(db, user, form, FormStatus.QC_REVIEW.value, "Re-analysis requested", request)
    run = pipeline.create_run(db, form, edited, user)
    jobs.submit(pipeline.run_job, run.id)
    db.refresh(run)
    return run_out(run)


@router.get("/qc/runs/{run_id}")
def run_status(run_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not has_perm(user, "qc.view"):
        raise HTTPException(403, "Your role cannot view QC runs.")
    run = db.get(QCRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found.")
    get_form_or_404(db, user, run.form_id)
    out = run_out(run, with_stats=user.role != Role.EDITOR.value)
    out["finding_count"] = len(run.findings)
    return out


# ----------------------------------------------------------------------------- finding review
class ReviewIn(BaseModel):
    action: str = Field(pattern="^(accept|reject|edit|not_applicable|escalate|reset)$")
    comment: str | None = Field(default=None, max_length=2000)
    title: str | None = Field(default=None, max_length=250)
    description: str | None = Field(default=None, max_length=4000)
    severity: str | None = None
    suggested_correction: str | None = Field(default=None, max_length=4000)


ACTION_STATUS = {"accept": ReviewStatus.ACCEPTED, "reject": ReviewStatus.REJECTED, "edit": ReviewStatus.EDITED,
                 "not_applicable": ReviewStatus.NOT_APPLICABLE, "escalate": ReviewStatus.ESCALATED, "reset": ReviewStatus.PENDING}


def _finding_for_review(db: Session, user: User, finding_id: int) -> tuple[Finding, Form]:
    f = db.get(Finding, finding_id)
    if not f:
        raise HTTPException(404, "Finding not found.")
    form = get_form_or_404(db, user, f.form_id)
    if form.status != FormStatus.QC_REVIEW.value:
        raise HTTPException(409, f"Form is '{form.status}'; findings can only be reviewed during QC Review.")
    latest = workflow.current_run(db, form)
    if not latest or f.run_id != latest.id:
        raise HTTPException(409, "This finding belongs to an earlier analysis run and is read-only.")
    if not get_settings().allow_self_review and (user.id == form.assigned_editor_id):
        raise HTTPException(403, "Independent review policy: you cannot review work you edited.")
    return f, form


def _apply_review(f: Finding, user: User, body: ReviewIn) -> None:
    if body.action == "edit":
        if body.severity and body.severity not in SEVERITIES:
            raise HTTPException(422, "Invalid severity.")
        if body.title:
            f.title = body.title.strip()
        if body.description is not None:
            f.description = body.description
        if body.severity:
            f.severity = body.severity
        if body.suggested_correction is not None:
            f.suggested_correction = body.suggested_correction
    if body.action in ("reject", "escalate", "not_applicable") and not (body.comment or "").strip() and body.action == "escalate":
        raise HTTPException(422, "Explain why this finding is being escalated.")
    f.review_status = ACTION_STATUS[body.action].value
    f.reviewer_id = None if body.action == "reset" else user.id
    f.reviewed_at = None if body.action == "reset" else utcnow()
    if body.comment is not None:
        f.reviewer_comment = body.comment.strip() or None


@router.post("/findings/{finding_id}/review")
def review_finding(finding_id: int, body: ReviewIn, request: Request, user: User = Depends(needs("qc.review")), db: Session = Depends(get_db)):
    f, form = _finding_for_review(db, user, finding_id)
    before = f.review_status
    _apply_review(f, user, body)
    audit(db, user, "finding.reviewed", entity_type="finding", entity_id=f.id, form_id=form.id, project_id=form.project_id,
          detail={"action": body.action, "from": before, "to": f.review_status, "source": f.source, "rule": f.rule_label,
                  "comment": body.comment}, request=request)
    db.commit()
    qc_reports.mark_dirty(form.project_id)
    return finding_out(f)


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    action: str = Field(pattern="^(accept|reject|not_applicable|reset)$")
    comment: str | None = None


@router.post("/forms/{form_id}/findings/bulk")
def bulk_review(form_id: int, body: BulkIn, request: Request, user: User = Depends(needs("qc.review")), db: Session = Depends(get_db)):
    done = []
    for fid in body.ids:
        f, form = _finding_for_review(db, user, fid)
        if f.form_id != form_id:
            raise HTTPException(422, f"Finding {fid} does not belong to this form.")
        _apply_review(f, user, ReviewIn(action=body.action, comment=body.comment))
        done.append(f)
    audit(db, user, "finding.bulk_reviewed", entity_type="form", entity_id=form_id, form_id=form_id,
          detail={"action": body.action, "count": len(done), "ids": body.ids}, request=request)
    db.commit()
    if done:
        qc_reports.mark_dirty(db.get(Form, form_id).project_id)
    return [finding_out(f) for f in done]


class ManualFindingIn(BaseModel):
    error_subtype: str = Field(max_length=40)      # one entry of the fixed error table (GET /api/qc/error-table)
    title: str = Field(min_length=3, max_length=250)
    description: str = Field(default="", max_length=4000)
    raw_excerpt: str | None = None
    edited_excerpt: str | None = None
    suggested_correction: str | None = None


@router.post("/forms/{form_id}/findings", status_code=201)
def add_manual_finding(form_id: int, body: ManualFindingIn, request: Request, user: User = Depends(needs("qc.review")),
                       db: Session = Depends(get_db)):
    """Reviewer-added finding (something the system missed). Quoted evidence must exist verbatim in the documents."""
    form = get_form_or_404(db, user, form_id)
    if form.status != FormStatus.QC_REVIEW.value:
        raise HTTPException(409, "Findings can only be added during QC Review.")
    run = workflow.current_run(db, form)
    if not run or run.status not in ("completed", "completed_with_warnings"):
        raise HTTPException(409, "Wait for the analysis to finish before adding findings.")
    sub = tx.subtype(body.error_subtype)
    if sub is None:
        raise HTTPException(422, "Choose an error from the QC error table.")
    cat = tx.CATEGORY_BY_KEY[sub.category]
    if not (body.raw_excerpt or body.edited_excerpt) and not (sub.manual_only and body.description.strip()):
        raise HTTPException(422, "Quote the evidence from the RAW or edited document"
                                 + (" (or describe what you found in the video / audio / picture)." if sub.manual_only else "."))
    raw, edited = db.get(Document, run.raw_document_id), db.get(Document, run.edited_document_id)
    rtoks, etoks = compare.build_tokens(raw.pages), compare.build_tokens(edited.pages)
    rspan = compare.locate(rtoks, body.raw_excerpt) if body.raw_excerpt else None
    espan = compare.locate(etoks, body.edited_excerpt) if body.edited_excerpt else None
    if (body.raw_excerpt and not rspan) or (body.edited_excerpt and not espan):
        raise HTTPException(422, "The quoted text was not found verbatim in the document; copy it exactly from the extracted text.")
    f = Finding(
        run_id=run.id, form_id=form.id, type="reviewer_added", category=cat.key, error_subtype=sub.key, severity=cat.severity,
        source="manual", confidence=None,
        title=body.title.strip(), description=body.description, suggested_correction=body.suggested_correction,
        raw_excerpt=compare.excerpt(rtoks, *rspan) if rspan else None, raw_page=compare.span_page(rtoks, rspan[0]) if rspan else None,
        raw_boxes=compare.boxes_for(rtoks, *rspan) if rspan else None,
        edited_excerpt=compare.excerpt(etoks, *espan) if espan else None, edited_page=compare.span_page(etoks, espan[0]) if espan else None,
        edited_boxes=compare.boxes_for(etoks, *espan) if espan else None, review_status=ReviewStatus.ACCEPTED.value,
        reviewer_id=user.id, reviewed_at=utcnow(), needs_human_attention=False)
    db.add(f)
    db.flush()
    audit(db, user, "finding.added_manually", entity_type="finding", entity_id=f.id, form_id=form.id, project_id=form.project_id,
          detail={"error_subtype": sub.key, "category": cat.key}, request=request)
    db.commit()
    qc_reports.mark_dirty(form.project_id)
    return finding_out(f)


# ----------------------------------------------------------------------------- decision
class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approved|re_edit_required|manual_review_required|blocked)$")
    summary: str | None = Field(default=None, max_length=4000)
    feedback_to_editor: str | None = Field(default=None, max_length=6000)
    acknowledge_warnings: bool = False


@router.post("/forms/{form_id}/decision")
def save_decision(form_id: int, body: DecisionIn, request: Request, user: User = Depends(needs("qc.review")), db: Session = Depends(get_db)):
    form = get_form_or_404(db, user, form_id)
    attempt = workflow.decide(db, user, form, body.decision, summary=body.summary, feedback=body.feedback_to_editor,
                              acknowledge_warnings=body.acknowledge_warnings, request=request)
    db.commit()
    return {"attempt": attempt_out(attempt, user), "form": form_out(form)}


# ----------------------------------------------------------------------------- QC report (PDF)
@router.get("/forms/{form_id}/qc-report")
def download_report(form_id: int, request: Request, user: User = Depends(needs("qc.report")), db: Session = Depends(get_db)):
    """Generate the QC report PDF for a form. FINAL once a human decision exists for the analysed submission, else DRAFT."""
    form = get_form_or_404(db, user, form_id)
    if not any(d.kind == "raw" for d in form.documents):
        raise HTTPException(409, "There is no RAW document to report on.")
    data, name = qc_report.build_report(db, form, user)
    audit(db, user, "report.qc_downloaded", entity_type="form", entity_id=form.id, form_id=form.id, project_id=form.project_id,
          detail={"filename": name, "bytes": len(data)}, request=request)
    db.commit()
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
