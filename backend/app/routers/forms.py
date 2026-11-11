"""Forms, documents and the document repository."""
from __future__ import annotations

from datetime import datetime

import pymupdf
from fastapi import APIRouter, Depends, File, Form as FormField, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db, utcnow
from ..deps import get_current_user, needs
from ..enums import FormStatus, Role
from ..models import (Assignment, AutoEdit, Client, MediaLink, Document, ExceptionItem, Finding, Form, FormComment, Project, QCRun,
                      ReviewAttempt, User)
from ..permissions import (accessible_project_ids, form_scope_clause, get_form_or_404, has_perm)
from ..serializers import (assignment_out, autoedit_out, attempt_out, comment_out, document_out, exception_out, form_out, iso,
                           run_out)
from ..services import auto_editor, jobs, naming, pipeline, repository, workflow
from ..storage import StorageNotConfigured, get_storage

router = APIRouter(prefix="/api", tags=["forms"])
SORTS = {"updated": Form.updated_at.desc(), "created": Form.created_at.desc(), "code": Form.form_code.asc(),
         "due": Form.due_at.asc(), "status": Form.status.asc()}


# ------------------------------------------------------------------------------ listing
@router.get("/forms")
def list_forms(q: str | None = None, project_id: int | None = None, client_id: int | None = None, status: str | None = None,
                editor_id: int | None = None, reviewer_id: int | None = None, pair: str | None = None,
                has_version: int | None = Query(None, ge=1), data: str = Query("all", pattern="^(all|real|sample)$"),
                date_from: datetime | None = None, date_to: datetime | None = None, mine: bool = False,
                sort: str = "updated", limit: int = Query(50, le=200), offset: int = 0,
                user: User = Depends(needs("forms.view")), db: Session = Depends(get_db)):
    stmt = select(Form).where(form_scope_clause(db, user))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Form.form_code.ilike(like), Form.id.in_(
            select(Document.form_id).where(or_(Document.logical_name.ilike(like), Document.original_filename.ilike(like), Document.sha256.ilike(like))))))
    if project_id:
        stmt = stmt.where(Form.project_id == project_id)
    if client_id:
        stmt = stmt.where(Form.project_id.in_(select(Project.id).where(Project.client_id == client_id)))
    if status:
        stmt = stmt.where(Form.status.in_(status.split(",")))
    if editor_id:
        stmt = stmt.where(Form.assigned_editor_id == editor_id)
    if reviewer_id:
        stmt = stmt.where(Form.assigned_reviewer_id == reviewer_id)
    if mine:
        stmt = stmt.where(or_(Form.assigned_editor_id == user.id, Form.assigned_reviewer_id == user.id))
    if has_version:
        stmt = stmt.where(Form.id.in_(select(Document.form_id).where(Document.kind == "edited", Document.version_no >= has_version)))
    if data != "all":
        stmt = stmt.where(Form.is_sample.is_(data == "sample"))
    if date_from:
        stmt = stmt.where(Form.created_at >= date_from)
    if date_to:
        stmt = stmt.where(Form.created_at <= date_to)
    if pair == "missing_edited":
        stmt = stmt.where(Form.id.in_(select(Document.form_id).where(Document.kind == "raw")),
                          ~Form.id.in_(select(Document.form_id).where(Document.kind == "edited")))
    elif pair == "missing_raw":
        stmt = stmt.where(~Form.id.in_(select(Document.form_id).where(Document.kind == "raw")))
    elif pair == "complete":
        stmt = stmt.where(Form.id.in_(select(Document.form_id).where(Document.kind == "raw")),
                          Form.id.in_(select(Document.form_id).where(Document.kind == "edited")))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(SORTS.get(sort, SORTS["updated"]), Form.id.desc()).limit(limit).offset(offset)).scalars().all()
    counts = dict(db.execute(select(MediaLink.form_id, func.count()).where(MediaLink.form_id.in_([f.id for f in rows])).group_by(MediaLink.form_id)).all()) if rows else {}
    items = [form_out(f) for f in rows]
    for it in items:
        it["media_count"] = counts.get(it["id"], 0) if has_perm(user, "media.view") else 0
    return {"total": total, "items": items}


@router.get("/forms/{form_id}")
def form_detail(form_id: int, user: User = Depends(needs("forms.view")), db: Session = Depends(get_db)):
    f = get_form_or_404(db, user, form_id)
    out = form_out(f, detail=True)
    users = {u.id: u for u in db.execute(select(User)).scalars()}
    is_editor = user.role == Role.EDITOR.value
    assignments = db.execute(select(Assignment).where(Assignment.form_id == f.id).order_by(Assignment.id.desc())).scalars().all()
    out["assignments"] = [assignment_out(a, users.get(a.assignee_id), users.get(a.assigned_by)) for a in assignments]
    out["comments"] = [comment_out(c, users.get(c.author_id)) for c in
                       db.execute(select(FormComment).where(FormComment.form_id == f.id).order_by(FormComment.id)).scalars()]
    attempts = db.execute(select(ReviewAttempt).where(ReviewAttempt.form_id == f.id).order_by(ReviewAttempt.id)).scalars().all()
    out["attempts"] = [attempt_out(a, users.get(a.reviewer_id)) for a in attempts]
    if has_perm(user, "qc.view"):
        runs = db.execute(select(QCRun).where(QCRun.form_id == f.id).order_by(QCRun.id.desc())).scalars().all()
        out["runs"] = [run_out(r, with_stats=not is_editor) for r in runs]
    else:
        out["runs"] = []
    # Editors receive feedback and CONFIRMED findings only, never unvalidated AI output.
    if is_editor:
        confirmed = db.execute(select(Finding).where(Finding.form_id == f.id, Finding.review_status.in_(["accepted", "edited"]))
                               .order_by(Finding.id)).scalars().all()
        from ..serializers import finding_out
        out["confirmed_findings"] = [finding_out(x) for x in confirmed]
    out["media_count"] = db.scalar(select(func.count()).select_from(MediaLink).where(MediaLink.form_id == f.id)) if has_perm(user, "media.view") else 0
    out["logical_path"] = f"{get_settings().workspace_name} / {f.project.client.name} / {f.project.name} / {f.form_code}"
    return out


# ------------------------------------------------------------------------------ uploads
def _resolve_project(db: Session, user: User, project_id: int, kind: str, form_code: str | None) -> Project:
    project = db.get(Project, project_id)
    if not project or not project.is_active:
        raise HTTPException(404, "Project not found or inactive.")
    if kind == "raw":
        ids = accessible_project_ids(db, user)
        if ids is not None and project_id not in ids:
            raise HTTPException(404, "Project not found or not within your access.")
        return project
    # edited uploads: editors only for forms assigned to them; managers need project access
    if user.role == Role.EDITOR.value:
        ok = form_code and db.execute(select(Form.id).where(Form.project_id == project_id, Form.form_code == form_code,
                                                            Form.assigned_editor_id == user.id)).first()
        if not ok:
            raise HTTPException(403, "You can only upload edited PDFs for forms assigned to you.")
    else:
        ids = accessible_project_ids(db, user)
        if ids is not None and project_id not in ids:
            raise HTTPException(404, "Project not found or not within your access.")
    return project


def _queue_after_upload(db: Session, user: User, form: Form, doc: Document, ai_edit: bool = False) -> None:
    if doc.kind == "raw":
        if ai_edit:  # the AI editor extracts the text itself, then submits the edited PDF to QC
            ae = auto_editor.create_autoedit(db, form, doc, None)
            jobs.submit(auto_editor.run_autoedit_job, ae.id)
        else:
            jobs.submit(pipeline.extract_document_job, doc.id)
    else:
        db.refresh(form)
        run = pipeline.create_run(db, form, doc, user)
        jobs.submit(pipeline.run_job, run.id)


@router.post("/repository/upload")
async def upload(request: Request, project_id: int = FormField(...), files: list[UploadFile] = File(...),
                 form_code: str | None = FormField(None), kind: str | None = FormField(None), note: str | None = FormField(None),
                 ai_edit: bool | None = FormField(None), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Upload one or many PDFs. File names must follow FORMID_1.pdf / FORMID_2.pdf unless Form ID and type are given
    explicitly (single file). Non-compliant files go to the exception queue."""
    if not (has_perm(user, "docs.upload_raw") or has_perm(user, "docs.upload_edited")):
        raise HTTPException(403, "Your role cannot upload documents.")
    if kind and kind not in ("raw", "edited"):
        raise HTTPException(422, "kind must be 'raw' or 'edited'.")
    if len(files) > 50:
        raise HTTPException(422, "Upload at most 50 files per request.")
    storage = get_storage()
    results = []
    s = get_settings()
    do_ai_edit = s.ai_edit_on_upload if ai_edit is None else ai_edit
    for up in files:
        name = up.filename or "unnamed.pdf"
        entry = {"filename": name, "ok": False}
        try:
            data = await up.read(s.max_upload_bytes + 1)
            parsed = naming.parse_filename(name)
            fcode, fkind = (parsed if parsed else (None, None))
            if len(files) == 1 and form_code and kind:
                fcode, fkind = naming.normalize_form_code(form_code), kind
            if not fcode:
                project = db.get(Project, project_id)
                item = repository.open_exception(
                    db, issue_type="naming_error", project=project, filename=name, user=user,
                    detail=f"'{name}' does not follow the naming convention FORMID_1.pdf (RAW) / FORMID_2.pdf (edited).",
                    quarantine_key=repository._quarantine(storage, name, data) if len(data) <= s.max_upload_bytes else None)
                audit(db, user, "document.rejected", entity_type="exception", entity_id=item.id, project_id=project_id,
                      detail={"issue": "naming_error", "filename": name}, request=request)
                db.commit()
                entry.update(error=item.detail, exception_id=item.id)
                results.append(entry)
                continue
            perm = "docs.upload_raw" if fkind == "raw" else "docs.upload_edited"
            if not has_perm(user, perm):
                entry["error"] = f"Your role may not upload {'RAW' if fkind == 'raw' else 'edited'} PDFs."
                results.append(entry)
                continue
            project = _resolve_project(db, user, project_id, fkind, fcode)
            res = repository.ingest_pdf(db, storage, user, project=project, form_code=fcode, kind=fkind, original_filename=name,
                                        data=data, request=request, note=note)
            _queue_after_upload(db, user, res.form, res.document, ai_edit=do_ai_edit)
            entry.update(ok=True, ai_edit_queued=bool(do_ai_edit and fkind == 'raw'), form_id=res.form.id, form_code=res.form.form_code, document_id=res.document.id,
                         logical_name=res.document.logical_name, version=res.document.version_no, warnings=res.warnings)
        except repository.IngestError as exc:
            entry.update(error=exc.message, exception_id=exc.exception_id)
        except HTTPException as exc:
            entry["error"] = exc.detail
        except StorageNotConfigured as exc:
            entry["error"] = str(exc)
        results.append(entry)
    ok = sum(1 for r in results if r["ok"])
    return {"uploaded": ok, "failed": len(results) - ok, "results": results}


# ------------------------------------------------------------------------------ documents
def _doc_or_404(db: Session, user: User, doc_id: int) -> tuple[Document, Form]:
    if not has_perm(user, "docs.view"):
        raise HTTPException(403, "Your role cannot view documents.")
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    form = get_form_or_404(db, user, doc.form_id)
    return doc, form


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc, form = _doc_or_404(db, user, doc_id)
    audit(db, user, "document.downloaded", entity_type="document", entity_id=doc.id, form_id=form.id, project_id=form.project_id, request=request)
    db.commit()
    return Response(get_storage().get(doc.storage_key), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{doc.stored_name}"', "Cache-Control": "private, max-age=300",
                             "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.get("/documents/{doc_id}/pages/{page_no}/image")
def page_image(doc_id: int, page_no: int, zoom: float = Query(1.5, ge=0.5, le=4.0), user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    doc, _ = _doc_or_404(db, user, doc_id)
    if page_no < 1 or (doc.page_count and page_no > doc.page_count):
        raise HTTPException(404, "Page out of range.")
    pdf = pymupdf.open(stream=get_storage().get(doc.storage_key), filetype="pdf")
    try:
        pix = pdf.load_page(page_no - 1).get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        png = pix.tobytes("png")
    finally:
        pdf.close()
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"})


@router.get("/documents/{doc_id}/text")
def document_text(doc_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc, _ = _doc_or_404(db, user, doc_id)
    if doc.extraction_status == "pending":
        try:
            pipeline.ensure_extracted(db, doc)
        except Exception:  # noqa: BLE001
            pass
    return {"document": document_out(doc), "pages": [
        {"page_no": p.page_no, "text": p.text, "method": p.method, "note": p.note, "width": p.width, "height": p.height,
         "ocr_confidence": p.ocr_confidence} for p in doc.pages]}


# ------------------------------------------------------------------------------ assignment & workflow actions
class AssignIn(BaseModel):
    editor_id: int | None = None
    reviewer_id: int | None = None
    due_at: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)


@router.post("/forms/{form_id}/assign")
def assign(form_id: int, body: AssignIn, request: Request, user: User = Depends(needs("forms.assign")), db: Session = Depends(get_db)):
    f = get_form_or_404(db, user, form_id)
    if f.status in (FormStatus.APPROVED.value, FormStatus.CLOSED.value):
        raise HTTPException(409, "Approved/closed forms cannot be reassigned.")
    if not (body.editor_id or body.reviewer_id or body.due_at):
        raise HTTPException(422, "Nothing to assign.")
    workflow.assign(db, user, f, editor_id=body.editor_id, reviewer_id=body.reviewer_id, due_at=body.due_at, note=body.note, request=request)
    db.commit()
    return form_out(f, detail=True)


@router.post("/forms/{form_id}/start")
def start_work(form_id: int, request: Request, user: User = Depends(needs("workflow.start")), db: Session = Depends(get_db)):
    f = get_form_or_404(db, user, form_id)
    if user.role == Role.EDITOR.value and f.assigned_editor_id != user.id:
        raise HTTPException(403, "This form is not assigned to you.")
    if f.status not in (FormStatus.PENDING.value, FormStatus.RE_EDIT_REQUIRED.value):
        raise HTTPException(409, f"Work cannot be started from '{f.status}'.")
    if f.assigned_editor_id is None:
        f.assigned_editor_id = user.id
    workflow.change_status(db, user, f, FormStatus.IN_PROGRESS.value, "Editing started", request)
    db.commit()
    return form_out(f)


class StatusIn(BaseModel):
    action: str = Field(pattern="^(block|unblock|close)$")
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/forms/{form_id}/status")
def status_action(form_id: int, body: StatusIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    f = get_form_or_404(db, user, form_id)
    if body.action == "close":
        if not has_perm(user, "workflow.close"):
            raise HTTPException(403, "Your role cannot close forms.")
        workflow.change_status(db, user, f, FormStatus.CLOSED.value, body.reason or "Closed", request)
    elif body.action == "block":
        if not has_perm(user, "workflow.block"):
            raise HTTPException(403, "Your role cannot block forms.")
        if not (body.reason or "").strip():
            raise HTTPException(422, "A reason is required.")
        workflow.change_status(db, user, f, FormStatus.BLOCKED.value, body.reason, request)
    else:
        if not has_perm(user, "workflow.block"):
            raise HTTPException(403, "Your role cannot unblock forms.")
        has_edited = any(d.kind == "edited" for d in f.documents)
        workflow.change_status(db, user, f, FormStatus.QC_REVIEW.value if has_edited else FormStatus.PENDING.value,
                               body.reason or "Unblocked", request)
    db.commit()
    return form_out(f)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/forms/{form_id}/comments", status_code=201)
def add_comment(form_id: int, body: CommentIn, request: Request, user: User = Depends(needs("comments.add")), db: Session = Depends(get_db)):
    f = get_form_or_404(db, user, form_id)
    c = FormComment(form_id=f.id, author_id=user.id, kind="comment", body=body.body.strip())
    db.add(c)
    audit(db, user, "form.comment_added", entity_type="form", entity_id=f.id, form_id=f.id, project_id=f.project_id, request=request)
    other = f.assigned_reviewer_id if user.id == f.assigned_editor_id else f.assigned_editor_id
    workflow.notify(db, other, "comment", f"New comment on {f.form_code}.", f)
    db.commit()
    return comment_out(c, user)


class EscalateIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/forms/{form_id}/escalate")
def escalate(form_id: int, body: EscalateIn, request: Request, user: User = Depends(needs("workflow.escalate")), db: Session = Depends(get_db)):
    f = get_form_or_404(db, user, form_id)
    if f.status != FormStatus.QC_REVIEW.value:
        raise HTTPException(409, "Only forms in QC Review can be escalated.")
    db.add(FormComment(form_id=f.id, author_id=user.id, kind="escalation", body=body.reason.strip()))
    workflow.change_status(db, user, f, FormStatus.MANUAL_REVIEW.value, f"Escalated: {body.reason.strip()}", request)
    audit(db, user, "form.escalated", entity_type="form", entity_id=f.id, form_id=f.id, project_id=f.project_id,
          detail={"reason": body.reason}, request=request)
    db.commit()
    return form_out(f)


@router.post("/forms/{form_id}/resolve-manual")
def resolve_manual(form_id: int, request: Request, user: User = Depends(needs("workflow.resolve_manual")), db: Session = Depends(get_db)):
    """Return a manual-review/escalated form to QC Review so a reviewer can decide it."""
    f = get_form_or_404(db, user, form_id)
    workflow.change_status(db, user, f, FormStatus.QC_REVIEW.value, "Returned to QC review after manual review", request)
    db.commit()
    return form_out(f)


# ------------------------------------------------------------------------------ AI editor
@router.post("/forms/{form_id}/ai-edit", status_code=202)
def run_ai_edit(form_id: int, request: Request, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    """Let the AI editor correct grammar/spelling and submit the result to QC (RAW first time; the previous edit when
    QC has sent the form back)."""
    f = get_form_or_404(db, user, form_id)
    if f.status not in (FormStatus.PENDING.value, FormStatus.IN_PROGRESS.value, FormStatus.RE_EDIT_REQUIRED.value):
        raise HTTPException(409, f"The AI editor can only run while a form is Pending, In Progress or Re-edit Required (it is '{f.status}').")
    base = auto_editor.choose_base(f)
    if base is None or base.status != "stored":
        raise HTTPException(409, "There is no RAW PDF to edit.")
    busy = db.execute(select(AutoEdit).where(AutoEdit.form_id == f.id, AutoEdit.status.in_(["queued", "running"]))).scalars().first()
    if busy:
        raise HTTPException(409, "The AI editor is already working on this form.")
    ae = auto_editor.create_autoedit(db, f, base, user)
    jobs.submit(auto_editor.run_autoedit_job, ae.id)
    db.refresh(ae)
    return autoedit_out(ae)


@router.get("/forms/{form_id}/ai-edits")
def list_ai_edits(form_id: int, user: User = Depends(needs("aiedit.view")), db: Session = Depends(get_db)):
    f = get_form_or_404(db, user, form_id)
    rows = db.execute(select(AutoEdit).where(AutoEdit.form_id == f.id).order_by(AutoEdit.id.desc())).scalars().all()
    return [autoedit_out(a) for a in rows]


# ------------------------------------------------------------------------------ repository views
@router.get("/repository/tree")
def tree(user: User = Depends(needs("forms.view")), db: Session = Depends(get_db)):
    ids = accessible_project_ids(db, user)
    clients = db.execute(select(Client).order_by(Client.name)).scalars().all()
    counts = {pid: (n, st) for pid, n, st in db.execute(
        select(Form.project_id, func.count(), func.sum(case((Form.status.in_(["approved", "closed"]), 1), else_=0)))
        .where(form_scope_clause(db, user)).group_by(Form.project_id)).all()}
    out = []
    for c in clients:
        projects = [p for p in c.projects if (ids is None or p.id in ids or p.id in counts)]
        if not projects:
            continue
        out.append({"id": c.id, "name": c.name, "projects": [
            {"id": p.id, "name": p.name, "forms": counts.get(p.id, (0, 0))[0], "approved": int(counts.get(p.id, (0, 0))[1] or 0),
             "is_sample": p.is_sample} for p in projects]})
    return {"workspace": get_settings().workspace_name, "clients": out}


@router.get("/repository/exceptions")
def exceptions(status: str = "open", user: User = Depends(needs("repository.exceptions")), db: Session = Depends(get_db)):
    stmt = select(ExceptionItem).where(ExceptionItem.status == status).order_by(ExceptionItem.id.desc())
    ids = accessible_project_ids(db, user)
    if ids is not None:
        stmt = stmt.where(or_(ExceptionItem.project_id.in_(ids), ExceptionItem.project_id.is_(None) & (ExceptionItem.created_by == user.id)))
    items = [exception_out(e) for e in db.execute(stmt.limit(300)).scalars()]
    if status == "open":
        forms = db.execute(select(Form).where(form_scope_clause(db, user))).scalars().all()
        for d in repository.derived_issues(db, forms):
            items.append({"id": d["key"], "project_id": d["project_id"], "form_id": d["form_id"], "form_code": d["form_code"],
                          "filename": None, "issue_type": d["issue_type"], "detail": d["detail"], "status": "open",
                          "created_at": iso(d["created_at"]), "resolved_at": None, "resolution_note": None, "derived": True})
    return {"total": len(items), "items": items}


class ResolveIn(BaseModel):
    status: str = Field(default="resolved", pattern="^(resolved|dismissed)$")
    note: str | None = Field(default=None, max_length=1000)


@router.post("/repository/exceptions/{item_id}/resolve")
def resolve_exception(item_id: int, body: ResolveIn, request: Request, user: User = Depends(needs("repository.exceptions")),
                      db: Session = Depends(get_db)):
    e = db.get(ExceptionItem, item_id)
    if not e:
        raise HTTPException(404, "Exception not found.")
    ids = accessible_project_ids(db, user)
    if ids is not None and e.project_id not in ids:
        raise HTTPException(404, "Exception not found.")
    e.status, e.resolved_by, e.resolved_at, e.resolution_note = body.status, user.id, utcnow(), body.note
    audit(db, user, f"exception.{body.status}", entity_type="exception", entity_id=e.id, project_id=e.project_id,
          detail={"note": body.note}, request=request)
    db.commit()
    return exception_out(e)


@router.post("/repository/scan")
def scan_storage(request: Request, user: User = Depends(needs("repository.exceptions")), db: Session = Depends(get_db)):
    """Reconcile files placed directly in the storage hierarchy (e.g. via a shared drive) with the database."""
    s = get_settings()
    storage = get_storage()
    known = {k for (k,) in db.execute(select(Document.storage_key)).all()}
    registered = skipped = problems = 0
    clients = {c.folder: c for c in db.execute(select(Client)).scalars()}
    ids = accessible_project_ids(db, user)
    for key in storage.list_keys(s.workspace_name):
        parts = key.split("/")
        if key in known or len(parts) != 4 or parts[1].startswith("_"):
            skipped += 1
            continue
        _, cfolder, pfolder, fname = parts
        client = clients.get(cfolder)
        project = next((p for p in client.projects if p.folder == pfolder), None) if client else None
        if not project or (ids is not None and project.id not in ids):
            repository.open_exception(db, issue_type="unknown_folder", filename=key, user=user,
                                      detail=f"File '{key}' is not under a known Client/Project folder.")
            problems += 1
            continue
        parsed = naming.parse_filename(fname)
        if not parsed:
            if not fname.lower().endswith(".pdf") or "_r" not in fname:  # versioned resubmission names are system generated
                repository.open_exception(db, issue_type="naming_error", project=project, filename=key, user=user,
                                          detail=f"'{fname}' does not follow FORMID_1.pdf / FORMID_2.pdf.")
                problems += 1
            continue
        fcode, kind = parsed
        try:
            res = repository.ingest_pdf(db, storage, user, project=project, form_code=fcode, kind=kind, original_filename=fname,
                                        data=storage.get(key), existing_key=key, request=request)
            _queue_after_upload(db, user, res.form, res.document)
            registered += 1
        except repository.IngestError:
            problems += 1
    db.commit()
    return {"registered": registered, "skipped": skipped, "problems": problems}
