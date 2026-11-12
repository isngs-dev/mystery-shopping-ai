"""Import a Drive folder tree into the platform.

Expected layout (matches the production repository convention):
    <root>/<Client>/<Project or Editor>/<ID>_1.pdf   RAW
                                       /<ID>_2.pdf   edited
    <root>/*.xlsx                                    operations tracker (imported read-only)
    <root>/*checklist* | *guidelines* | *instructions*   QC rule documents (listed; rules come from the rules pack)

Everything goes through the normal ingestion path (validation, immutable storage, duplicate/naming checks, exception
queue, audit trail), then the normal QC pipeline. Imports are idempotent: files already imported are skipped.
"""
from __future__ import annotations

import io
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import SessionLocal, utcnow
from ..models import AutoEdit, Client, Document, DriveFile, ExceptionItem, Form, ImportJob, Notification, Project, ProjectReviewer, QCRun, TrackerEntry, User, UserProjectAccess
from ..storage import get_storage, safe_segment
from . import auto_editor, media, pipeline, qc_reports, repository, rules_pack, workflow
from .drive_sources import LocalSource, Node, Source, SourceError, ZipSource, GoogleDriveSource
from ..config import get_settings

log = logging.getLogger("msai.import")
PDF_NAME = [re.compile(r"^(?P<id>[A-Za-z0-9\-]+)_(?P<n>\d+)\.pdf$", re.I),
            re.compile(r"^(?P<id>\d{4,})\s*-\s*.*?-\s*(?P<n>\d+)\.pdf$", re.I),   # e.g. "3353196 - Admin Shop Review-2.pdf"
            re.compile(r"^(?P<id>\d{4,})\s*-\s*(?P<n>\d+)\.pdf$", re.I),          # "131227-2.pdf", "42386 - 1.pdf", "3354395 -2.pdf"
            re.compile(r"^(?P<id>\d{4,})\s*\(\s*(?P<n>\d+)\s*\)\.pdf$", re.I),    # "23695714 (1).pdf"
            re.compile(r"^(?P<id>\d{4,})\s*-\s*[^\d\W][^.]*\.pdf$", re.I)]        # "3353722 - Admin Shop Review.pdf": no number = the RAW; its edit is "ID - 2.pdf"
UNNUMBERED_RAW = PDF_NAME[-1]
RULE_DOC = re.compile(r"checklist|guideline|instruction|brief|sop", re.I)
SUFFIX_KIND = {1: "raw", 2: "edited"}
MAX_LOG = 600


def parse_pdf_name(name: str) -> tuple[str, int] | None:
    for rx in PDF_NAME:
        m = rx.match(name.strip())
        if m:
            return m.group("id").upper(), (1 if rx is UNNUMBERED_RAW else int(m.group("n")))
    return None


class Reporter:
    def __init__(self, db: Session, job: ImportJob):
        self.db, self.job, self.n = db, job, 0
        self.stats: dict[str, int] = defaultdict(int)
        self.lines: list[str] = []
        self.form_ids: list[int] = []   # forms that were QC'd / AI-edited by this import (for the report download)
        self.source_key = ""
        self.touched: set[tuple[int, str]] = set()   # (project id, form id) that received something new
        self.ledger: dict[str, DriveFile] = {}

    def say(self, msg: str) -> None:
        if len(self.lines) < MAX_LOG:
            self.lines.append(msg)

    def flush(self, force: bool = False) -> None:
        self.n += 1
        if force or self.n % 5 == 0:
            self.job.stats, self.job.log = {**dict(self.stats), "form_ids": self.form_ids[:2000]}, list(self.lines)
            self.db.commit()


# ------------------------------------------------------------------------------------------------ org objects
def get_client(db: Session, name: str, rep: Reporter) -> Client:
    c = db.execute(select(Client).where(Client.name == name)).scalar_one_or_none()
    if c:
        return c
    c = Client(name=name, folder=safe_segment(name).replace(" ", "_"))
    db.add(c)
    db.flush()
    rep.stats["clients_created"] += 1
    rep.say(f"Created client “{name}”")
    return c


def get_project(db: Session, client: Client, name: str, user: User, rep: Reporter) -> Project:
    folder = safe_segment(name).replace(" ", "_")
    p = db.execute(select(Project).where(Project.client_id == client.id, Project.folder == folder)).scalar_one_or_none()
    if p:
        return p
    p = Project(client_id=client.id, name=name, folder=folder)
    db.add(p)
    db.flush()
    grant = {user.id} if user.role != "super_admin" else set()   # whoever creates a project must be able to see it
    roles = [r.strip() for r in get_settings().import_grant_roles.split(",") if r.strip()]
    if roles:   # the working team (admin / manager / QC) sees new projects without a manual grant; editors see only what is assigned to them
        grant |= set(db.execute(select(User.id).where(User.role.in_(roles), User.is_active.is_(True))).scalars())
    grant |= set(db.execute(select(User.id).where(User.is_coordinator.is_(True), User.is_active.is_(True))).scalars())    # coordinators see every project
    for uid in grant:
        db.add(UserProjectAccess(user_id=uid, project_id=p.id, granted_by=user.id))
    db.flush()
    if get_settings().auto_assign_by_folder_name:
        from .project_qc import adopt_folder_owner      # the folder name says whose project it is ("Orlando_Devansh")
        owner = adopt_folder_owner(db, p, user.id)
        if owner:
            rep.say(f"Project “{name}” belongs to {owner.name}")
    else:
        rep.say(f"Project “{name}” is waiting for a coordinator to assign it to a QC lead")
    rep.stats["projects_created"] += 1
    rep.say(f"Created project “{client.name} / {name}”")
    return p


# ------------------------------------------------------------------------------------------------ helpers
def _exception_once(db: Session, rep: Reporter, *, issue_type: str, project: Project | None, user: User, detail: str, form_code: str | None = None,
                    filename: str | None = None) -> bool:
    """Raise an exception-queue item unless the same problem was already raised (open, resolved or dismissed): a sync that
    runs every few minutes must not re-report the same problem each time."""
    q = select(ExceptionItem.id).where(ExceptionItem.issue_type == issue_type, ExceptionItem.project_id == (project.id if project else None),
                                       ExceptionItem.form_code == form_code, ExceptionItem.filename == filename, ExceptionItem.detail == detail)
    if db.execute(q).first():
        return False
    repository.open_exception(db, issue_type=issue_type, project=project, form_code=form_code, filename=filename, user=user, detail=detail)
    return True


def _auto_resolve_missing_raw(db: Session, project: Project, fid: str, user: User) -> None:
    for e in db.execute(select(ExceptionItem).where(ExceptionItem.project_id == project.id, ExceptionItem.form_code == fid,
                                                    ExceptionItem.issue_type == "missing_raw", ExceptionItem.status == "open")).scalars():
        e.status, e.resolved_by, e.resolved_at, e.resolution_note = "resolved", user.id, utcnow(), "Resolved automatically: the RAW file arrived."


def _record(db: Session, rep: Reporter, node: Node, path: str, kind: str, form: Form | None, doc: Document | None, note: str | None = None) -> None:
    row = rep.ledger.get(node.id)
    if row is None:
        row = DriveFile(source_key=rep.source_key, drive_id=node.id, path=path[:600], kind=kind)
        db.add(row)
        rep.ledger[node.id] = row
    row.version, row.size, row.path, row.kind, row.note = node.version, node.size, path[:600], kind, note
    row.form_id = form.id if form else row.form_id
    row.document_id = doc.id if doc else row.document_id


def _restore_missing_files(db: Session, source: Source, rep: Reporter, storage) -> list:
    """A document row whose PDF is gone from the storage disk is fetched again from the Drive file it came from.
    Only byte-identical content is restored (the stored SHA-256 must match); returns [(form, latest edited document)] to QC again."""
    again: dict[int, tuple] = {}
    for row in list(rep.ledger.values()):
        if not row.document_id or row.kind not in ("raw", "edited"):
            continue
        doc = db.get(Document, row.document_id)
        if doc is None or doc.status != "stored" or storage.exists(doc.storage_key):
            continue
        try:
            data = source.read(Node(row.drive_id, row.path.rsplit("/", 1)[-1], False, row.size, "", row.version))
        except Exception as exc:  # noqa: BLE001
            rep.stats["restore_failed"] += 1
            rep.say(f"{doc.stored_name}: the stored file is missing and could not be fetched again ({exc})")
            continue
        if repository.sha256_of(data) != doc.sha256:
            rep.stats["restore_failed"] += 1
            rep.say(f"{doc.stored_name}: the stored file is missing and the Drive file has changed since, so the original cannot be restored")
            continue
        storage.put_immutable(doc.storage_key, data)
        rep.stats["files_restored"] += 1
        form = db.get(Form, row.form_id) if row.form_id else None
        edited = workflow.latest_edited(form) if form else None
        if form and edited:
            again[form.id] = (form, edited)
        qc_reports.mark_dirty(form.project_id if form else None)
    if rep.stats.get("files_restored"):
        rep.say(f"{rep.stats['files_restored']} missing PDF(s) were restored from Drive")
    db.commit()
    return list(again.values())


RETRY_PER_CYCLE = 25


def _catch_up_qc(db: Session, user: User, opts: dict, rep: Reporter) -> None:
    """Every form that has a RAW and an edited PDF but no QC analysis yet gets one (e.g. forms synced before QC could run for them)."""
    if not opts.get("run_qc", True):
        return
    from .project_qc import SKIP_STATES

    has_run = select(QCRun.id).where(QCRun.form_id == Form.id).exists()
    forms = db.execute(select(Form).where(~has_run, Form.status.not_in(list(SKIP_STATES))).order_by(Form.id).limit(RETRY_PER_CYCLE * 4)).scalars().all()
    n = 0
    for form in forms:
        if n >= RETRY_PER_CYCLE:
            break
        edited = workflow.latest_edited(form)
        if not edited or not any(d.kind == "raw" for d in form.documents):
            continue
        try:
            run = pipeline.create_run(db, form, edited, user)
            pipeline.execute_run(db, run.id)
            rep.stats["qc_runs"] += 1
            rep.form_ids.append(form.id)
            n += 1
        except Exception as exc:  # noqa: BLE001
            rep.stats["qc_errors"] += 1
            rep.say(f"{form.form_code}: QC could not run ({exc})")
        rep.flush()
    if n:
        rep.say(f"QC ran for {n} form(s) that had no analysis yet")
RETRYABLE_ERRORS = ("%No such file or directory%", "%missing from storage%", "Interrupted by a server restart%")


def _retry_lost_file_failures(db: Session, user: User, opts: dict, rep: Reporter) -> None:
    """A QC run that failed because a PDF was missing from the disk (or the server restarted) is not a final answer: once the file is
    readable again (it is fetched from Drive on demand) the analysis is simply run again — only for projects handed to a QC lead."""
    if not opts.get("run_qc", True):
        return
    failed = db.execute(select(QCRun).where(QCRun.status == "failed", or_(*[QCRun.error.like(p) for p in RETRYABLE_ERRORS]))
                        .order_by(QCRun.id.desc())).scalars().all()
    done: set[int] = set()
    for run in failed:
        if run.form_id in done or len(done) >= RETRY_PER_CYCLE:
            continue
        done.add(run.form_id)
        form = db.get(Form, run.form_id)
        edited = workflow.latest_edited(form) if form else None
        current = workflow.current_run(db, form) if form else None
        if not form or not edited or current is None or current.id != run.id:
            continue
        try:
            new = pipeline.create_run(db, form, edited, user)
            pipeline.execute_run(db, new.id)
            rep.stats["qc_retried"] += 1
            rep.form_ids.append(form.id)
        except Exception as exc:  # noqa: BLE001
            rep.stats["qc_errors"] += 1
            rep.say(f"{form.form_code}: QC could not be run again ({exc})")
        rep.flush()


def _unchanged(rep: Reporter, node: Node | None) -> bool:
    if node is None:
        return True
    row = rep.ledger.get(node.id)
    return bool(row and node.version and row.version == node.version)


# ------------------------------------------------------------------------------------------------ main import
def import_tree(db: Session, job: ImportJob, source: Source, user: User) -> None:
    opts = job.options or {}
    rep = Reporter(db, job)
    rep.source_key = f"{source.kind}:{source.key}"
    rep.ledger = {r.drive_id: r for r in db.execute(select(DriveFile).where(DriveFile.source_key == rep.source_key)).scalars()}
    storage = get_storage()
    restored = _restore_missing_files(db, source, rep, storage)       # files lost from the disk (e.g. a host without a persistent disk) come back from Drive
    root = source.root()
    top = source.children(root)
    rep.say(f"Source: {source.label} ({source.kind}); {sum(1 for n in top if n.is_dir)} folder(s) and {sum(1 for n in top if not n.is_dir)} file(s) at the top level")

    # ---- root-level documents: tracker spreadsheets (only when changed) + QC rule documents
    for n in (x for x in top if not x.is_dir):
        low = n.name.lower()
        if low.endswith((".xlsx", ".xlsm")) and opts.get("import_tracker", True):
            if _unchanged(rep, n):
                continue
            try:
                added, sheets = import_tracker(db, n.name, source.read(n))
                rep.stats["tracker_rows"] += added
                rep.say(f"Tracker “{n.name}”: {added} new row(s) from {sheets} sheet(s)")
                _record(db, rep, n, n.name, "tracker", None, None)
            except Exception as exc:  # noqa: BLE001
                rep.stats["tracker_errors"] += 1
                rep.say(f"Tracker “{n.name}” could not be read: {exc}")
        elif RULE_DOC.search(n.name) and not _unchanged(rep, n):
            rep.stats["rule_documents_found"] += 1
            rep.say(f"QC rule document found: {n.name} (its rules are managed under QC Rules; see the rules pack)")
            _record(db, rep, n, n.name, "document", None, None)
    rep.flush(True)

    # ---- clients / projects / PDFs
    for cnode in (x for x in top if x.is_dir):
        client = get_client(db, cnode.name, rep)
        kids = source.children(cnode)
        subdirs = [k for k in kids if k.is_dir]
        groups = [(k.name, source.children(k)) for k in subdirs]
        loose = [k for k in kids if not k.is_dir]
        if loose:
            groups.append((cnode.name if not subdirs else "General", loose))
        for pname, items in groups:
            project = get_project(db, client, pname, user, rep)
            if _import_project(db, source, user, client, project, items, opts, rep, storage) is False:
                break
    _finish_pass2(db, user, restored, opts, rep)           # QC again for the forms whose lost PDFs were just restored
    _retry_lost_file_failures(db, user, opts, rep)         # ... and for forms whose earlier QC failed only because a PDF was missing
    _catch_up_qc(db, user, opts, rep)                      # ... and for any complete pair that never got a QC analysis
    _ai_edit_stale(db, rep, opts)
    try:
        n_sheet = media.reconcile_sheet_links(db)
        if n_sheet:
            rep.stats["recordings_linked"] += n_sheet
            rep.say(f"{n_sheet} recording link(s) from the tracker were attached to their forms")
    except Exception as exc:  # noqa: BLE001
        rep.say(f"Recording links from the tracker could not be attached: {exc}")
    # ---- rules from the checklists/guidelines (as drafts awaiting approval)
    if opts.get("apply_rules", True):
        res = rules_pack.apply_pack(db, user)
        rep.stats["rules_submitted"] += res["submitted"]
        if res["submitted"]:
            rep.say(f"QC rules pack: {res['submitted']} rule(s) submitted for approval")
    db.commit()
    rep.flush(True)
    for pid in {p for p, _ in rep.touched}:            # new / changed forms: their project's Excel QC report is rewritten
        qc_reports.mark_dirty(pid)


def _import_project(db: Session, source: Source, user: User, client: Client, project: Project, items: list[Node], opts: dict, rep: Reporter, storage) -> bool:
    by_id: dict[str, dict[int, Node]] = defaultdict(dict)
    unmapped: dict[int, list[str]] = defaultdict(list)
    for n in items:
        if n.is_dir or not n.name.lower().endswith(".pdf"):
            if not n.is_dir:
                rep.stats["non_pdf_ignored"] += 1
            continue
        parsed = parse_pdf_name(n.name)
        if not parsed:
            if _exception_once(db, rep, issue_type="naming_error", project=project, user=user, filename=n.name,
                               detail=f"'{n.name}' does not follow ID_1.pdf (RAW) / ID_2.pdf (edited)."):
                rep.stats["naming_errors"] += 1
            continue
        fid, num = parsed
        for e in db.execute(select(ExceptionItem).where(ExceptionItem.project_id == project.id, ExceptionItem.issue_type == "naming_error",
                                                        ExceptionItem.filename == n.name, ExceptionItem.status == "open")).scalars():
            e.status, e.resolved_by, e.resolved_at, e.resolution_note = "resolved", user.id, utcnow(), "Resolved automatically: this file name is now recognised."
        if num in SUFFIX_KIND:
            by_id[fid][num] = n
        else:
            unmapped[num].append(n.name)
    for num, names in unmapped.items():
        if _exception_once(db, rep, issue_type="unmapped_suffix", project=project, user=user, filename=f"*_{num}.pdf",
                           detail=f"{len(names)} file(s) end in _{num} (e.g. {', '.join(sorted(names)[:3])}). Only _1 (RAW) and _2 (edited) are defined, so these "
                                  f"were NOT imported. Tell the admin what _{num} means to import them."):
            rep.stats["unmapped_suffix_files"] += len(names)
    db.commit()

    _attach_recordings(db, source, project, items, rep)
    limit = opts.get("limit")
    pending: list[tuple[Form, Document]] = []   # (form, newly imported edited doc) -> QC in pass 2
    for fid, files in sorted(by_id.items()):
        raw_n, ed_n = files.get(1), files.get(2)
        if _unchanged(rep, raw_n) and _unchanged(rep, ed_n) and raw_n is not None:
            rep.stats["already_imported"] += 1       # nothing changed since the last sync: no download at all
            continue
        if limit and rep.stats["forms_new"] >= limit:
            rep.say(f"Stopped at the limit of {limit} new form(s); the rest is picked up by the next sync")
            _finish_pass2(db, user, pending, opts, rep)
            return False
        base = f"{client.name}/{project.name}/"
        if raw_n is None:
            if _exception_once(db, rep, issue_type="missing_raw", project=project, form_code=fid, filename=ed_n.name if ed_n else None, user=user,
                               detail=f"Edited file for {fid} exists but there is no RAW ({fid}_1.pdf)."):
                rep.stats["missing_raw"] += 1
            continue
        form = db.execute(select(Form).where(Form.project_id == project.id, Form.form_code == fid)).scalar_one_or_none()
        try:
            raw_doc = next((d for d in form.documents if d.kind == "raw"), None) if form else None
            # ---------------- RAW (immutable: never replaced)
            if not _unchanged(rep, raw_n):
                data = source.read(raw_n)
                if raw_doc is None:
                    res = repository.ingest_pdf(db, storage, user, project=project, form_code=fid, kind="raw", original_filename=raw_n.name, data=data)
                    form, raw_doc = res.form, res.document
                    rep.stats["raw_imported"] += 1
                    rep.touched.add((project.id, fid))
                    rep.stats["forms_new"] = len(rep.touched)
                    pipeline.ensure_extracted(db, raw_doc)      # extract now: boilerplate detection needs the whole project's RAW text
                    _auto_resolve_missing_raw(db, project, fid, user)
                    _record(db, rep, raw_n, base + raw_n.name, "raw", form, raw_doc)
                elif repository.sha256_of(data) == raw_doc.sha256:
                    _record(db, rep, raw_n, base + raw_n.name, "raw", form, raw_doc, "matches stored RAW")
                else:
                    _record(db, rep, raw_n, base + raw_n.name, "raw", form, raw_doc, "changed in Drive; original kept")
                    if _exception_once(db, rep, issue_type="raw_changed", project=project, form_code=fid, filename=raw_n.name, user=user,
                                       detail=f"The RAW file {raw_n.name} was changed in Drive after it was imported. The immutable original "
                                              f"({raw_doc.stored_name}) was kept and the new content was NOT imported."):
                        rep.stats["raw_changed"] += 1
            # ---------------- edited (a new or replaced file becomes a new, timestamped version)
            if ed_n is not None and form is not None and raw_doc is not None and not _unchanged(rep, ed_n):
                data = source.read(ed_n)
                known = {d.sha256 for d in form.documents if d.kind == "edited"}
                if repository.sha256_of(data) in known:
                    _record(db, rep, ed_n, base + ed_n.name, "edited", form, None, "already stored")
                else:
                    had_edit = any(d.kind == "edited" for d in form.documents)
                    try:
                        res = repository.ingest_pdf(db, storage, user, project=project, form_code=fid, kind="edited", original_filename=ed_n.name,
                                                    data=data, note="Updated from Google Drive" if had_edit else None)
                        res.document.origin = "human"
                        db.commit()
                        pending.append((res.form, res.document))
                        rep.stats["edited_imported" if not had_edit else "edited_resubmitted"] += 1
                        rep.touched.add((project.id, fid))
                        rep.stats["forms_new"] = len(rep.touched)
                        _record(db, rep, ed_n, base + ed_n.name, "edited", res.form, res.document)
                    except repository.IngestError as exc:
                        _record(db, rep, ed_n, base + ed_n.name, "edited", form, None, f"rejected: {exc.message[:120]}")
                        raise
            db.commit()
        except repository.IngestError as exc:
            _record(db, rep, raw_n, base + raw_n.name, "raw", form, None, f"rejected: {exc.message[:120]}") if form is None else None
            db.commit()
            rep.stats["rejected"] += 1
            rep.say(f"{project.name}/{fid}: {exc.message}")
        except SourceError as exc:
            rep.stats["read_errors"] += 1
            rep.say(f"{project.name}/{fid}: {exc}")
        rep.flush()
    _finish_pass2(db, user, pending, opts, rep)
    return True


def _attach_recordings(db: Session, source: Source, project: Project, items: list[Node], rep: Reporter) -> None:
    """Video/audio files named by form ID (also inside a 'Recordings'/'Videos' sub-folder) are linked to their form, not downloaded."""
    nodes: list[Node] = []
    for n in items:
        if n.is_dir and media.MEDIA_DIR.search(n.name):
            nodes += [k for k in source.children(n) if not k.is_dir]
        elif not n.is_dir:
            nodes.append(n)
    for n in nodes:
        fid = media.parse_media_name(n.name)
        if not fid:
            continue
        if source.kind == "zip":
            rep.stats["recordings_skipped_zip"] += 1      # recordings are streamed from Drive; they are not copied out of a ZIP
            continue
        form = db.execute(select(Form).where(Form.project_id == project.id, Form.form_code == fid)).scalar_one_or_none()
        if form is None:
            rep.stats["recordings_waiting"] += 1          # the form's PDF has not arrived yet; the next sync attaches it
            continue
        ext = media.ext_of(n.name)
        key = f"{source.kind}:{n.id}"
        _, new = media.attach(db, form, title=n.name, kind=media.kind_of(n.name), source="sync", origin_key=key, mime=media.MIME.get(ext, ""), size=n.size,
                              drive_id=n.id if source.kind == "google_drive" else None, local_path=n.id if source.kind == "local" else None)
        if new:
            rep.stats["recordings_linked"] += 1
    db.commit()


def _finish_pass2(db: Session, user: User, pending: list, opts: dict, rep: Reporter) -> None:
    """Pass 2: QC every newly imported / replaced edited PDF."""
    for form, edited in pending:
        try:
            db.refresh(form)
            if opts.get("run_qc", True):                        # QC never waits for an assignment: assigning only decides who reviews
                run = pipeline.create_run(db, form, edited, user)
                pipeline.execute_run(db, run.id)
                rep.stats["qc_runs"] += 1
                rep.form_ids.append(form.id)
        except Exception as exc:  # noqa: BLE001
            rep.stats["qc_errors"] += 1
            rep.say(f"{form.form_code}: QC could not run ({exc})")
        rep.flush()
    pending.clear()


def _ai_edit_stale(db: Session, rep: Reporter, opts: dict) -> None:
    """RAWs that still have no edited file after `ai_edit_after_hours` get an AI edit (spelling/grammar only) and go to QC."""
    if not opts.get("ai_edit_missing"):
        return
    cutoff = utcnow() - timedelta(hours=float(opts.get("ai_edit_after_hours", 0) or 0))
    for row in list(rep.ledger.values()):
        if row.kind != "raw" or not row.form_id or not row.document_id:
            continue
        form, raw_doc = db.get(Form, row.form_id), db.get(Document, row.document_id)
        if not form or not raw_doc or form.status != "pending" or any(d.kind == "edited" for d in form.documents) or raw_doc.uploaded_at > cutoff:
            continue
        if db.execute(select(AutoEdit.id).where(AutoEdit.form_id == form.id)).first():
            continue    # already tried; failed attempts are not retried automatically
        try:
            ae = auto_editor.create_autoedit(db, form, raw_doc, None)
            auto_editor.run_autoedit_job(ae.id)       # also runs QC on the AI-edited PDF
            rep.stats["ai_edited"] += 1
            rep.form_ids.append(form.id)
        except Exception as exc:  # noqa: BLE001
            rep.stats["qc_errors"] += 1
            rep.say(f"{form.form_code}: AI edit could not run ({exc})")
        rep.flush()


# ------------------------------------------------------------------------------------------------ tracker spreadsheet
CANON = {
    "input date": "input_date", "form": "form_name", "class / location": "location", "location": "location", "shop id": "shop_id", "job id": "shop_id",
    "shop date": "shop_date", "editor": "editor", "editor name": "editor", "rw editor": "editor", "ops status": "ops_status",
    "comments to shopper": "comments_to_shopper", "qc member": "qc_member", "qc status": "qc_status", "feedback": "feedback",
    "error category": "error_category", "error categories": "error_category",
}
SECRET = re.compile(r"pass(word|wd)?\b|pwd|credential|login (id|details)", re.I)


def _cell(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%m/%d/%Y")
    s = str(v).strip()
    return s or None


def import_tracker(db: Session, file_name: str, data: bytes) -> tuple[int, int]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    added = 0
    sheets = 0
    for ws in wb.worksheets:
        mapping: dict[int, str] = {}
        names: dict[int, str] = {}
        seen = False
        for rno, row in enumerate(ws.iter_rows(values_only=True), 1):
            cells = [_cell(c) for c in row]
            lowered = [(c or "").lower() for c in cells]
            if ("shop id" in lowered or "job id" in lowered) and ("form" in lowered):  # header row (a sheet may hold several tables)
                names = {i: c for i, c in enumerate(cells) if c}
                mapping = {i: CANON[l] for i, l in enumerate(lowered) if l in CANON}
                continue
            if not mapping or not any(cells):
                continue
            rec = {mapping[i]: cells[i] for i in mapping if i < len(cells) and cells[i]}
            sid = rec.get("shop_id", "")
            if not sid or not re.fullmatch(r"\d{3,12}", sid):
                continue  # explanatory/reference rows (error-category lists, login notes, ...) are not tracker rows
            if any(c and SECRET.search(c) for c in cells):
                continue  # never import anything that looks like a credential
            seen = True
            extra = {names[i]: cells[i] for i in names if i < len(cells) and cells[i] and i not in mapping}
            urls = media.media_urls_from_cells(names, cells)
            if urls:
                extra["media_urls"] = urls
            fields = {k: (v[:64] if k in ("shop_id", "ops_status", "qc_status", "input_date", "shop_date") else v) for k, v in rec.items()}
            existing = db.execute(select(TrackerEntry).where(TrackerEntry.source_file == file_name, TrackerEntry.sheet == ws.title,
                                                              TrackerEntry.row_no == rno)).scalar_one_or_none()
            if existing:
                for k, v in fields.items():
                    setattr(existing, k, v)
                existing.data = extra
            else:
                db.add(TrackerEntry(source_file=file_name, sheet=ws.title, row_no=rno, data=extra, **fields))
                added += 1
            if added and added % 400 == 0:
                db.flush()
        sheets += 1 if seen else 0
    db.commit()
    return added, sheets


# ------------------------------------------------------------------------------------------------ job runner
def _notify_admins(db: Session, job: ImportJob) -> None:
    st = job.stats or {}
    new = st.get("raw_imported", 0) + st.get("edited_imported", 0) + st.get("edited_resubmitted", 0) + st.get("ai_edited", 0)
    if job.status == "failed":
        msg = f"Drive sync #{job.id} failed: {(job.error or '')[:200]}"
    elif new and (job.options or {}).get("auto"):
        parts = [f"{st.get(k)} {label}" for k, label in (("raw_imported", "new RAW"), ("edited_imported", "edited"), ("edited_resubmitted", "revised edited"),
                                                          ("ai_edited", "AI-edited")) if st.get(k)]
        msg = "Drive sync: " + ", ".join(parts) + f" ({st.get('qc_runs', 0)} QC analyses)."
    else:
        return
    for u in db.execute(select(User).where(User.is_active.is_(True), User.role.in_(["super_admin", "admin"]))).scalars():
        db.add(Notification(user_id=u.id, kind="drive_sync", message=msg[:500]))



def open_source(job: ImportJob) -> Source:
    o = job.options or {}
    if job.kind == "zip":
        return ZipSource(o["zip_path"], o.get("filename"))
    if job.kind == "local":
        return LocalSource(o["path"])
    if job.kind == "google_drive":
        return GoogleDriveSource(o["folder_id"])
    raise SourceError(f"Unknown import source {job.kind}")


def run_import_job(job_id: int) -> None:
    db = SessionLocal()
    source: Source | None = None
    try:
        job = db.get(ImportJob, job_id)
        if not job:
            return
        user = db.get(User, job.created_by)
        job.status = "running"
        db.commit()
        try:
            source = open_source(job)
            import_tree(db, job, source, user)
            job.status = "completed"
        except SourceError as exc:
            db.rollback()
            job = db.get(ImportJob, job_id)
            job.status, job.error = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("import %s crashed", job_id)
            db.rollback()
            job = db.get(ImportJob, job_id)
            job.status, job.error = "failed", f"Unexpected error: {exc}"
        job.finished_at = utcnow()
        _notify_admins(db, job)
        audit(db, user, f"import.{job.status}", entity_type="import_job", entity_id=job.id, detail={"source": job.source, "stats": job.stats, "error": job.error})
        db.commit()
    finally:
        if source:
            source.close()
        zp = (db.get(ImportJob, job_id).options or {}).get("zip_path") if job_id else None
        if zp:
            try:
                import os
                os.remove(zp)  # the uploaded archive is deleted once imported (documents are stored immutably)
            except OSError:
                pass
        db.close()
