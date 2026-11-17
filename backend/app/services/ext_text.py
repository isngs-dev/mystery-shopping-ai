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
    return re.sub(r"[^a-z0-9]", "", s.lower())


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


def propose_text(db: Session, *, answers: list[dict], client_hint: str | None = None, client_id: int | None = None,
                 feedback: str | None = None, rejected: list[dict] | None = None, item_feedback: list[dict] | None = None) -> dict:
    answers = [a for a in answers if str(a.get("text") or "").strip()]
    company = match_client(db, client_hint, client_id)
    cid = company["client_id"] if company else None
    rules = rules_for_company(db, cid)
    rejected = rejected or []
    rej = {(str(r.get("original", "")).strip().lower(), str(r.get("replacement", "")).strip().lower()) for r in rejected}
    toks = _tokens(answers)
    allowed: set[str] = set()
    rule_ops, rule_skipped = auto_editor.rule_based_edits(toks, allowed, set())
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
    ops, overlap = auto_editor._resolve(rule_ops + ai_ops, toks)
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
            "company_rules": rules["company_rules"], "pending_rules": len(rules["pending_approval"]), "rules": rules["rules"],
            "engine": "ai+rules" if provider is not None and ai_meta else "rules", "ai_model": ai_meta.get("model") if ai_meta else None,
            "ai_status": ai_status.value if hasattr(ai_status, "value") else str(ai_status), "message": message, "answers": len(answers)}
