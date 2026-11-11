"""Relational schema. Every table carries timestamps; history is append-only wherever the
workflow requires it (documents, QC runs, findings, review attempts, audit events)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    # Project coordinator: sees every project and hands projects to QC leads (like the manager), without any other manager right.
    is_coordinator: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # bump to revoke sessions


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    folder: Mapped[str] = mapped_column(String(160), unique=True)  # filesystem-safe name
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    projects: Mapped[list[Project]] = relationship(back_populates="client")


class Project(Base):
    """A project (or editor workspace) folder beneath a client."""
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("client_id", "folder"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    name: Mapped[str] = mapped_column(String(160))
    folder: Mapped[str] = mapped_column(String(160))
    editor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # editor-named folder
    form_type: Mapped[str] = mapped_column(String(64), default="standard")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    client: Mapped[Client] = relationship(back_populates="projects")


class UserProjectAccess(Base):
    __tablename__ = "user_project_access"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Form(Base):
    __tablename__ = "forms"
    __table_args__ = (UniqueConstraint("project_id", "form_code"), Index("ix_forms_status", "status"))
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    form_code: Mapped[str] = mapped_column(String(48), index=True)
    form_type: Mapped[str] = mapped_column(String(64), default="standard")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_editor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    assigned_reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    first_submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship()
    documents: Mapped[list[Document]] = relationship(back_populates="form", order_by="Document.id")
    assigned_editor: Mapped[User | None] = relationship(foreign_keys=[assigned_editor_id])
    assigned_reviewer: Mapped[User | None] = relationship(foreign_keys=[assigned_reviewer_id])


class Document(Base):
    """An immutable stored PDF. RAW is version 1 of kind=raw; edited submissions are numbered."""
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("form_id", "kind", "version_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    logical_name: Mapped[str] = mapped_column(String(160))   # e.g. FORM001_1.pdf
    stored_name: Mapped[str] = mapped_column(String(200))    # versioned filename actually stored
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="stored")  # stored | invalid | unreadable
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_status: Mapped[str] = mapped_column(String(24), default="pending")
    extraction_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[str] = mapped_column(String(16), default="human", server_default="human")  # human | ai_assisted

    form: Mapped[Form] = relationship(back_populates="documents")
    pages: Mapped[list[DocumentPage]] = relationship(back_populates="document", order_by="DocumentPage.page_no",
                                                     cascade="all, delete-orphan")


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)  # 1-based
    text: Mapped[str] = mapped_column(Text, default="")
    words: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [[x0,y0,x1,y1,"word"], ...] in PDF points
    width: Mapped[float] = mapped_column(Float, default=0)
    height: Mapped[float] = mapped_column(Float, default=0)
    method: Mapped[str] = mapped_column(String(16), default="text")  # text | ocr | none
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document: Mapped[Document] = relationship(back_populates="pages")


class ExceptionItem(Base):
    """Exception queue: files/pairs requiring intervention."""
    __tablename__ = "exception_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    form_id: Mapped[int | None] = mapped_column(ForeignKey("forms.id"), nullable=True)
    form_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issue_type: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[str] = mapped_column(Text)
    quarantine_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open|resolved|dismissed
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class QCRule(Base):
    __tablename__ = "qc_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    versions: Mapped[list[RuleVersion]] = relationship(back_populates="rule", order_by="RuleVersion.version")


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("qc_rules.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(48))
    # Fixed error taxonomy (services/error_taxonomy.py). A rule without a sub-type is not part of the QC rules and never runs.
    error_subtype: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String(16), default="minor")
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    form_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # check_type: forbidden_terms | regex_forbidden | required_labels | max_sentence_words | ai_guideline | builtin
    check_type: Mapped[str] = mapped_column(String(32), default="ai_guideline")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    examples: Mapped[str | None] = mapped_column(Text, nullable=True)
    exceptions: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_date: Mapped[date] = mapped_column(Date, default=date.today)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule: Mapped[QCRule] = relationship(back_populates="versions")


class QCRun(Base):
    __tablename__ = "qc_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id"), index=True)
    raw_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    edited_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stages: Mapped[list] = mapped_column(JSON, default=list)  # [{name,status,detail,started,finished}]
    ai_status: Mapped[str] = mapped_column(String(24), default="skipped")
    ai_provider: Mapped[str | None] = mapped_column(String(48), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(96), nullable=True)
    ai_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_versions: Mapped[list] = mapped_column(JSON, default=list)  # exact rule versions used
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    findings: Mapped[list[Finding]] = relationship(back_populates="run", order_by="Finding.id")


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("qc_runs.id", ondelete="CASCADE"), index=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id"), index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[str] = mapped_column(String(48), index=True)       # taxonomy category key (grammar, missed_instruction, ...)
    error_subtype: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)   # taxonomy sub-type key
    severity: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(16))  # deterministic | ai | manual
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rule_version_id: Mapped[int | None] = mapped_column(ForeignKey("rule_versions.id"), nullable=True)
    rule_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    raw_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_boxes: Mapped[list | None] = mapped_column(JSON, nullable=True)     # [{page,x0,y0,x1,y1}]
    edited_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edited_boxes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    suggested_correction: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_human_attention: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewer_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    original: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # snapshot before reviewer edits
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    run: Mapped[QCRun] = relationship(back_populates="findings")


class ReviewAttempt(Base):
    """One human QC decision on one submission of a form (preserved forever)."""
    __tablename__ = "review_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("qc_runs.id"), nullable=True)
    edited_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_to_editor: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    other_count: Mapped[int] = mapped_column(Integer, default=0)
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id"), index=True)
    role_kind: Mapped[str] = mapped_column(String(16))  # editor | reviewer
    assignee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FormComment(Base):
    """Feedback / comments / escalations linked to a Form ID."""
    __tablename__ = "form_comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id"), index=True)
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("review_attempts.id"), nullable=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), default="comment")  # comment|feedback|escalation|resubmission
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    actor_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    form_id: Mapped[int | None] = mapped_column(ForeignKey("forms.id"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ExportJob(Base):
    __tablename__ = "export_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    report: Mapped[str] = mapped_column(String(64))
    fmt: Mapped[str] = mapped_column(String(8))
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(String(500))
    form_id: Mapped[int | None] = mapped_column(ForeignKey("forms.id"), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AutoEdit(Base):
    """One run of the AI editor: proposes grammar/spelling corrections on a base PDF and writes a new edited PDF.
    Every applied and skipped edit is logged so QC reviewers can see exactly what the AI changed."""
    __tablename__ = "auto_edits"
    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id"), index=True)
    base_document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    result_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|completed|no_changes|failed
    engine: Mapped[str | None] = mapped_column(String(24), nullable=True)           # rules | ai+rules
    ai_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(96), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    edits: Mapped[list] = mapped_column(JSON, default=list)
    skipped: Mapped[list] = mapped_column(JSON, default=list)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ImportJob(Base):
    """One import of a Drive folder tree (ZIP upload, local folder or Google Drive API)."""
    __tablename__ = "import_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(16))  # zip | local | google_drive
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|completed|failed
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    log: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TrackerEntry(Base):
    """A row of an operations tracker spreadsheet (e.g. 'HSB Multi-Projects'), imported read-only."""
    __tablename__ = "tracker_entries"
    __table_args__ = (UniqueConstraint("source_file", "sheet", "row_no"), Index("ix_tracker_shop", "shop_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    source_file: Mapped[str] = mapped_column(String(255))
    sheet: Mapped[str] = mapped_column(String(120))
    row_no: Mapped[int] = mapped_column(Integer)
    input_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    form_name: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    shop_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shop_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    editor: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    ops_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    comments_to_shopper: Mapped[str | None] = mapped_column(Text, nullable=True)
    qc_member: Mapped[str | None] = mapped_column(String(160), nullable=True)
    qc_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # every other column, verbatim
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    """Small key/value store for runtime settings that admins change from the UI (e.g. Drive auto-sync)."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DriveFile(Base):
    """Sync ledger: which Drive file (at which version) was imported as which document. Makes every sync incremental."""
    __tablename__ = "drive_files"
    __table_args__ = (UniqueConstraint("source_key", "drive_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(300), index=True)
    drive_id: Mapped[str] = mapped_column(String(400))
    path: Mapped[str] = mapped_column(String(600))
    kind: Mapped[str] = mapped_column(String(16))  # raw | edited
    version: Mapped[str] = mapped_column(String(120), default="")  # md5 / size:mtime fingerprint of the Drive file
    size: Mapped[int] = mapped_column(Integer, default=0)
    form_id: Mapped[int | None] = mapped_column(ForeignKey("forms.id"), nullable=True, index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MediaLink(Base):
    """A recording (video/audio) attached to a form. The bytes stay in Google Drive (or the synced folder) and are streamed on demand."""
    __tablename__ = "media_links"
    __table_args__ = (UniqueConstraint("form_id", "origin_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(8), default="video")  # video | audio
    mime: Mapped[str] = mapped_column(String(80), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    drive_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    local_path: Mapped[str | None] = mapped_column(String(800), nullable=True)   # file inside IMPORT_LOCAL_ROOT (Drive for desktop mirror)
    url: Mapped[str | None] = mapped_column(String(800), nullable=True)           # the Drive share link that was pasted / found in a sheet
    source: Mapped[str] = mapped_column(String(16), default="manual")             # sync | sheet | manual
    origin_key: Mapped[str] = mapped_column(String(500))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProjectReviewer(Base):
    """The QC lead / reviewer who owns a project: every form in it is analysed and lands in this person's queue."""
    __tablename__ = "project_reviewers"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
