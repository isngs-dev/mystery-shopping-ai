"""Word-level comparison of RAW vs edited documents, keeping page numbers and bounding boxes
so every change can be tied to real evidence on a real page."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

PUNCT = ".,;:!?\"'()[]{}<>“”‘’*"


@dataclass
class Tok:
    text: str
    norm: str
    page: int
    box: tuple[float, float, float, float]
    idx: int = 0
    line: int = 0  # line ordinal within page (derived from y)


def _norm(t: str) -> str:
    n = t.strip(PUNCT).lower()
    return n if n else t


def build_tokens(pages) -> list[Tok]:
    toks: list[Tok] = []
    for p in pages:
        words = p.words or []
        if not words and p.text:  # pages without geometry: fall back to plain text tokens
            for w in p.text.split():
                toks.append(Tok(w, _norm(w), p.page_no, (0, 0, 0, 0)))
            continue
        line, last_y = 0, None
        for w in words:
            x0, y0, x1, y1, txt = w[0], w[1], w[2], w[3], w[4]
            cy = (y0 + y1) / 2
            if last_y is not None and abs(cy - last_y) > (y1 - y0) * 0.6:
                line += 1
            last_y = cy
            toks.append(Tok(txt, _norm(txt), p.page_no, (x0, y0, x1, y1), line=line))
    for i, t in enumerate(toks):
        t.idx = i
    return toks


def boxes_for(toks: list[Tok], i1: int, i2: int) -> list[dict]:
    """Merge tokens i1..i2 into one box per visual line for highlighting."""
    out: list[dict] = []
    cur = None
    for t in toks[i1:i2]:
        if t.box == (0, 0, 0, 0):
            continue
        key = (t.page, t.line)
        if cur and cur["key"] == key:
            cur["x0"], cur["y0"] = min(cur["x0"], t.box[0]), min(cur["y0"], t.box[1])
            cur["x1"], cur["y1"] = max(cur["x1"], t.box[2]), max(cur["y1"], t.box[3])
        else:
            if cur:
                out.append(cur)
            cur = {"key": key, "page": t.page, "x0": t.box[0], "y0": t.box[1], "x1": t.box[2], "y1": t.box[3]}
    if cur:
        out.append(cur)
    for o in out:
        o.pop("key")
        for k in ("x0", "y0", "x1", "y1"):
            o[k] = round(o[k], 2)
    return out


def span_text(toks: list[Tok], i1: int, i2: int) -> str:
    return " ".join(t.text for t in toks[i1:i2])


def excerpt(toks: list[Tok], i1: int, i2: int, ctx: int = 6, max_tokens: int = 60) -> str:
    """Evidence excerpt: the span with a little surrounding context (context is genuine document text)."""
    if i2 - i1 > max_tokens:
        i2 = i1 + max_tokens
    a, b = max(0, i1 - ctx), min(len(toks), i2 + ctx)
    return " ".join(t.text for t in toks[a:b])


def span_page(toks: list[Tok], i1: int) -> int | None:
    return toks[i1].page if 0 <= i1 < len(toks) else None


@dataclass
class Change:
    id: int
    op: str  # replace | delete | insert
    raw: tuple[int, int]
    edited: tuple[int, int]
    raw_text: str = ""
    edited_text: str = ""


@dataclass
class Comparison:
    raw: list[Tok]
    edited: list[Tok]
    changes: list[Change] = field(default_factory=list)
    changed_edited: set[int] = field(default_factory=set)   # edited token idx inside changed regions
    changed_raw: set[int] = field(default_factory=set)
    edited_to_raw: dict[int, int] = field(default_factory=dict)  # aligned (equal) tokens
    similarity: float = 1.0


def compare_tokens(raw: list[Tok], edited: list[Tok]) -> Comparison:
    a = [t.norm for t in raw]
    b = [t.norm for t in edited]
    sm = SequenceMatcher(None, a, b, autojunk=False)
    cmp = Comparison(raw=raw, edited=edited, similarity=round(sm.ratio(), 4) if (a or b) else 1.0)
    opcodes = sm.get_opcodes()
    # merge hunks separated by a single equal token (reads as one edit to a human)
    merged: list[list] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for k in range(i2 - i1):
                cmp.edited_to_raw[j1 + k] = i1 + k
            merged.append([tag, i1, i2, j1, j2])
            continue
        if merged and merged[-1][0] != "equal":
            prev = merged[-1]
            prev[2], prev[4] = i2, j2
            prev[0] = "replace"
        elif len(merged) >= 2 and merged[-1][0] == "equal" and (merged[-1][2] - merged[-1][1]) == 1 \
                and merged[-2][0] != "equal":
            eq = merged.pop()
            prev = merged[-1]
            for k in range(eq[2] - eq[1]):  # un-align the bridged token
                cmp.edited_to_raw.pop(eq[3] + k, None)
            prev[2], prev[4] = i2, j2
            prev[0] = "replace"
        else:
            merged.append([tag, i1, i2, j1, j2])
    cid = 0
    for tag, i1, i2, j1, j2 in merged:
        if tag == "equal":
            continue
        cid += 1
        op = "replace" if (i2 > i1 and j2 > j1) else ("delete" if i2 > i1 else "insert")
        cmp.changes.append(Change(cid, op, (i1, i2), (j1, j2), span_text(raw, i1, i2), span_text(edited, j1, j2)))
        cmp.changed_raw.update(range(i1, i2))
        cmp.changed_edited.update(range(j1, j2))
    return cmp


def locate(toks: list[Tok], needle: str) -> tuple[int, int] | None:
    """Find `needle` verbatim (case/punctuation-insensitive) in the token stream. Used to verify that
    AI-quoted evidence genuinely exists; unverifiable evidence is rejected."""
    words = [_norm(w) for w in re.split(r"\s+", needle.strip()) if w]
    if not words:
        return None
    n = len(words)
    first = words[0]
    for i in range(0, len(toks) - n + 1):
        if toks[i].norm != first:
            continue
        if all(toks[i + k].norm == words[k] for k in range(1, n)):
            return i, i + n
    return None
