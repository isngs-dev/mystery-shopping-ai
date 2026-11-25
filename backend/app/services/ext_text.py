"""Grammar edits for a form that is NOT a PDF in Google Drive: the web page of a mystery-shopping provider (Intelli-Shop shop review) where every
question has a text box with the shopper's answer. The extension sends the questions and answers; the same editing engine as for PDFs (built-in
corrector + the AI editor with the company's guidelines + the same safety policy) returns edits with character positions inside each answer.

Nothing is stored and nothing is written anywhere: the page shows the edits, the person decides.
"""
from __future__ import annotations

import hashlib
import re
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Client
from . import auto_editor
from .ai import AIError, resolve_provider
from .compare import Tok, _norm
from .ext_assist import guidelines_text, rules_for_company

WORD = re.compile(r"\S+")


def _key(s: str) -> str:
    """Letters and digits only, doubled letters collapsed ("Intelishop" matches "intelli-shop.com")."""
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z0-9]", "", s.lower()))


def match_client(db: Session, hint: str | None, client_id: int | None = None) -> dict | None:
    """The client of the page: the one the person chose, otherwise the client whose name appears in the page's header text ("Love's: T-Stop")."""
    clients = db.execute(select(Client).where(Client.is_active.is_(True))).scalars().all()
    if client_id:
        c = next((c for c in clients if c.id == client_id), None)
        return {"client": c.name, "client_id": c.id, "matched_by": "chosen"} if c else None
    h = _key(hint or "")
    best = None
    for c in clients:
        k = _key(c.name)
        if len(k) >= 3 and k in h and (best is None or len(k) > len(_key(best.name))):
            best = c
    return {"client": best.name, "client_id": best.id, "matched_by": "page header"} if best else None


def _tokens(answers: list[dict]) -> list[Tok]:
    """One 'page' per answer (page = position + 1); a Tok's box carries its character range, its line is the text line inside the answer."""
    toks: list[Tok] = []
    for n, a in enumerate(answers, start=1):
        text = a["text"]
        for m in WORD.finditer(text):
            toks.append(Tok(m.group(0), _norm(m.group(0)), n, (m.start(), 1, m.end(), 2), line=text.count("\n", 0, m.start())))
    for i, t in enumerate(toks):
        t.idx = i
    return toks


def char_rules(toks: list[Tok], answers: list[dict], covered: set[int]) -> list[auto_editor.EditOp]:
    """What the word-based corrector cannot see because it never looks at the white space between words: extra spaces, a comma glued to the next word
    when the first one carries a digit ("5pm,the"), a space before a comma that is glued to the next word (" ,paid"), and a last sentence without its full stop.
    `covered` = tokens an earlier rule already edits (never touched twice)."""
    ops: list[auto_editor.EditOp] = []
    by_page: dict[int, list[int]] = {}
    for i, t in enumerate(toks):
        by_page.setdefault(t.page, []).append(i)
    plain = lambda s: not re.search(r"://|@|www\.|\.[a-z]{2,4}/", s, re.I)                              # noqa: E731 - never touch links and e-mail addresses
    for page, idx in by_page.items():
        text = answers[page - 1]["text"]
        for a, b in zip(idx, idx[1:]):
            ta, tb = toks[a], toks[b]
            if a in covered or b in covered or not (plain(ta.text) and plain(tb.text)):
                continue
            gap = text[int(ta.box[2]):int(tb.box[0])]
            if gap != " " and "\n" not in gap and "\r" not in gap and gap.strip() == "":
                # more than one space, or a space that is not a plain one (a non-breaking space or a tab copied from Word / a PDF looks like a space but is not)
                why = "Extra space between words" if len(gap) > 1 else "A special space character instead of a normal space"
                ops.append(auto_editor.EditOp(a, b + 1, f"{ta.text} {tb.text}", f"{ta.text}{gap}{tb.text}", why, "spacing", 0.97, "rules", page))
                covered.update((a, b))
            elif gap == " " and re.fullmatch(r"[,;:][A-Za-z].*", tb.text) and not re.search(r"[,;:.!?]$", ta.text):
                ops.append(auto_editor.EditOp(a, b + 1, f"{ta.text}{tb.text[0]} {tb.text[1:]}", f"{ta.text} {tb.text}", "No space before a comma; a space after it", "spacing", 0.9, "rules", page))
                covered.update((a, b))
        for i in idx:
            m = re.fullmatch(r"([A-Za-z0-9’'-]*[A-Za-z][A-Za-z0-9’'-]*),([A-Za-z][\w’'-]*[.!?]?)", toks[i].text)
            if m and i not in covered and plain(toks[i].text):
                ops.append(auto_editor.EditOp(i, i + 1, f"{m.group(1)}, {m.group(2)}", toks[i].text, "A space is needed after the comma", "spacing", 0.9, "rules", page))
                covered.add(i)
        body = text.rstrip()
        last = idx[-1]
        last_line = body.split("\n")[-1]
        if last not in covered and re.search(r"[A-Za-z]$", body) and len(last_line.split()) >= 6 and plain(toks[last].text) \
                and (len(body.split()) >= 12 or re.search(r"[.!?]", body[:-1])):                              # prose, not a name or an address
            ops.append(auto_editor.EditOp(last, last + 1, toks[last].text + ".", toks[last].text, "The last sentence has no full stop", "punctuation", 0.6, "rules", page))
    return ops


def _narrow(op: auto_editor.EditOp, toks: list[Tok]) -> auto_editor.EditOp:
    """An edit marks only the words that change (like Grammarly): the words the replacement shares with the start and the end of the original are cut off
    (at least one word is kept on each side), so "for a oil change" -> "for an oil change" becomes just "a" -> "an"."""
    o = [t.text for t in toks[op.i1:op.i2]]
    r = op.replacement.split()
    p = 0
    while p < len(o) - 1 and p < len(r) - 1 and o[p] == r[p]:
        p += 1
    s = 0
    while s < len(o) - p - 1 and s < len(r) - p - 1 and o[-1 - s] == r[-1 - s]:
        s += 1
    if (p == 0 and s == 0) or o[p:len(o) - s] == r[p:len(r) - s]:          # nothing to narrow, or the change is only the white space between words
        return op
    return auto_editor.EditOp(op.i1 + p, op.i2 - s, " ".join(r[p:len(r) - s]), " ".join(o[p:len(o) - s]), op.reason, op.category, op.confidence, op.source, op.page)


def propose_text(db: Session, *, answers: list[dict], client_hint: str | None = None, client_id: int | None = None,
                 feedback: str | None = None, rejected: list[dict] | None = None, item_feedback: list[dict] | None = None,
                 guidelines: list[str] | None = None, project: str | None = None) -> dict:
    answers = [a for a in answers if str(a.get("text") or "").strip()]
    company = match_client(db, client_hint, client_id)
    cid = company["client_id"] if company else None
    rules = rules_for_company(db, cid, guidelines={str(g)[:16] for g in (guidelines or [])}, web=True, project=(project or "").strip()[:160] or None)
    rejected = rejected or []
    rej = {(str(r.get("original", "")).strip().lower(), str(r.get("replacement", "")).strip().lower()) for r in rejected}
    toks = _tokens(answers)
    allowed: set[str] = set()
    rule_ops, rule_skipped = auto_editor.rule_based_edits(toks, allowed, set())
    covered = {k for o in rule_ops for k in range(o.i1, o.i2)}
    rule_ops = rule_ops + char_rules(toks, answers, covered)
    provider, ai_status, ai_msg = resolve_provider()
    ai_ops: list[auto_editor.EditOp] = []
    ai_skipped: list[dict] = []
    ai_meta: dict = {}
    message = None
    if provider is None:
        message = "AI model not used: " + ai_msg + " Only the built-in grammar corrector ran."
    elif toks:
        fb = []
        if feedback:
            fb.append("Reviewer comment: " + feedback.strip()[:2000])
        if rejected:
            fb.append("The reviewer REJECTED these edits; do not propose them again: " + "; ".join(f"“{r.get('original')}” → “{r.get('replacement')}”" for r in rejected[:40]))
        for it in (item_feedback or [])[:40]:
            if str(it.get("comment") or "").strip():
                fb.append(f"Reviewer comment on the edit “{it.get('original')}” → “{it.get('replacement')}”: {str(it['comment']).strip()[:600]}")
        qa = "\n".join(f"[page {n}] QUESTION: {str(a.get('question') or '(not given)')[:300]}\nANSWER: {a['text'].replace(chr(10), ' ')}\n"
                       for n, a in enumerate(answers, start=1))[:9000]
        pages = [SimpleNamespace(page_no=n, text=a["text"]) for n, a in enumerate(answers, start=1)]
        try:
            ai_ops, ai_skipped, ai_meta = auto_editor.ai_edits(provider, toks, pages, "\n".join(fb) or None, allowed, set(), guidelines_text(rules["rules"]) or None, qa_text=qa)
        except AIError as exc:
            message = f"AI model failed ({exc}). Only the built-in grammar corrector ran."
    ops, overlap = auto_editor._resolve([_narrow(o, toks) for o in rule_ops + ai_ops], toks)
    ops = [o for o in ops if (o.original.strip().lower(), o.replacement.strip().lower()) not in rej]
    edits = []
    for o in sorted(ops, key=lambda o: o.i1):
        a = answers[toks[o.i1].page - 1]
        start, end = int(toks[o.i1].box[0]), int(toks[o.i2 - 1].box[2])
        page = toks[o.i1].page
        edits.append({"id": hashlib.sha1(f"{a['id']}:{start}:{end}:{o.replacement}".encode()).hexdigest()[:10], "qid": a["id"], "start": start, "end": end,
                      "original": a["text"][start:end], "replacement": o.replacement, "reason": o.reason, "category": o.category,
                      "confidence": round(o.confidence, 2), "source": o.source,
                      "before": " ".join(t.text for t in toks[max(0, o.i1 - 3):o.i1] if t.page == page),
                      "after": " ".join(t.text for t in toks[o.i2:o.i2 + 3] if t.page == page)})
    return {"edits": edits, "skipped": (rule_skipped + ai_skipped + overlap)[:60], "company": company, "rules_used": len(rules["rules"]),
            "company_rules": rules["company_rules"], "learned_rules": sum(1 for r in rules["rules"] if r.get("learned")), "manual_rules": sum(1 for r in rules["rules"] if r.get("manual")), "pending_rules": len(rules["pending_approval"]), "rules": rules["rules"],
            "engine": "ai+rules" if provider is not None and ai_meta else "rules", "ai_model": ai_meta.get("model") if ai_meta else None,
            "ai_status": ai_status.value if hasattr(ai_status, "value") else str(ai_status), "message": message, "answers": len(answers)}
