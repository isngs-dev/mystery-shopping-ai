"""QC report PDF: the human-readable record of a RAW vs edited review.

Contains the pair details, what the AI editor changed, every finding with quoted evidence (and highlighted RAW/edited
crops), the reviewer's decisions, the rule versions applied and the review history. It is generated automatically after every
QC run and is marked AUTOMATED until a human QC decision exists for the analysed submission (then FINAL).
"""
from __future__ import annotations

import html
import io
from datetime import datetime

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AutoEdit, Document, Finding, Form, QCRun, ReviewAttempt, User
from ..storage import get_storage
from . import error_taxonomy as tx, file_restore, workflow

E = html.escape
SEV_COLOR = {"critical": "#b3261e", "major": "#b25e00", "minor": "#1d5fb4", "info": "#5b6470"}
REVIEW_LABEL = {"pending": "NOT REVIEWED", "accepted": "ACCEPTED (error confirmed)", "rejected": "REJECTED (not an error)",
                "edited": "EDITED & ACCEPTED", "not_applicable": "NOT APPLICABLE", "escalated": "ESCALATED"}
MAX_EVIDENCE_IMAGES = 14

CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #1b1f24; }
h1 { font-size: 19pt; color: #14322a; margin-bottom: 2pt; }
h2 { font-size: 12.5pt; color: #14322a; margin-top: 13pt; margin-bottom: 4pt; border-bottom: 1pt solid #8a6a43; }
h3 { font-size: 10pt; margin-bottom: 2pt; }
p { margin-top: 2pt; margin-bottom: 2pt; }
table { width: 100%; }
td, th { padding: 3pt; vertical-align: top; }
th { background-color: #e9e2d3; text-align: left; font-size: 8pt; }
.k { color: #5b6470; width: 24%; }
.tag { font-size: 7.5pt; font-weight: bold; }
.box { border: 1pt solid #cfc7b6; padding: 5pt; margin-top: 4pt; margin-bottom: 4pt; }
.q { font-family: monospace; font-size: 8pt; background-color: #f4f1ea; padding: 2pt; }
.muted { color: #5b6470; font-size: 8pt; }
.final { color: #14663a; font-weight: bold; }
.draft { color: #b25e00; font-weight: bold; }
"""


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%d %b %Y %H:%M UTC") if dt else "—"


def evidence_png(pdf_bytes: bytes, page_no: int, boxes: list[dict]) -> bytes | None:
    """Crop the page around the highlighted words and draw the highlight (on a throw-away in-memory copy)."""
    bx = [b for b in boxes if b.get("page") == page_no]
    if not bx:
        return None
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc.load_page(page_no - 1)
        r = pymupdf.Rect(min(b["x0"] for b in bx), min(b["y0"] for b in bx), max(b["x1"] for b in bx), max(b["y1"] for b in bx))
        for b in bx:
            page.draw_rect(pymupdf.Rect(b["x0"] - 1, b["y0"] - 1, b["x1"] + 1, b["y1"] + 1), color=(0.9, 0.45, 0.0), width=1.2,
                           fill=(1, 0.85, 0.2), fill_opacity=0.4)
        clip = pymupdf.Rect(page.rect.x0 + 18, max(page.rect.y0, r.y0 - 28), page.rect.x1 - 18, min(page.rect.y1, r.y1 + 28))
        return page.get_pixmap(clip=clip, dpi=105).tobytes("png")
    finally:
        doc.close()


def _kv(rows: list[tuple[str, str]]) -> str:
    return "".join(f"<p><b>{E(k)}:</b> {v}</p>" for k, v in rows)


def build_report(db: Session, form: Form, generated_by: User) -> tuple[bytes, str]:
    storage = get_storage()
    raw = next((d for d in form.documents if d.kind == "raw"), None)
    edited = workflow.latest_edited(form)
    run: QCRun | None = workflow.current_run(db, form)
    users = {u.id: u for u in db.execute(select(User)).scalars()}
    attempts = db.execute(select(ReviewAttempt).where(ReviewAttempt.form_id == form.id).order_by(ReviewAttempt.id)).scalars().all()
    last = attempts[-1] if attempts else None
    final = bool(last and run and last.run_id == run.id)
    cat_rank = {c.key: i for i, c in enumerate(reversed(tx.CATEGORIES))}          # heaviest category first
    findings: list[Finding] = sorted(run.findings, key=lambda f: (cat_rank.get(f.category, 9), f.error_subtype or "", f.edited_page or f.raw_page or 0, f.id)) if run else []
    auto = db.execute(select(AutoEdit).where(AutoEdit.form_id == form.id, AutoEdit.result_document_id == (edited.id if edited else -1))
                      .order_by(AutoEdit.id.desc())).scalars().first()
    def _bytes(doc):
        try:
            return file_restore.read_document(db, doc) if doc else None
        except file_restore.FileMissing:
            return None                                  # the report is still built; evidence crops are left out

    raw_bytes, ed_bytes = _bytes(raw), _bytes(edited)
    arch = pymupdf.Archive()

    h: list[str] = []
    status_html = (f"<span class='final'>FINAL — decision by {E(users[last.reviewer_id].name if last.reviewer_id in users else 'reviewer')}"
                   f" on {_fmt(last.decided_at)}</span>" if final else "<span class='draft'>AUTOMATED QC REPORT — generated from the fixed QC error table; "
                   "findings stay suggestions until a QC reviewer confirms them</span>")
    h.append(f"<h1>QC Report — {E(form.form_code)}</h1><p class='muted'>{E(form.project.client.name)} / {E(form.project.name)} · "
             f"generated {_fmt(datetime.utcnow())} by {E(generated_by.name)}</p><p>{status_html}</p>")

    # ---- 1. summary
    ps = (run.stats or {}) if run else {}
    rows = [("Form status", E(form.status.replace("_", " ").title())),
            ("RAW document", f"{E(raw.stored_name)} · {raw.page_count} page(s) · SHA-256 {raw.sha256[:16]}… · extraction {E(raw.extraction_status)}" if raw else "—"),
            ("Edited document", (f"{E(edited.stored_name)} (v{edited.version_no}) · {edited.page_count} page(s) · "
                                 f"{'AI-assisted edit' if edited.origin == 'ai_assisted' else 'human edit'} · SHA-256 {edited.sha256[:16]}… · extraction {E(edited.extraction_status)}") if edited else "—"),
            ("Analysis", (f"run #{run.id} · {E(run.status.replace('_', ' '))} · {ps.get('changes', 0)} changed passage(s) · "
                          f"text similarity {ps.get('similarity', 0):.1%} · {len(run.rule_versions or [])} rule(s) applied") if run else "No analysis has run yet."),
            ("AI analysis", E((run.ai_status.replace('_', ' ') + (f" · {run.ai_provider}/{run.ai_model}" if run.ai_model else "") + (f" — {run.ai_message}" if run.ai_message else "")) if run else "—")),
            ("Editor / reviewer", f"{E(form.assigned_editor.name) if form.assigned_editor else '—'} / {E(form.assigned_reviewer.name) if form.assigned_reviewer else '—'}")]
    h.append("<h2>Summary</h2>" + _kv(rows))
    if run and run.status == "completed_with_warnings":
        h.append("<div class='box'><b>Warnings</b>" + "".join(f"<p>• {E(str(w))}</p>" for w in (ps.get("warnings") or [])) + "</div>")

    # ---- 2. decision
    h.append("<h2>QC review</h2>")
    if last:
        h.append(_kv([("Decision", f"<b>{E(last.decision.replace('_', ' ').upper())}</b> (review attempt #{last.attempt_no})"),
                      ("Reviewer", E(users[last.reviewer_id].name) if last.reviewer_id in users else "—"), ("Decided", _fmt(last.decided_at)),
                      ("Findings", f"{last.accepted_count} confirmed · {last.rejected_count} rejected · {last.other_count} other"),
                      ("Summary", E(last.summary or "—")), ("Feedback to editor", E(last.feedback_to_editor or "—"))]))
        if not final:
            h.append("<p class='muted'>This decision belongs to an earlier submission; the current submission has not been decided yet.</p>")
    else:
        h.append("<p>Generated automatically after the edited PDF arrived. No finding has been approved by the system; the QC reviewer "
                 "accepts or rejects each finding, and only accepted findings count towards the reviewer-confirmed score below.</p>")

    # ---- 3. QC scorecard (the fixed error table)
    h.append("<h2>QC scorecard</h2>")
    confirmed = [f for f in findings if f.review_status in ("accepted", "edited")]
    sc = tx.score([f.error_subtype for f in confirmed])
    pending = sum(1 for f in findings if f.review_status == "pending")
    h.append(f"<p><b>QC score: {sc['score']} / {sc['total']}</b>{' (provisional: ' + str(pending) + ' finding(s) not reviewed yet)' if pending else ''}"
             + ("".join(f" · −{d['points']} {E(d['label'])}" for d in sc["deductions"]) if sc["deductions"] else " · no confirmed errors") + "</p>")
    auto_sc = tx.score([f.error_subtype for f in findings if f.review_status not in ("rejected", "not_applicable")])
    h.append(f"<p><b>Automated check: {auto_sc['score']} / {auto_sc['total']}</b> if every finding the reviewer has not rejected is confirmed"
             + ("".join(f" · −{d['points']} {E(d['label'])}" for d in auto_sc["deductions"]) if auto_sc["deductions"] else "") + "</p>")
    rows_t = ["<table><tr><th>Category (marks)</th><th>Error</th><th>Confirmed</th><th>Not reviewed</th><th>Rejected</th><th>Checked by</th></tr>"]
    for cat in tx.CATEGORIES:
        for sub in (x for x in tx.SUBTYPES if x.category == cat.key):
            mine = [f for f in findings if f.error_subtype == sub.key]
            n_conf = sum(1 for f in mine if f.review_status in ("accepted", "edited"))
            n_pend = sum(1 for f in mine if f.review_status == "pending")
            n_rej = sum(1 for f in mine if f.review_status == "rejected")
            strong = "<b>" if n_conf else ""
            rows_t.append(f"<tr><td>{E(cat.label)} (−{cat.points})</td><td>{strong}{E(sub.label)}{'</b>' if n_conf else ''}</td><td>{n_conf or '–'}</td>"
                          f"<td>{n_pend or '–'}</td><td>{n_rej or '–'}</td><td>{'reviewer (video / audio / picture)' if sub.manual_only else 'system + reviewer'}</td></tr>")
    rows_t.append("</table>")
    h.append("".join(rows_t))
    h.append("<p class='muted'>QC checks only the fixed error table. A category with at least one confirmed error takes its marks off 100 once. "
             "Errors marked “reviewer” depend on the video, audio, call log or picture, which the system cannot see.</p>")
    if findings and not any(x.review_status != "pending" for x in findings):
        h.append("<p class='muted'>No finding has been reviewed yet.</p>")

    # ---- 4. AI edit log
    if auto:
        h.append("<h2>AI editor log</h2>")
        h.append(f"<p>{E(auto.engine or '')} · {len(auto.edits or [])} correction(s) applied · {len(auto.skipped or [])} left for a human. "
                 f"{E(auto.message or '')}</p>")
        if auto.edits:
            h.append("<table><tr><th>Pg</th><th>Original</th><th>Corrected to</th><th>Reason</th><th>By</th></tr>" + "".join(
                f"<tr><td>{e.get('page')}</td><td class='q'>{E(e['original'])}</td><td class='q'>{E(e['replacement'] or '(removed)')}</td>"
                f"<td>{E(e['reason'])}</td><td>{E(e['source'])} {int(e['confidence'] * 100)}%</td></tr>" for e in auto.edits[:60]) + "</table>")
        if auto.skipped:
            h.append("<p class='tag'>Left unchanged (needs a human)</p>" + "".join(
                f"<p class='muted'>• <span class='q'>{E(s['original'])}</span> — {E(s['reason'])}</p>" for s in auto.skipped[:25]))

    # ---- 5. findings detail
    h.append("<h2>Findings in detail</h2>")
    if not findings:
        h.append("<p>The automated checks found nothing to report. This does not prove the report is perfect.</p>")
    images = 0
    for n, f in enumerate(findings, 1):
        color = SEV_COLOR.get(f.severity, "#333")
        block = (f"<div class='box'><h3><span style='color:{color}'>{E(tx.category_label(f.category).split(' /')[0].upper())} −{tx.CATEGORY_BY_KEY[f.category].points if f.category in tx.CATEGORY_BY_KEY else 0}</span> · #{n} {E(f.title)}</h3>"
                 f"<p class='muted'>{E(tx.category_label(f.category))} › {E(tx.label(f.error_subtype))} · {'AI' if f.source == 'ai' else 'reviewer-added' if f.source == 'manual' else 'rule check'}"
                 f"{f' · confidence {int(f.confidence * 100)}%' if f.confidence is not None else ''} · rule: {E(f.rule_label or 'none cited')}</p>"
                 f"<p>{E(f.description)}</p>")
        if f.raw_excerpt:
            block += f"<p class='tag'>RAW evidence (page {f.raw_page})</p><p class='q'>“{E(f.raw_excerpt)}”</p>"
        if f.edited_excerpt:
            block += f"<p class='tag'>Edited evidence (page {f.edited_page})</p><p class='q'>“{E(f.edited_excerpt)}”</p>"
        if images < MAX_EVIDENCE_IMAGES and (f.raw_boxes or f.edited_boxes):
            imgs = []
            for label, data, page, boxes in (("RAW", raw_bytes, f.raw_page, f.raw_boxes), ("EDITED", ed_bytes, f.edited_page, f.edited_boxes)):
                if data and page and boxes:
                    png = evidence_png(data, page, boxes)
                    if png:
                        name = f"ev{n}_{label}.png"
                        arch.add(png, name)
                        imgs.append(f"<td width='50%'><p class='tag'>{label}</p><img src='{name}' width='235'></td>")
            if imgs:
                images += 1
                block += "<table><tr>" + "".join(imgs) + "</tr></table>"
        if f.suggested_correction:
            block += f"<p><b>Recommended correction:</b> {E(f.suggested_correction)}</p>"
        who = users[f.reviewer_id].name if f.reviewer_id in users else None
        block += (f"<p><b>Reviewer decision:</b> {REVIEW_LABEL.get(f.review_status, f.review_status)}"
                  f"{' — ' + E(who) if who else ''}{' on ' + _fmt(f.reviewed_at) if f.reviewed_at else ''}"
                  f"{'. Comment: ' + E(f.reviewer_comment) if f.reviewer_comment else ''}</p></div>")
        h.append(block)

    # ---- 6. history + rules
    if attempts:
        h.append("<h2>Review history</h2><table><tr><th>#</th><th>When</th><th>Reviewer</th><th>Decision</th><th>Confirmed / rejected</th></tr>" + "".join(
            f"<tr><td>{a.attempt_no}</td><td>{_fmt(a.decided_at)}</td><td>{E(users[a.reviewer_id].name) if a.reviewer_id in users else '—'}</td>"
            f"<td>{E(a.decision.replace('_', ' '))}</td><td>{a.accepted_count} / {a.rejected_count}</td></tr>" for a in attempts) + "</table>")
    if run and run.rule_versions:
        h.append("<h2>Rules applied (exact versions)</h2><p class='muted'>" + " · ".join(
            f"{E(r['rule_key'])} v{r['version']}" for r in run.rule_versions) + "</p>")
    h.append("<p class='muted' style='margin-top:14pt'>Generated by Mystery Shopping AI. Automated findings are suggestions; every finding in this report "
             "was or must be validated by a named human reviewer. The RAW document is immutable and was not modified.</p>")

    # ---- render
    data = render_pdf("".join(h), lambda i, n: f"QC Report {form.form_code} · {'FINAL' if final else 'AUTOMATED'} · page {i} of {n}", arch)
    return data, f"QC_Report_{form.form_code}_{datetime.utcnow().strftime('%Y%m%d-%H%M')}.pdf"


def render_pdf(body_html: str, footer, arch: pymupdf.Archive | None = None) -> bytes:
    """A4 PDF from an HTML fragment; `footer(page_no, page_count)` is stamped on every page."""
    story = pymupdf.Story(html="<html><body>" + body_html + "</body></html>", user_css=CSS, archive=arch or pymupdf.Archive())
    out = io.BytesIO()
    writer = pymupdf.DocumentWriter(out)
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (36, 36, -36, -46)
    more = 1
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    doc = pymupdf.open(stream=out.getvalue(), filetype="pdf")
    for i, page in enumerate(doc, 1):
        page.insert_text((36, page.rect.height - 24), footer(i, doc.page_count), fontsize=7.5, color=(0.35, 0.38, 0.42))
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data
