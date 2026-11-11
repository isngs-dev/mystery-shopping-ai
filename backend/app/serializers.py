from __future__ import annotations

from datetime import date, datetime

from .enums import ROLE_LABEL, ROLE_LEVEL, Role
from .models import (Assignment, AuditEvent, AutoEdit, Document, ExceptionItem, Finding, Form, FormComment, Project, QCRun,
                     ReviewAttempt, RuleVersion, User)
from .services import error_taxonomy as tx
from .services.rules_service import in_qc, scope_level


def iso(dt: datetime | date | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat() + "Z"
    return dt.isoformat()


def user_out(u: User | None) -> dict | None:
    if not u:
        return None
    role = Role(u.role)
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "role_label": ROLE_LABEL[role],
            "level": ROLE_LEVEL[role], "is_active": u.is_active, "must_change_password": u.must_change_password,
            "is_sample": u.is_sample, "is_coordinator": bool(u.is_coordinator), "created_at": iso(u.created_at),
            "last_login_at": iso(u.last_login_at)}


def user_brief(u: User | None) -> dict | None:
    return {"id": u.id, "name": u.name, "role": u.role} if u else None


def project_out(p: Project) -> dict:
    return {"id": p.id, "name": p.name, "folder": p.folder, "client_id": p.client_id, "client_name": p.client.name,
            "form_type": p.form_type, "is_active": p.is_active, "is_sample": p.is_sample,
            "editor_user_id": p.editor_user_id, "path": f"{p.client.name} / {p.name}"}


def document_out(d: Document) -> dict:
    return {"id": d.id, "form_id": d.form_id, "kind": d.kind, "version_no": d.version_no, "logical_name": d.logical_name,
            "stored_name": d.stored_name, "original_filename": d.original_filename, "storage_key": d.storage_key,
            "sha256": d.sha256, "size_bytes": d.size_bytes, "page_count": d.page_count, "status": d.status,
            "extraction_status": d.extraction_status, "extraction_summary": d.extraction_summary,
            "uploaded_at": iso(d.uploaded_at), "uploaded_by": d.uploaded_by, "is_sample": d.is_sample, "origin": d.origin}


def form_out(f: Form, detail: bool = False) -> dict:
    raw = next((d for d in f.documents if d.kind == "raw"), None)
    edited = [d for d in f.documents if d.kind == "edited"]
    latest = max(edited, key=lambda d: d.version_no) if edited else None
    pair = "complete" if raw and edited else ("missing_edited" if raw else ("missing_raw" if edited else "empty"))
    out = {
        "id": f.id, "form_code": f.form_code, "form_type": f.form_type, "status": f.status, "status_reason": f.status_reason,
        "project_id": f.project_id, "project": f.project.name, "client": f.project.client.name, "client_id": f.project.client_id,
        "path": f"{f.project.client.name} / {f.project.name} / {f.form_code}",
        "assigned_editor": user_brief(f.assigned_editor), "assigned_reviewer": user_brief(f.assigned_reviewer),
        "due_at": iso(f.due_at), "priority": f.priority, "is_sample": f.is_sample, "created_at": iso(f.created_at),
        "updated_at": iso(f.updated_at), "first_submitted_at": iso(f.first_submitted_at), "approved_at": iso(f.approved_at),
        "closed_at": iso(f.closed_at), "pair_status": pair, "edited_versions": len(edited),
        "raw_document_id": raw.id if raw else None, "latest_edited_document_id": latest.id if latest else None,
        "last_activity_at": iso(max([f.updated_at] + [d.uploaded_at for d in f.documents])),
    }
    if detail:
        out["documents"] = [document_out(d) for d in sorted(f.documents, key=lambda d: (d.kind, d.version_no))]
    return out


def finding_out(f: Finding) -> dict:
    return {
        "id": f.id, "run_id": f.run_id, "form_id": f.form_id, "type": f.type, "category": f.category, "severity": f.severity,
        "category_label": tx.category_label(f.category), "error_subtype": f.error_subtype, "error_label": tx.label(f.error_subtype),
        "points": tx.CATEGORY_BY_KEY[f.category].points if f.category in tx.CATEGORY_BY_KEY else None,
        "source": f.source, "confidence": f.confidence, "rule_version_id": f.rule_version_id, "rule_label": f.rule_label,
        "title": f.title, "description": f.description, "raw_excerpt": f.raw_excerpt, "raw_page": f.raw_page,
        "raw_boxes": f.raw_boxes or [], "edited_excerpt": f.edited_excerpt, "edited_page": f.edited_page,
        "edited_boxes": f.edited_boxes or [], "suggested_correction": f.suggested_correction,
        "needs_human_attention": f.needs_human_attention, "review_status": f.review_status, "reviewer_id": f.reviewer_id,
        "reviewer_comment": f.reviewer_comment, "reviewed_at": iso(f.reviewed_at), "original": f.original,
    }


def run_out(r: QCRun, with_stats: bool = True) -> dict:
    return {"id": r.id, "form_id": r.form_id, "attempt_no": r.attempt_no, "status": r.status, "stages": r.stages or [],
            "ai_status": r.ai_status, "ai_provider": r.ai_provider, "ai_model": r.ai_model, "ai_message": r.ai_message,
            "rule_versions": r.rule_versions or [], "stats": r.stats if with_stats else None, "error": r.error,
            "edited_document_id": r.edited_document_id, "raw_document_id": r.raw_document_id,
            "created_at": iso(r.created_at), "started_at": iso(r.started_at), "finished_at": iso(r.finished_at)}


def attempt_out(a: ReviewAttempt, reviewer: User | None = None) -> dict:
    return {"id": a.id, "form_id": a.form_id, "attempt_no": a.attempt_no, "run_id": a.run_id,
            "edited_document_id": a.edited_document_id, "reviewer": user_brief(reviewer), "decision": a.decision,
            "summary": a.summary, "feedback_to_editor": a.feedback_to_editor, "accepted_count": a.accepted_count,
            "rejected_count": a.rejected_count, "other_count": a.other_count, "decided_at": iso(a.decided_at)}


def comment_out(c: FormComment, author: User | None = None) -> dict:
    return {"id": c.id, "kind": c.kind, "body": c.body, "attempt_id": c.attempt_id, "author": user_brief(author),
            "created_at": iso(c.created_at)}


def assignment_out(a: Assignment, assignee: User | None, by: User | None) -> dict:
    return {"id": a.id, "role_kind": a.role_kind, "assignee": user_brief(assignee), "assigned_by": user_brief(by),
            "note": a.note, "active": a.active, "assigned_at": iso(a.assigned_at), "ended_at": iso(a.ended_at)}


def exception_out(e: ExceptionItem) -> dict:
    return {"id": e.id, "project_id": e.project_id, "form_id": e.form_id, "form_code": e.form_code, "filename": e.filename,
            "issue_type": e.issue_type, "detail": e.detail, "status": e.status, "created_at": iso(e.created_at),
            "resolved_at": iso(e.resolved_at), "resolution_note": e.resolution_note, "derived": False}


def rule_out(rv: RuleVersion, owner: User | None = None) -> dict:
    return {"id": rv.id, "rule_id": rv.rule_id, "rule_key": rv.rule.rule_key, "version": rv.version, "name": rv.name,
            "description": rv.description, "category": rv.category, "category_label": tx.category_label(rv.category),
            "error_subtype": rv.error_subtype, "error_label": tx.label(rv.error_subtype), "in_qc": in_qc(rv),
            "severity": rv.severity, "client_id": rv.client_id,
            "project_id": rv.project_id, "form_type": rv.form_type, "scope": scope_level(rv), "check_type": rv.check_type,
            "config": rv.config or {}, "examples": rv.examples, "exceptions": rv.exceptions,
            "effective_date": iso(rv.effective_date), "owner": user_brief(owner), "status": rv.status,
            "change_note": rv.change_note, "created_at": iso(rv.created_at), "submitted_at": iso(rv.submitted_at),
            "approved_at": iso(rv.approved_at), "approved_by": rv.approved_by, "created_by": rv.created_by,
            "rejection_reason": rv.rejection_reason}


def audit_out(e: AuditEvent) -> dict:
    return {"id": e.id, "created_at": iso(e.created_at), "actor_id": e.actor_id, "actor": e.actor_label, "action": e.action,
            "entity_type": e.entity_type, "entity_id": e.entity_id, "form_id": e.form_id, "project_id": e.project_id,
            "detail": e.detail, "ip": e.ip}


def autoedit_out(a: AutoEdit) -> dict:
    return {"id": a.id, "form_id": a.form_id, "status": a.status, "engine": a.engine, "ai_status": a.ai_status, "ai_model": a.ai_model,
            "message": a.message, "edits": a.edits or [], "skipped": a.skipped or [], "stats": a.stats or {},
            "base_document_id": a.base_document_id, "result_document_id": a.result_document_id,
            "created_at": iso(a.created_at), "finished_at": iso(a.finished_at)}
