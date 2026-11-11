"""Project QC report as an Excel workbook in the layout of the team's tracker (“HSB Multi-Projects”, sheet “October”).

One row per form of the project, same 23 columns in the same order (Input date … Error Category, Platform, Incomplete Status,
Duplicates). The QC columns are filled from the automated QC on the fixed error table:

    QC Member      the QC lead of the project            QC Date    day the QC analysis ran
    QC Status      uses the tracker's own status words   Feedback   score + errors found, by category
    Error Category uses the tracker's category list      Editing Type  AI Edited / Manually Edited

Two more sheets follow: “Findings” (every finding with its evidence) and “Summary” (errors by the QC error table).
Columns of the tracker that the platform cannot know (Editor, OPS Status, Comments to shopper, …) are taken from an imported
tracker row with the same Shop ID when there is one, and left empty otherwise — nothing is invented.
"""
from __future__ import annotations

import io
import re
from collections import Counter, defaultdict
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DocumentPage, Finding, Form, Project, TrackerEntry, User
from . import error_taxonomy as tx
from . import workflow

# the tracker's columns (sheet “October”) and their widths
COLUMNS: list[tuple[str, float]] = [
    ("Input date", 10.7), ("Form", 27.7), ("Class / Location", 39.0), ("Shop ID", 11.1), ("Shop Date", 10.6), ("Input Status", 13.4),
    ("Editor", 12.9), ("Editor Name", 22.6), ("Transaction Date", 15.3), ("OPS Status", 18.6), ("Comments to shopper", 57.7),
    ("Editing Type", 14.0), ("QC Member", 18.6), ("QC Date", 12.6), ("QC Status", 15.0), ("Feedback", 78.0), ("Client Name", 16.0),
    ("Client Escalation", 16.3), ("Company's Name", 16.1), ("Error Category", 24.0), ("Platform", 14.0), ("Incomplete Status", 18.4),
    ("Duplicates", 11.0),
]
# the tracker's Error Category list (Master sheet) — spelling kept exactly as in the tracker
SHEET_CATEGORY = {"grammar": "Grammar", "missed_instruction": "Missed Instruction", "contradiction": "Contradication",
                  "wrong_location": "Wrong Location/ Put on Different category"}
STATUS_LIST = "In Process,Finalized,Edited & Saved,Query,Collaboration Shop,Incomplete,Hold A,Reviewed,Hold B,Accepted,Request Revision,Rejected"
CATEGORY_LIST = "Grammar,Missed Instruction,Contradication,Wrong Location/ Put on Different category,All,Client Error,QCD,Internal error"
HEADER_FILL = PatternFill("solid", fgColor="0000FF")
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
PLATFORMS = [(r"sassie", "Sassie"), (r"athpower", "AthPower"), (r"shopmetrics", "ShopMetrics"), (r"intelli-?shop", "Intelli-Shop")]
COUNTED = ("pending", "accepted", "edited", "escalated")        # findings the reviewer has not thrown out


FORMULA_START = ("=", "+", "-", "@", chr(9), chr(13))


def _safe(v):
    """Text that starts like a formula would be executed by Excel: neutralise it (evidence is copied from untrusted PDFs)."""
    if isinstance(v, str) and v[:1] in FORMULA_START:
        return "'" + v
    return v


def _norm_id(v) -> str:
    s = str(v or "").strip()
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s


def _platform(db: Session, form: Form) -> str:
    raw = next((d for d in form.documents if d.kind == "raw"), None)
    if not raw:
        return ""
    text = " ".join(t or "" for t in db.execute(select(DocumentPage.text).where(DocumentPage.document_id == raw.id, DocumentPage.page_no <= 2)).scalars())
    for pat, name in PLATFORMS:
        if re.search(pat, text, re.I):
            return name
    return ""


def _status(form: Form, edited, run) -> str:
    """QC Status in the tracker's own words: In Process · Accepted · Request Revision · Query."""
    if not edited:
        return ""
    if run is None or run.status in ("queued", "running"):
        return "In Process"
    if run.status == "failed":
        return "Query"
    counted = [f for f in run.findings if f.review_status in COUNTED]
    return "Request Revision" if counted else "Accepted"


def _feedback(form: Form, edited, run) -> tuple[str, list[Finding]]:
    if not edited:
        return "Waiting for the edited PDF (_2) — QC has not run.", []
    if run is None or run.status in ("queued", "running"):
        return "QC analysis is in progress.", []
    if run.status == "failed":
        return f"QC could not run: {(run.error or 'unknown error')[:300]}", []
    counted = [f for f in run.findings if f.review_status in COUNTED]
    confirmed = [f for f in run.findings if f.review_status in ("accepted", "edited")]
    auto = tx.score([f.error_subtype for f in counted])
    conf = tx.score([f.error_subtype for f in confirmed])
    lines = [f"QC score {auto['score']}/100 (automated check)" + (f"; reviewer-confirmed {conf['score']}/100" if confirmed else "") + "."]
    if not counted:
        lines.append("No error of the QC error table was found.")
    for cat in tx.CATEGORIES:
        subs = Counter(f.error_subtype for f in counted if f.category == cat.key)
        if subs:
            parts = "; ".join(f"{tx.label(k)} ×{n}" for k, n in subs.most_common())
            lines.append(f"{cat.label} (−{cat.points}): {parts}")
    return "\n".join(lines), counted


def _error_category(counted: list[Finding]) -> str:
    keys = [c.key for c in tx.CATEGORIES if any(f.category == c.key for f in counted)]
    if len(keys) == len(tx.CATEGORIES):
        return "All"
    return ", ".join(SHEET_CATEGORY[k] for k in keys)


def _as_date(v) -> date | str | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return v or None


def build_project_excel(db: Session, project: Project, generated_by: User | None) -> tuple[bytes, str]:
    forms = db.execute(select(Form).where(Form.project_id == project.id).order_by(Form.form_code)).scalars().all()
    reviewer_name = None
    from .project_qc import owner_of
    owner = owner_of(db, project.id)
    if owner:
        reviewer_name = owner.name
    codes = {_norm_id(f.form_code) for f in forms}
    tracker: dict[str, TrackerEntry] = {}
    for t in db.execute(select(TrackerEntry).order_by(TrackerEntry.id)).scalars():
        if _norm_id(t.shop_id) in codes:
            tracker[_norm_id(t.shop_id)] = t

    wb = Workbook()
    ws = wb.active
    ws.title = "QC Report"
    for i, (name, width) in enumerate(COLUMNS, 1):
        c = ws.cell(1, i, name)
        c.font, c.fill, c.alignment = HEADER_FONT, HEADER_FILL, Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 17.25
    ws.freeze_panes = "A2"

    findings_ws = wb.create_sheet("Findings")
    fcols = ["Shop ID", "QC Member", "Category", "Marks", "Error (QC error table)", "Origin", "Source", "Review", "Page", "Title",
             "RAW evidence", "Edited evidence", "Explanation", "Suggested correction", "Rule"]
    for i, name in enumerate(fcols, 1):
        c = findings_ws.cell(1, i, name)
        c.font, c.fill, c.alignment = HEADER_FONT, HEADER_FILL, Alignment(horizontal="center", vertical="center")
    for i, w in enumerate([11, 16, 22, 7, 38, 14, 12, 12, 6, 34, 50, 50, 50, 40, 30], 1):
        findings_ws.column_dimensions[get_column_letter(i)].width = w
    findings_ws.freeze_panes = "A2"

    per_cat: dict[str, Counter] = {c.key: Counter() for c in tx.CATEGORIES}
    scores: list[int] = []
    status_count: Counter = Counter()
    row = 2
    frow = 2
    for form in forms:
        edited = workflow.latest_edited(form)
        run = workflow.current_run(db, form)
        t = tracker.get(_norm_id(form.form_code))
        td = (t.data if t else {}) or {}
        feedback, counted = _feedback(form, edited, run)
        status = _status(form, edited, run)
        status_count[status or "No edited PDF"] += 1
        reviewer = form.assigned_reviewer.name if form.assigned_reviewer else reviewer_name
        qc_date = _as_date(run.finished_at) if run and run.finished_at else None
        values = [
            _as_date(t.input_date) if t and t.input_date else form.created_at.date(),
            (t.form_name if t and t.form_name else form.project.client.name),
            (t.location if t and t.location else ""),
            _norm_id(form.form_code),
            (t.shop_date if t else None),
            ("Completed" if edited else ""),
            (t.editor if t and t.editor else "iSN Team"),
            td.get("Editor Name") or td.get("editor_name") or reviewer or "",
            td.get("Transaction Date") or td.get("transaction_date") or "",
            (t.ops_status if t else ""),
            (t.comments_to_shopper if t else ""),
            ("" if not edited else ("AI Edited" if edited.origin == "ai_assisted" else "Manually Edited")),
            reviewer or "",
            qc_date,
            status,
            feedback,
            form.project.client.name,
            "",
            "",
            _error_category(counted),
            _platform(db, form),
            "",
            "",
        ]
        for i, v in enumerate(values, 1):
            c = ws.cell(row, i, _safe(v))
            left = i in (11, 16)          # Comments to shopper / Feedback read left-aligned; everything else is centred, as in the tracker
            c.alignment = Alignment(horizontal="left" if left else "center", vertical="center", wrap_text=left)
            if i in (1, 14) and isinstance(v, date):
                c.number_format = "m/d/yyyy"
        row += 1
        if counted or (run and run.status in ("completed", "completed_with_warnings")):
            scores.append(tx.score([f.error_subtype for f in counted])["score"])
        for f in counted:
            per_cat[f.category][f.error_subtype] += 1
        if run:
            for f in sorted(run.findings, key=lambda x: (list(c.key for c in tx.CATEGORIES)[::-1].index(x.category) if x.category in SHEET_CATEGORY else 9,
                                                         x.error_subtype or "", x.edited_page or x.raw_page or 0, x.id)):
                cat = tx.CATEGORY_BY_KEY.get(f.category)
                vals = [_norm_id(form.form_code), reviewer or "", cat.label if cat else f.category, cat.points if cat else "", tx.label(f.error_subtype),
                        {"missed_correction": "Left by editor", "new_error": "Added by editor"}.get(f.type, "Reviewer"),
                        {"ai": "AI", "manual": "Reviewer", "deterministic": "Rule check"}.get(f.source, f.source), f.review_status.replace("_", " "),
                        f.edited_page or f.raw_page, f.title, f.raw_excerpt, f.edited_excerpt, f.description, f.suggested_correction, f.rule_label]
                for i, v in enumerate(vals, 1):
                    c = findings_ws.cell(frow, i, _safe(v))
                    c.alignment = Alignment(vertical="top", wrap_text=i in (10, 11, 12, 13, 14))
                frow += 1
    last = max(row - 1, 2)
    for col, items in (("G", "iSN Team,Freelancer"), ("J", STATUS_LIST), ("L", "AI Edited,Manually Edited"), ("O", STATUS_LIST), ("T", CATEGORY_LIST)):
        dv = DataValidation(type="list", formula1=f'"{items}"', allow_blank=True, showErrorMessage=False)      # dropdowns as in the tracker
        dv.add(f"{col}2:{col}{max(last, 1000)}")
        ws.add_data_validation(dv)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(row - 1, 1)}"
    findings_ws.auto_filter.ref = f"A1:{get_column_letter(len(fcols))}{max(frow - 1, 1)}"

    # ---- Summary
    sm = wb.create_sheet("Summary")
    sm.column_dimensions["A"].width = 44
    sm.column_dimensions["B"].width = 16
    sm.column_dimensions["C"].width = 12
    sm.column_dimensions["D"].width = 14
    bold = Font(bold=True)
    sm["A1"], sm["A1"].font = f"QC report — {project.client.name} / {project.name}", Font(bold=True, size=14)
    info = [("QC lead", reviewer_name or "not assigned"), ("Forms", len(forms)), ("Generated", datetime.utcnow().strftime("%d %b %Y %H:%M UTC")),
            ("Generated by", generated_by.name if generated_by else "automatic update"), ("Average automated QC score", round(sum(scores) / len(scores), 1) if scores else "—")]
    r = 3
    for k, v in info:
        sm.cell(r, 1, k).font = bold
        sm.cell(r, 2, v)
        r += 1
    r += 1
    for col, name in enumerate(["QC Status", "Forms"], 1):
        c = sm.cell(r, col, name)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
    r += 1
    for k, n in status_count.most_common():
        sm.cell(r, 1, k)
        sm.cell(r, 2, n)
        r += 1
    r += 1
    for col, name in enumerate(["Category / error (QC error table)", "Marks", "Findings", "Checked by"], 1):
        c = sm.cell(r, col, name)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
    r += 1
    for cat in tx.CATEGORIES:
        sm.cell(r, 1, cat.label).font = bold
        sm.cell(r, 2, cat.points)
        sm.cell(r, 3, sum(per_cat[cat.key].values()))
        r += 1
        for s in (x for x in tx.SUBTYPES if x.category == cat.key):
            sm.cell(r, 1, "    " + s.label)
            sm.cell(r, 3, per_cat[cat.key][s.key] or None)
            sm.cell(r, 4, "Reviewer (video / audio / picture)" if s.manual_only else "System + reviewer")
            r += 1
    r += 1
    notes = ["Findings counted: everything the reviewer has not rejected. QC Status uses the tracker's words — Accepted: no error found; Request Revision: errors found; "
             "In Process: QC running; Query: QC could not read the PDF.",
             "Error Category spelling follows the tracker's list (“Contradication”). Several categories are comma-separated; “All” = all four.",
             "Editor, OPS Status, Comments to shopper, Shop/Transaction dates come from the imported tracker row with the same Shop ID, and stay empty when there is none.",
             "Checks that need the video, audio, call log or a picture are done by the reviewer and appear here once added."]
    for n in notes:
        sm.cell(r, 1, n).alignment = Alignment(wrap_text=True, vertical="top")
        sm.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        sm.row_dimensions[r].height = 42
        r += 1

    out = io.BytesIO()
    wb.save(out)
    safe = "".join(ch if ch.isalnum() else "_" for ch in project.name)
    return out.getvalue(), f"QC_Report_{safe}_{datetime.utcnow().strftime('%Y%m%d-%H%M')}.xlsx"
