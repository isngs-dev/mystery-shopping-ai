"""Document repository: ingestion, immutable versioned storage, pair health, exception queue.

Hierarchy:  <Workspace>/<Client>/<Project or Editor>/FORMID_1.pdf (RAW) | FORMID_2.pdf (edited)
Resubmissions: FORMID_2_r<N>_<timestamp>.pdf. Stored files are never overwritten.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import utcnow
from ..enums import DocKind, FormStatus
from ..models import Document, ExceptionItem, Form, FormComment, Project, ProjectReviewer, User
from ..storage import StorageAdapter, safe_segment
from . import naming
from .extraction import validate_pdf


class IngestError(Exception):
    def __init__(self, message: str, status: int = 400, exception_id: int | None = None):
        super().__init__(message)
        self.message, self.status, self.exception_id = message, status, exception_id


@dataclass
class IngestResult:
    form: Form
    document: Document
    warnings: list[str] = field(default_factory=list)
    created_form: bool = False


def folder_prefix(project: Project) -> str:
    s = get_settings()
    return "/".join([safe_segment(s.workspace_name), safe_segment(project.client.folder), safe_segment(project.folder)])


def logical_path(project: Project, form_code: str) -> str:
    return f"{project.client.name} / {project.name} / {form_code}"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def open_exception(db: Session, *, issue_type: str, detail: str, project: Project | None = None,
                   form: Form | None = None, form_code: str | None = None, filename: str | None = None,
                   quarantine_key: str | None = None, user: User | None = None) -> ExceptionItem:
    item = ExceptionItem(
        project_id=project.id if project else None, form_id=form.id if form else None,
        form_code=form_code or (form.form_code if form else None), filename=filename, issue_type=issue_type,
        detail=detail, quarantine_key=quarantine_key, created_by=user.id if user else None,
    )
    db.add(item)
    db.flush()
    return item


def _quarantine(storage: StorageAdapter, filename: str, data: bytes) -> str | None:
    s = get_settings()
    key = "/".join([safe_segment(s.workspace_name), "_quarantine", f"{utcnow().strftime('%Y%m%dT%H%M%S%f')}_{safe_segment(filename)}"])
    try:
        storage.put_immutable(key, data)
        return key
    except Exception:  # noqa: BLE001
        return None


def get_or_create_form(db: Session, project: Project, form_code: str, user: User | None) -> tuple[Form, bool]:
    form = db.execute(select(Form).where(Form.project_id == project.id, Form.form_code == form_code)).scalar_one_or_none()
    if form:
        return form, False
    owner = db.get(ProjectReviewer, project.id)        # a project handed to a QC lead: its new forms go straight to that person
    form = Form(project_id=project.id, form_code=form_code, form_type=project.form_type, assigned_reviewer_id=owner.reviewer_id if owner else None)
    db.add(form)
    db.flush()
    audit(db, user, "form.created", entity_type="form", entity_id=form.id, form_id=form.id, project_id=project.id,
          detail={"form_code": form_code})
    return form, True


def ingest_pdf(db: Session, storage: StorageAdapter, user: User, *, project: Project, form_code: str, kind: str,
               original_filename: str, data: bytes, existing_key: str | None = None, request=None,
               note: str | None = None, is_sample: bool = False) -> IngestResult:
    """Validate and store one PDF. Raises IngestError (with an exception-queue record) on failure."""
    s = get_settings()
    form_code = naming.normalize_form_code(form_code)
    if not naming.FORM_CODE_RE.match(form_code):
        raise IngestError(f"Invalid Form ID '{form_code}'. Use letters, digits and hyphens (max 40).")

    def fail(issue: str, msg: str, status: int = 400, form: Form | None = None, quarantine: bool = True):
        qkey = _quarantine(storage, original_filename, data) if quarantine and existing_key is None else None
        item = open_exception(db, issue_type=issue, detail=msg, project=project, form=form, form_code=form_code,
                              filename=original_filename, quarantine_key=qkey, user=user)
        audit(db, user, "document.rejected", entity_type="exception", entity_id=item.id, form_id=form.id if form else None,
              project_id=project.id, detail={"issue": issue, "message": msg, "filename": original_filename}, request=request)
        db.commit()
        raise IngestError(msg, status, item.id)

    if len(data) > s.max_upload_bytes:
        fail("too_large", f"File is {len(data) / 1e6:.1f} MB; limit is {s.max_upload_mb} MB.", 413, quarantine=False)

    val = validate_pdf(data)
    if not val.ok:
        fail("invalid_pdf" if val.status == "invalid" else "unreadable", val.error or "Invalid PDF.")

    digest = sha256_of(data)
    form = db.execute(select(Form).where(Form.project_id == project.id, Form.form_code == form_code)).scalar_one_or_none()

    if kind == DocKind.RAW.value:
        if form:
            existing_raw = next((d for d in form.documents if d.kind == "raw"), None)
            if existing_raw:
                same = " (identical file)" if existing_raw.sha256 == digest else ""
                fail("duplicate", f"Duplicate Form ID: a RAW PDF already exists for {form_code} in this project{same}. "
                                  "The original is immutable and cannot be replaced.", 409, form=form)
        # same content already stored under a different Form ID => likely duplicate intake
        dup = db.execute(select(Document).where(Document.sha256 == digest, Document.kind == "raw")).scalars().first()
        dup_warning = None
        if dup:
            dup_warning = f"Identical content already stored as {dup.logical_name} (Form {dup.form.form_code})."
    else:
        if not form:
            fail("missing_raw", f"No RAW PDF/Form exists for {form_code}. Upload {form_code}_1.pdf first.", 409)
        if form.status in (FormStatus.APPROVED.value, FormStatus.CLOSED.value):
            fail("invalid_state", f"{form_code} is {form.status}; resubmissions are not accepted.", 409, form=form,
                 quarantine=False)
        latest = max((d.version_no for d in form.documents if d.kind == "edited"), default=0)
        prev = next((d for d in form.documents if d.kind == "edited" and d.version_no == latest), None)
        if prev and prev.sha256 == digest:
            fail("duplicate", f"Identical to the previously submitted edited PDF (v{latest}); nothing changed.", 409,
                 form=form, quarantine=False)
        dup_warning = None

    form, created = get_or_create_form(db, project, form_code, user)
    version = 1
    if kind == DocKind.EDITED.value:
        version = max((d.version_no for d in form.documents if d.kind == "edited"), default=0) + 1
    now = utcnow()
    logical = naming.logical_name(form_code, kind)
    stored = naming.stored_name(form_code, kind, version, now)
    key = existing_key or "/".join([folder_prefix(project), stored])
    if existing_key is None:
        try:
            storage.put_immutable(key, data)
        except FileExistsError:
            fail("duplicate", f"Storage already contains {key}; refusing to overwrite.", 409, form=form, quarantine=False)

    doc = Document(
        form=form, kind=kind, version_no=version, logical_name=logical, stored_name=stored,
        original_filename=original_filename, storage_key=key, sha256=digest, size_bytes=len(data),
        page_count=val.page_count, status="stored", uploaded_by=user.id, uploaded_at=now, is_sample=is_sample,
    )
    db.add(doc)
    db.flush()
    warnings = list(val.warnings)
    if dup_warning:
        warnings.append(dup_warning)
    if original_filename.lower() != stored.lower() and naming.parse_filename(original_filename) is None:
        warnings.append(f"File name '{original_filename}' does not follow the convention; stored as {stored} "
                        "(Form ID and type were supplied explicitly).")

    if kind == DocKind.EDITED.value:
        if form.first_submitted_at is None:
            form.first_submitted_at = now
        if form.assigned_editor_id is None and user.role == "editor":
            form.assigned_editor_id = user.id
        previous_status = form.status
        form.status = FormStatus.QC_REVIEW.value
        form.status_reason = "Edited PDF submitted" if version == 1 else f"Resubmission v{version}"
        if note or version > 1:
            db.add(FormComment(form_id=form.id, author_id=user.id, kind="resubmission",
                               body=note or f"Resubmitted edited PDF (version {version})."))
        audit(db, user, "form.status_changed", entity_type="form", entity_id=form.id, form_id=form.id,
              project_id=project.id, detail={"from": previous_status, "to": form.status, "reason": form.status_reason})
    audit(db, user, "document.uploaded" if version == 1 else "document.resubmitted", entity_type="document",
          entity_id=doc.id, form_id=form.id, project_id=project.id,
          detail={"kind": kind, "version": version, "stored_name": stored, "sha256": digest, "size": len(data),
                  "warnings": warnings, "logical_path": logical_path(project, form_code)}, request=request)
    db.commit()
    from . import qc_reports

    qc_reports.mark_dirty(project.id)                 # a new / changed document: the project's Excel QC report follows
    return IngestResult(form=form, document=doc, warnings=warnings, created_form=created)


# --------------------------------------------------------------------------------- pair health
def pair_status(form: Form) -> dict:
    raw = [d for d in form.documents if d.kind == "raw"]
    edited = [d for d in form.documents if d.kind == "edited"]
    if raw and edited:
        state = "complete"
    elif raw:
        state = "missing_edited"
    elif edited:
        state = "missing_raw"
    else:
        state = "empty"
    return {"state": state, "raw": bool(raw), "edited_versions": len(edited)}


def derived_issues(db: Session, forms: list[Form]) -> list[dict]:
    """Issues computed live from repository state (missing pairs, unreadable extraction)."""
    out = []
    now = utcnow()
    for f in forms:
        ps = pair_status(f)
        if ps["state"] == "missing_raw":
            out.append({"key": f"missing_raw:{f.id}", "form_id": f.id, "project_id": f.project_id, "form_code": f.form_code,
                        "issue_type": "missing_raw", "detail": "Edited PDF exists but no RAW (_1) PDF was uploaded.",
                        "created_at": f.created_at})
        elif ps["state"] == "missing_edited" and (f.status != FormStatus.PENDING.value or (now - f.created_at).total_seconds() > 48 * 3600):
            out.append({"key": f"missing_edited:{f.id}", "form_id": f.id, "project_id": f.project_id, "form_code": f.form_code,
                        "issue_type": "missing_edited", "detail": "RAW PDF present but no edited (_2) PDF has been submitted.",
                        "created_at": f.created_at})
        for d in f.documents:
            if d.extraction_status in ("failed", "ocr_unavailable", "partial"):
                out.append({"key": f"extract:{d.id}", "form_id": f.id, "project_id": f.project_id, "form_code": f.form_code,
                            "issue_type": "extraction_" + d.extraction_status,
                            "detail": f"{d.logical_name} v{d.version_no}: extraction {d.extraction_status.replace('_', ' ')}.",
                            "created_at": d.uploaded_at})
    return out
