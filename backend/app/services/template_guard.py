"""Protect template text from QC checks and from the AI editor.

Reports exported from survey platforms (e.g. Sassie / AthPower "Admin Shop Review" pages) contain far more than the
shopper's narrative: page headers, URLs, shopper contact details, reviewer controls, event logs and question wording.
Checking or editing that text would drown reviewers in false findings (or alter things nobody should touch).

Two layers decide which tokens are *protected*:
  1. Boilerplate learned from the data: a line that appears (after normalisation) in the RAW PDFs of at least
     ``MIN_DOCS`` other forms of the same project is template text, not the shopper's words.
  2. An explicit, versioned "template_profile" rule (check_type='template_profile') with
       ignore_line_patterns : regexes; a matching line is protected
       editable_start/end   : regexes; text before the first / after the last matching line is protected
Comparison still sees everything; protected text just never produces findings or edits.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document, DocumentPage, Form
from .compare import Tok
from .rules_service import rules_for_form

MIN_DOCS = 3
MAX_DOCS = 40


def norm_line(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def line_map(toks: list[Tok]) -> dict[tuple[int, int], list[int]]:
    lines: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, t in enumerate(toks):
        lines[(t.page, t.line)].append(i)
    return lines


def profile_for(db: Session, form: Form) -> dict:
    """Merge every applicable published template_profile rule (client/project/general)."""
    merged: dict = {"ignore_line_patterns": [], "editable_start": None, "editable_end": None, "rules": []}
    for rv in rules_for_form(db, form):
        if rv.check_type != "template_profile":
            continue
        c = rv.config or {}
        merged["ignore_line_patterns"] += [p for p in c.get("ignore_line_patterns", []) if isinstance(p, str)]
        merged["editable_start"] = c.get("editable_start") or merged["editable_start"]
        merged["editable_end"] = c.get("editable_end") or merged["editable_end"]
        merged["rules"].append(f"{rv.rule.rule_key} v{rv.version}")
    if not merged["ignore_line_patterns"]:
        # No approved profile yet: still never check the survey platform's own page furniture (URLs, headers, reviewer panel, event log).
        from .rules_pack import PLATFORM_IGNORE

        merged["ignore_line_patterns"] = list(PLATFORM_IGNORE)
        merged["rules"].append("built-in page-furniture profile")
    return merged


def template_lines(db: Session, form: Form) -> set[str]:
    """Lines present in >= MIN_DOCS RAW documents of *other* forms in the same project."""
    docs = db.execute(
        select(Document.id).join(Form, Form.id == Document.form_id)
        .where(Form.project_id == form.project_id, Form.id != form.id, Document.kind == "raw", Document.extraction_status.in_(["ok", "partial"]))
        .order_by(Document.id.desc()).limit(MAX_DOCS)).scalars().all()
    if len(docs) < MIN_DOCS:
        return set()
    counts: Counter[str] = Counter()
    per_doc: dict[int, set[str]] = defaultdict(set)
    for did, text in db.execute(select(DocumentPage.document_id, DocumentPage.text).where(DocumentPage.document_id.in_(docs))):
        for ln in (text or "").split("\n"):
            n = norm_line(ln)
            if len(n) >= 4:
                per_doc[did].add(n)
    for s in per_doc.values():
        counts.update(s)
    return {ln for ln, c in counts.items() if c >= MIN_DOCS}


# “gkamath (Geeta, Kamath) 2026-11-02 05:27” — the platform's own comment / review-history stamp, never the shopper's words.
STAMP_RE = re.compile(r"\S+ \([A-Z][\w'’\-]+, [A-Z][\w'’\-]+\) \d{4}-\d{2}-\d{2} \d{1,2}:\d{2}(?::\d{2})?")
WORD_RE = re.compile(r"[A-Za-z][a-z'’\-]{2,}[.,;:!?]?|[A-Z][a-z]+|[AaI]")


def _garbled(texts: list[str]) -> bool:
    """A line of font-encoding garbage (“SU[]W QRPW SRdOSW”): too few real words to be checked."""
    return len(texts) >= 6 and sum(1 for t in texts if WORD_RE.fullmatch(t)) / len(texts) < 0.35


def _stamp_tokens(toks: list[Tok]) -> set[int]:
    out: set[int] = set()
    text, starts, pos = [], [], 0
    for t in toks:
        starts.append(pos)
        text.append(t.text)
        pos += len(t.text) + 1
    joined = " ".join(text)
    import bisect

    for m in STAMP_RE.finditer(joined):
        i1 = bisect.bisect_right(starts, m.start()) - 1
        i2 = bisect.bisect_left(starts, m.end())
        out.update(range(max(i1, 0), max(i2, i1 + 1)))
    return out


def compute_protected(toks: list[Tok], profile: dict, template: set[str]) -> set[int]:
    lines = line_map(toks)
    order = sorted(lines)
    texts = {k: norm_line(" ".join(toks[i].text for i in idxs)) for k, idxs in lines.items()}
    flagged: set[tuple[int, int]] = set()
    pats = []
    for p in profile.get("ignore_line_patterns", []):
        try:
            pats.append(re.compile(p, re.I))
        except re.error:
            continue
    for k in order:
        t = texts[k]
        if t in template or any(p.search(t) for p in pats) or _garbled([toks[i].text for i in lines[k]]):
            flagged.add(k)
    start, end = profile.get("editable_start"), profile.get("editable_end")
    try:
        s_re = re.compile(start, re.I) if start else None
        e_re = re.compile(end, re.I) if end else None
    except re.error:
        s_re = e_re = None
    if s_re or e_re:
        first = next((i for i, k in enumerate(order) if s_re and s_re.search(texts[k])), None) if s_re else 0
        last = max((i for i, k in enumerate(order) if e_re and e_re.search(texts[k])), default=None) if e_re else len(order)
        if first is None:
            first = 0
        if last is None:
            last = len(order)
        for i, k in enumerate(order):
            if i < first or i >= last:
                flagged.add(k)
    return {i for k in flagged for i in lines[k]} | _stamp_tokens(toks)


def all_protected(span: tuple[int, int] | None, protected: set[int]) -> bool:
    return bool(span) and all(i in protected for i in range(span[0], span[1]))
