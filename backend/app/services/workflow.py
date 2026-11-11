"""Form workflow state machine, assignment, and QC decision rules.

Pending -> In Progress -> QC Review -> Re-edit Required | Approved -> Closed
plus Manual Review Required and Blocked. Approval is only ever the result of a human reviewer's decision.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import utcnow
from ..enums import Decision, FormStatus, ReviewStatus, Role, RunStatus
from ..models import (Assignment, Document, Finding, Form, FormComment, Notification, QCRun, ReviewAttempt, User)
from ..permissions import accessible_project_ids

S = FormStatus
TRANSITIONS: dict[str, set[str]] = {
    S.PENDING.value: {S.IN_PROGRESS.value, S.QC_REVIEW.value, S.BLOCKED.value},
    S.IN_PROGRESS.value: {S.QC_REVIEW.value, S.BLOCKED.value},
    S.QC_REVIEW.value: {S.APPROVED.value, S.RE_EDIT_REQUIRED.value, S.MANUAL_REVIEW.value, S.BLOCKED.value},
    S.RE_EDIT_REQUIRED.value: {S.IN_PROGRESS.value, S.QC_REVIEW.value, S.BLOCKED.value},
    S.APPROVED.value: {S.CLOSED.value},
    S.CLOSED.value: set(),
    S.MANUAL_REVIEW.value: {S.QC_REVIEW.value, S.RE_EDIT_REQUIRED.value, S.BLOCKED.value},
    S.BLOCKED.value: {S.PENDING.value, S.IN_PROGRESS.value, S.QC_REVIEW.value},
}


def notify(db: Session, user_id: int | None, kind: str, message: str, form: Form | None = None) -> None:
    if user_id:
        db.add(Notification(user_id=user_id, kind=kind, message=message[:500], form_id=form.id if form else None))


def change_status(db: Session, actor: User | None, form: Form, to: str, reason: str | None = None, request=None) -> None:
    if to not in TRANSITIONS.get(form.status, set()):
        raise HTTPException(409, f"Cannot move a form from '{form.status}' to '{to}'.")
    prev = form.status
    form.status, form.status_reason = to, reason
    now = utcnow()
    if to == S.APPROVED.value:
        form.approved_at = now
    if to == S.CLOSED.value:
        form.closed_at = now
    audit(db, actor, "form.status_changed", entity_type="form", entity_id=form.id, form_id=form.id,
          project_id=form.project_id, detail={"from": prev, "to": to, "reason": reason}, request=request)


# ------------------------------------------------------------------- assignment
def assign(db: Session, actor: User, form: Form, *, editor_id: int | None, reviewer_id: int | None,
           due_at=None, note: str | None = None, request=None) -> None:
    allowed_proj = accessible_project_ids(db, actor)
    if allowed_proj is not None and form.project_id not in allowed_proj:
        raise HTTPException(403, "You do not have access to this project.")

    def apply(kind: str, uid: int, roles: set[str]) -> None:
        target = db.get(User, uid)
        if not target or not target.is_active:
            raise HTTPException(422, f"User {uid} not found or inactive.")
        if target.role not in roles:
            raise HTTPException(422, f"{target.name} has role '{target.role}' and cannot be assigned as {kind}.")
        # target must be able to see this project (editors get access via assignment itself)
        if kind == "reviewer":
            ids = accessible_project_ids(db, target)
            if ids is not None and form.project_id not in ids:
                raise HTTPException(422, f"{target.name} has no access to this project; grant access first.")
        elif target.role == Role.MANAGER.value:
            ids = accessible_project_ids(db, target)
            if ids is not None and form.project_id not in ids:
                raise HTTPException(422, f"{target.name} has no access to this project; grant access first.")
        current = form.assigned_editor_id if kind == "editor" else form.assigned_reviewer_id
        if current == uid and not due_at:
            return
        for a in db.execute(select(Assignment).where(Assignment.form_id == form.id, Assignment.role_kind == kind,
                                                      Assignment.active.is_(True))).scalars():
            a.active, a.ended_at = False, utcnow()
        db.add(Assignment(form_id=form.id, role_kind=kind, assignee_id=uid, assigned_by=actor.id, note=note, due_at=due_at))
        if kind == "editor":
            form.assigned_editor_id = uid
        else:
            form.assigned_reviewer_id = uid
        audit(db, actor, f"form.{kind}_assigned", entity_type="form", entity_id=form.id, form_id=form.id,
              project_id=form.project_id, detail={"assignee_id": uid, "previous": current, "note": note}, request=request)
        notify(db, uid, "assignment", f"You were assigned {form.form_code} as {kind}.", form)

    if editor_id:
        apply("editor", editor_id, {Role.EDITOR.value, Role.MANAGER.value})
    if reviewer_id:
        apply("reviewer", reviewer_id, {Role.QC_REVIEWER.value, Role.MANAGER.value})
    if due_at is not None:
        form.due_at = due_at
        audit(db, actor, "form.due_date_set", entity_type="form", entity_id=form.id, form_id=form.id,
              project_id=form.project_id, detail={"due_at": due_at.isoformat()}, request=request)


# ------------------------------------------------------------------- decision
def latest_edited(form: Form) -> Document | None:
    ed = [d for d in form.documents if d.kind == "edited"]
    return max(ed, key=lambda d: d.version_no) if ed else None


def current_run(db: Session, form: Form) -> QCRun | None:
    ed = latest_edited(form)
    if not ed:
        return None
    return db.execute(select(QCRun).where(QCRun.form_id == form.id, QCRun.edited_document_id == ed.id)
                      .order_by(QCRun.id.desc())).scalars().first()


def decide(db: Session, reviewer: User, form: Form, decision: str, *, summary: str | None, feedback: str | None,
           acknowledge_warnings: bool = False, request=None) -> ReviewAttempt:
    settings = get_settings()
    if form.status != S.QC_REVIEW.value:
        raise HTTPException(409, f"Form is '{form.status}'; decisions can only be saved while in QC Review.")
    ed = latest_edited(form)
    if not ed:
        raise HTTPException(409, "No edited PDF has been submitted for this form.")
    if not settings.allow_self_review and (reviewer.id == form.assigned_editor_id or reviewer.id == ed.uploaded_by):
        raise HTTPException(403, "Independent review policy: you cannot make the QC decision on work you edited or submitted.")
    run = current_run(db, form)
    if decision in (Decision.APPROVED.value, Decision.RE_EDIT.value):
        if not run or run.status not in (RunStatus.COMPLETED.value, RunStatus.COMPLETED_WITH_WARNINGS.value):
            raise HTTPException(409, "Run the QC analysis for the latest submission before approving or sending back.")
    findings = list(run.findings) if run else []
    counts = {k: sum(1 for f in findings if f.review_status == k) for k in
              (ReviewStatus.PENDING.value, ReviewStatus.ACCEPTED.value, ReviewStatus.REJECTED.value, ReviewStatus.EDITED.value,
               ReviewStatus.NOT_APPLICABLE.value, ReviewStatus.ESCALATED.value)}
    confirmed = counts["accepted"] + counts["edited"]

    if decision == Decision.APPROVED.value:
        if counts["pending"]:
            raise HTTPException(409, f"{counts['pending']} finding(s) have not been reviewed. Every finding must be validated by a human.")
        if counts["escalated"]:
            raise HTTPException(409, f"{counts['escalated']} escalated finding(s) must be resolved before approval.")
        if confirmed:
            raise HTTPException(409, f"{confirmed} finding(s) were confirmed as errors; send the form back for re-edit instead of approving.")
        if run.status == RunStatus.COMPLETED_WITH_WARNINGS.value and not acknowledge_warnings:
            raise HTTPException(409, "This run completed with warnings (e.g. AI analysis unavailable). Acknowledge the warnings to approve.")
        target = S.APPROVED.value
    elif decision == Decision.RE_EDIT.value:
        if not confirmed and not (feedback or "").strip():
            raise HTTPException(422, "Provide feedback for the editor or accept at least one finding.")
        if counts["pending"]:
            raise HTTPException(409, f"{counts['pending']} finding(s) still need a decision (accept, reject, edit, not applicable or escalate).")
        target = S.RE_EDIT_REQUIRED.value
    elif decision == Decision.MANUAL_REVIEW.value:
        if not (summary or "").strip():
            raise HTTPException(422, "Explain why manual review is required.")
        target = S.MANUAL_REVIEW.value
    elif decision == Decision.BLOCKED.value:
        if not (summary or "").strip():
            raise HTTPException(422, "Explain what input is missing or invalid.")
        target = S.BLOCKED.value
    else:
        raise HTTPException(422, "Unknown decision.")

    prior = db.execute(select(func.count()).select_from(ReviewAttempt).where(ReviewAttempt.form_id == form.id)).scalar_one()
    attempt = ReviewAttempt(
        form_id=form.id, attempt_no=prior + 1, run_id=run.id if run else None, edited_document_id=ed.id, reviewer_id=reviewer.id,
        decision=decision, summary=summary, feedback_to_editor=feedback, accepted_count=confirmed,
        rejected_count=counts["rejected"], other_count=counts["not_applicable"] + counts["escalated"])
    db.add(attempt)
    db.flush()
    if (feedback or "").strip():
        db.add(FormComment(form_id=form.id, attempt_id=attempt.id, author_id=reviewer.id, kind="feedback", body=feedback.strip()))
    change_status(db, reviewer, form, target, summary or feedback, request)
    audit(db, reviewer, "qc.decision_saved", entity_type="review_attempt", entity_id=attempt.id, form_id=form.id,
          project_id=form.project_id, detail={"decision": decision, "attempt_no": attempt.attempt_no, "accepted": confirmed,
                                               "rejected": counts["rejected"], "run_id": run.id if run else None}, request=request)
    if decision == Decision.RE_EDIT.value:
        notify(db, form.assigned_editor_id, "feedback", f"{form.form_code} needs re-edit: QC feedback available.", form)
    elif decision == Decision.APPROVED.value:
        notify(db, form.assigned_editor_id, "approved", f"{form.form_code} was approved.", form)
    return attempt
