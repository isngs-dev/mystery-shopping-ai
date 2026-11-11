"""End-to-end QC processing pipeline.

validate -> extract (OCR if needed) -> compare -> deterministic rules -> AI analysis -> persist.
Stage progress is persisted on the QCRun so the UI can show real status. The pipeline never approves a
form: it only produces findings that a human reviewer must validate.
"""
from __future__ import annotations

import logging
import traceback

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import SessionLocal, utcnow
from ..enums import AIStatus, FormStatus, RunStatus
from ..models import Document, DocumentPage, Finding, Form, QCRun, User
from ..storage import get_storage
from . import ai_analysis, compare, error_taxonomy as tx, qc_reports, template_guard
from .ai import AIError, resolve_provider
from .extraction import extract_pdf
from .rules_engine import Candidate, Ctx, injection_hits, run_deterministic
from .rules_service import rules_for_form

log = logging.getLogger("msai.pipeline")
STAGES = ["validate", "extract", "compare", "rules", "ai", "persist"]


class PipelineStop(Exception):
    def __init__(self, message: str, form_status: str | None = None):
        super().__init__(message)
        self.message, self.form_status = message, form_status


def _stage(db: Session, run: QCRun, name: str, status: str, detail: str | None = None) -> None:
    stages = [dict(s) for s in (run.stages or [])]
    cur = next((s for s in stages if s["name"] == name), None)
    if cur is None:
        cur = {"name": name, "status": status, "detail": detail, "started_at": utcnow().isoformat() + "Z"}
        stages.append(cur)
    cur["status"], cur["detail"] = status, detail
    if status != "running":
        cur["finished_at"] = utcnow().isoformat() + "Z"
    run.stages = stages
    db.commit()


def create_run(db: Session, form: Form, edited: Document | None, user: User | None) -> QCRun:
    raw = next((d for d in form.documents if d.kind == "raw"), None)
    run = QCRun(form_id=form.id, raw_document_id=raw.id if raw else None, edited_document_id=edited.id if edited else None,
                attempt_no=edited.version_no if edited else 0, status=RunStatus.QUEUED.value,
                triggered_by=user.id if user else None, stages=[])
    db.add(run)
    audit(db, user, "qc.run_queued", entity_type="qc_run", entity_id="pending", form_id=form.id, project_id=form.project_id,
          detail={"edited_document_id": edited.id if edited else None})
    db.commit()
    return run


def ensure_extracted(db: Session, doc: Document) -> None:
    if doc.extraction_status in ("ok", "partial") and doc.pages and (doc.extraction_summary or {}).get("order") == "geometric":
        return
    data = get_storage().get(doc.storage_key)
    res = extract_pdf(data)
    for p in list(doc.pages):
        db.delete(p)
    db.flush()
    for p in res.pages:
        db.add(DocumentPage(document_id=doc.id, page_no=p.page_no, text=p.text, words=p.words, width=p.width,
                            height=p.height, method=p.method, ocr_confidence=p.ocr_confidence, note=p.note))
    doc.extraction_status, doc.extraction_summary = res.status, res.summary
    db.commit()
    db.refresh(doc)


def extract_document_job(document_id: int) -> None:
    """Background job: extract a document right after upload so it can be viewed/searched."""
    db = SessionLocal()
    try:
        doc = db.get(Document, document_id)
        if doc:
            ensure_extracted(db, doc)
    finally:
        db.close()


def run_job(run_id: int) -> None:
    db = SessionLocal()
    try:
        execute_run(db, run_id)
    finally:
        db.close()


def _set_form_status(db: Session, form: Form, status: str, reason: str) -> None:
    if form.status == FormStatus.QC_REVIEW.value:
        audit(db, None, "form.status_changed", entity_type="form", entity_id=form.id, form_id=form.id,
              project_id=form.project_id, detail={"from": form.status, "to": status, "reason": reason}, actor_label="system:pipeline")
        form.status, form.status_reason = status, reason


def _to_finding(run: QCRun, form: Form, c: Candidate, cmp: compare.Comparison) -> Finding:
    raw_ex = compare.excerpt(cmp.raw, *c.raw_span) if c.raw_span else None
    ed_ex = compare.excerpt(cmp.edited, *c.edited_span) if c.edited_span else None
    rule_label = f"{c.rule.rule.rule_key} v{c.rule.version} — {c.rule.name}" if c.rule else None
    cat = tx.category_of(c.subtype)          # every finding sits on the fixed error table
    f = Finding(
        run_id=run.id, form_id=form.id, type=c.type, category=cat.key, error_subtype=c.subtype, severity=cat.severity, source=c.source,
        confidence=c.confidence, rule_version_id=c.rule.id if c.rule else None, rule_label=rule_label, title=c.title[:250],
        description=c.description, raw_excerpt=raw_ex, raw_page=compare.span_page(cmp.raw, c.raw_span[0]) if c.raw_span else None,
        raw_boxes=compare.boxes_for(cmp.raw, *c.raw_span) if c.raw_span else None,
        edited_excerpt=ed_ex, edited_page=compare.span_page(cmp.edited, c.edited_span[0]) if c.edited_span else None,
        edited_boxes=compare.boxes_for(cmp.edited, *c.edited_span) if c.edited_span else None,
        suggested_correction=c.suggestion, needs_human_attention=c.needs_human or c.rule is None,
    )
    f.original = {"title": f.title, "description": f.description, "severity": f.severity,
                  "suggested_correction": f.suggested_correction, "confidence": f.confidence}
    return f


def execute_run(db: Session, run_id: int) -> None:
    try:
        _execute_run(db, run_id)
    finally:
        run = db.get(QCRun, run_id)
        form = db.get(Form, run.form_id) if run else None
        if form:
            qc_reports.mark_dirty(form.project_id)       # the project's Excel QC report picks up this form


def _execute_run(db: Session, run_id: int) -> None:
    run = db.get(QCRun, run_id)
    if run is None:
        return
    form = db.get(Form, run.form_id)
    run.status, run.started_at = RunStatus.RUNNING.value, utcnow()
    db.commit()
    warnings: list[str] = []
    try:
        # 1. validate pair --------------------------------------------------------------
        _stage(db, run, "validate", "running")
        raw = db.get(Document, run.raw_document_id) if run.raw_document_id else None
        edited = db.get(Document, run.edited_document_id) if run.edited_document_id else None
        if not raw or raw.status != "stored":
            raise PipelineStop("The RAW PDF (_1) is missing or invalid; a pair is required for comparison.", FormStatus.BLOCKED.value)
        if not edited or edited.status != "stored":
            raise PipelineStop("The edited PDF (_2) is missing or invalid; a pair is required for comparison.", FormStatus.BLOCKED.value)
        _stage(db, run, "validate", "done", f"Pair identified: {raw.logical_name} (RAW) and {edited.logical_name} v{edited.version_no} (edited).")

        # 2. extraction -----------------------------------------------------------------
        _stage(db, run, "extract", "running")
        for d in (raw, edited):
            ensure_extracted(db, d)
        bad = [d for d in (raw, edited) if d.extraction_status in ("failed", "ocr_unavailable")]
        if bad:
            detail = "; ".join(f"{d.logical_name}: {d.extraction_summary and d.extraction_summary.get('ocr', {}).get('reason') or d.extraction_status}" for d in bad)
            _stage(db, run, "extract", "failed", detail)
            raise PipelineStop(f"Text could not be extracted reliably ({detail}). Manual review is required; no automated comparison was performed.",
                               FormStatus.MANUAL_REVIEW.value)
        partial = [d for d in (raw, edited) if d.extraction_status == "partial"]
        if partial:
            warnings.append("Some pages were extracted with low confidence or failed: " + ", ".join(d.logical_name for d in partial))
        ocr_pages = sum((d.extraction_summary or {}).get("ocr_pages", 0) for d in (raw, edited))
        _stage(db, run, "extract", "done", f"Extracted {raw.page_count + edited.page_count} pages" + (f" ({ocr_pages} via OCR)." if ocr_pages else "."))

        # 3. comparison -----------------------------------------------------------------
        _stage(db, run, "compare", "running")
        raw_pages = list(raw.pages)
        edited_pages = list(edited.pages)
        cmp = compare.compare_tokens(compare.build_tokens(raw_pages), compare.build_tokens(edited_pages))
        _stage(db, run, "compare", "done", f"{len(cmp.changes)} changed passages; text similarity {cmp.similarity:.1%}.")

        # 4. deterministic rules ------------------------------------------------------------
        _stage(db, run, "rules", "running")
        rules = rules_for_form(db, form)
        run.rule_versions = [{"rule_version_id": r.id, "rule_key": r.rule.rule_key, "version": r.version, "name": r.name,
                              "check_type": r.check_type} for r in rules]
        ctx = Ctx(cmp, raw_pages, edited_pages)
        profile = template_guard.profile_for(db, form)
        tmpl = template_guard.template_lines(db, form)
        ctx.protected_raw = template_guard.compute_protected(cmp.raw, profile, tmpl)
        ctx.protected_edited = template_guard.compute_protected(cmp.edited, profile, tmpl)
        ai_rules = run_deterministic(ctx, rules)
        cands = [c for c in ctx.candidates if tx.is_valid(c.subtype)]
        # Things that are not QC errors are reported as notices to the reviewer, never as findings or score deductions.
        if raw.page_count != edited.page_count:
            warnings.append(f"Page count differs: RAW has {raw.page_count} page(s), the edited PDF has {edited.page_count}.")
        for p in edited_pages + raw_pages:
            if p.note and (p.note.startswith("Low OCR") or "failed" in p.note.lower()):
                warnings.append(f"Unreliable text extraction on page {p.page_no}: {p.note}")
        inj = injection_hits(cmp)
        if inj:
            warnings.append(f"{inj} passage(s) read like instructions to an AI system; they were ignored (documents are data, not commands).")
        _stage(db, run, "rules", "done", f"{len(rules) - len(ai_rules)} deterministic rules applied; {len(cands)} candidate findings.")
        if ctx.skipped_rules:
            warnings.append(f"{len(ctx.skipped_rules)} rule(s) could not be evaluated: " + "; ".join(f"{s['rule']} ({s['reason']})" for s in ctx.skipped_rules))

        # 5. AI ---------------------------------------------------------------------------------
        _stage(db, run, "ai", "running")
        provider, ai_status, ai_msg = resolve_provider()
        ai_meta: dict = {}
        if provider is None:
            run.ai_status, run.ai_message = ai_status.value, ai_msg
            _stage(db, run, "ai", "skipped", ai_msg)
            warnings.append("AI analysis was NOT performed: " + ai_msg)
            if ai_rules:
                warnings.append(f"{len(ai_rules)} guideline-based rule(s) could not be evaluated without AI.")
        else:
            run.ai_provider, run.ai_model = provider.name, provider.model
            try:
                ai_cands, ai_meta = ai_analysis.analyze(
                    provider, cmp, raw_pages, edited_pages, ai_rules, cands,
                    {"client": form.project.client.name, "project": form.project.name, "form_type": form.form_type, "form_id": form.form_code})
                cands.extend(ai_cands)
                run.ai_status, run.ai_model = AIStatus.COMPLETED.value, ai_meta.get("model") or provider.model
                run.ai_message = f"{ai_meta['accepted']} AI finding(s) accepted; {ai_meta['discarded_unverified']} discarded (evidence could not be verified)."
                _stage(db, run, "ai", "done", run.ai_message)
            except AIError as exc:
                run.ai_status, run.ai_message = AIStatus.FAILED.value, str(exc)
                _stage(db, run, "ai", "failed", str(exc))
                warnings.append("AI analysis failed: " + str(exc))

        # 6. persist ----------------------------------------------------------------------------
        _stage(db, run, "persist", "running")
        sev_rank = {"critical": 0, "major": 1, "minor": 2, "info": 3}
        cands.sort(key=lambda c: (sev_rank.get(c.severity, 9), c.edited_span[0] if c.edited_span else (c.raw_span[0] if c.raw_span else 0)))
        for c in cands:
            db.add(_to_finding(run, form, c, cmp))
        run.stats = {
            "findings": len(cands), "by_source": {k: sum(1 for c in cands if c.source == k) for k in ("deterministic", "ai")},
            "by_severity": {k: sum(1 for c in cands if c.severity == k) for k in sev_rank},
            "changes": len(cmp.changes), "similarity": cmp.similarity,
            "protected_tokens": {"raw": len(ctx.protected_raw), "edited": len(ctx.protected_edited), "template_lines": len(tmpl), "profile": profile.get("rules", [])}, "rules_applied": len(rules),
            "ai": ai_meta, "skipped_rules": ctx.skipped_rules, "warnings": warnings,
            "ai_guideline_rules_unevaluated": len(ai_rules) if run.ai_status != AIStatus.COMPLETED.value else 0,
        }
        run.status = RunStatus.COMPLETED.value if not warnings else RunStatus.COMPLETED_WITH_WARNINGS.value
        run.finished_at = utcnow()
        _stage(db, run, "persist", "done", f"{len(cands)} finding(s) saved.")
        audit(db, None, "qc.run_completed", entity_type="qc_run", entity_id=run.id, form_id=form.id, project_id=form.project_id,
              detail={"status": run.status, "findings": len(cands), "ai_status": run.ai_status, "rule_versions": len(rules)},
              actor_label="system:pipeline")
        db.commit()
    except PipelineStop as stop:
        db.rollback()
        run = db.get(QCRun, run_id)
        form = db.get(Form, run.form_id)
        run.status, run.error, run.finished_at = RunStatus.FAILED.value, stop.message, utcnow()
        if stop.form_status:
            _set_form_status(db, form, stop.form_status, stop.message)
        audit(db, None, "qc.run_failed", entity_type="qc_run", entity_id=run.id, form_id=form.id, project_id=form.project_id,
              detail={"reason": stop.message}, actor_label="system:pipeline")
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.error("QC run %s crashed: %s\n%s", run_id, exc, traceback.format_exc())
        run = db.get(QCRun, run_id)
        run.status, run.error, run.finished_at = RunStatus.FAILED.value, f"Unexpected processing error: {exc}", utcnow()
        audit(db, None, "qc.run_failed", entity_type="qc_run", entity_id=run.id, form_id=run.form_id,
              detail={"error": str(exc)}, actor_label="system:pipeline")
        db.commit()


def recover_interrupted_runs() -> int:
    """On startup: runs left queued/running by a crashed process are marked failed (never silently 'running')."""
    db = SessionLocal()
    try:
        rows = db.execute(select(QCRun).where(QCRun.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value]))).scalars().all()
        for r in rows:
            r.status, r.error, r.finished_at = RunStatus.FAILED.value, "Interrupted by a server restart. Re-run the analysis.", utcnow()
        from ..models import AutoEdit

        edits = db.execute(select(AutoEdit).where(AutoEdit.status.in_(["queued", "running"]))).scalars().all()
        for a in edits:
            a.status, a.message, a.finished_at = "failed", "Interrupted by a server restart. Run the AI editor again.", utcnow()
        db.commit()
        return len(rows) + len(edits)
    finally:
        db.close()
