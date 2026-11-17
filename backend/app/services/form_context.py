"""Question / answer view of a survey report.

The report PDF mixes two kinds of text: the *questions* (form wording, the same in every report of the project; the editor never touches it) and the
shopper's *answers* (the only text an editor may correct). The editor needs to know which question an answer belongs to, because the question tells
what the answer has to look like: "Please describe the circumstances" wants a past-tense narrative, a question about what the employee said wants the
spoken words in quotation marks, and so on.

Questions are found with the protected (template) tokens when they are known, and with the wording of the line (ends with "?" or ":", "Please ...")
as a fallback for a project whose template has not been learned yet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .compare import Tok

QUESTION_LINE = re.compile(r"(\?\s*$)|(:\s*$)|^(please|describe|explain|what|how|why|were you|did you|do you|are you|is there|was there|list|provide|enter|write)\b", re.I)
NOISE_LINE = re.compile(r"^(spell check|upload|rotate|delete picture|yes|no|n/?a|pc|\d+\s*/\s*\d+|\d{1,2}/\d{1,2}/\d{2,4}.*|https?://\S+)$", re.I)


@dataclass
class QA:
    question: str
    answer: str
    page: int
    i1: int
    i2: int          # token range of the answer


def _lines(toks: list[Tok]) -> list[tuple[int, int, list[int]]]:
    out: list[tuple[int, int, list[int]]] = []
    for i, t in enumerate(toks):
        if out and out[-1][0] == t.page and out[-1][1] == t.line:
            out[-1][2].append(i)
        else:
            out.append((t.page, t.line, [i]))
    return out


def question_answers(toks: list[Tok], protected: set[int] | None = None, min_words: int = 4) -> list[QA]:
    """Pair every run of answer lines with the question lines in front of it."""
    protected = protected or set()
    pairs: list[QA] = []
    q_lines: list[str] = []
    a_idx: list[int] = []

    def flush() -> None:
        nonlocal a_idx
        if a_idx and len(a_idx) >= min_words:
            q = " ".join(q_lines[-3:]).strip()
            pairs.append(QA(q[:400], " ".join(toks[i].text for i in a_idx), toks[a_idx[0]].page, a_idx[0], a_idx[-1] + 1))
        a_idx = []

    for page, _ln, idx in _lines(toks):
        text = " ".join(toks[i].text for i in idx)
        free = [i for i in idx if i not in protected]
        strong = bool(re.search(r"[?:]\s*$", text)) and len(idx) >= 2                    # a line that ends with ? or : is form wording
        is_question = (not free) or (len(free) < len(idx) / 2) or strong or bool(QUESTION_LINE.search(text) and len(idx) >= 2 and not protected_known(protected))
        if NOISE_LINE.match(text.strip()):
            continue
        if is_question:
            if a_idx:
                flush()
                q_lines.clear()
            q_lines.append(text)
        else:
            a_idx.extend(free)
    flush()
    return pairs


def protected_known(protected: set[int]) -> bool:
    return len(protected) > 20


def block_text(pairs: list[QA], limit: int = 7000) -> str:
    """The pairs as text for the editing model."""
    out: list[str] = []
    used = 0
    for p in pairs:
        s = f"[page {p.page}] QUESTION: {p.question or '(not found)'}\nANSWER: {p.answer}\n"
        if used + len(s) > limit:
            break
        out.append(s)
        used += len(s)
    return "\n".join(out)


def quote_balance(text: str) -> int:
    """Number of double quotation marks in the text (odd = an opening or closing mark is missing)."""
    return sum(1 for c in text if c in '"“”')
