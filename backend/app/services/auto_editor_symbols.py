"""Quotation marks, brackets, apostrophes, symbols and run-together sentences for the built-in editor.

Only the way the words are written changes, never what they say. Every edit is a small, visible change that the reviewer can dismiss in the panel.
"""
from __future__ import annotations

import re

from .compare import Tok

QUOTES = '"“”'
MISSING_APOS = {"dont": "don't", "cant": "can't", "didnt": "didn't", "doesnt": "doesn't", "wasnt": "wasn't", "werent": "weren't", "wouldnt": "wouldn't",
                "couldnt": "couldn't", "shouldnt": "shouldn't", "isnt": "isn't", "arent": "aren't", "havent": "haven't", "hasnt": "hasn't", "hadnt": "hadn't",
                "ive": "I've", "youre": "you're", "theyre": "they're", "thats": "that's", "theres": "there's"}
RUN_TOGETHER = {"alot": "a lot", "infront": "in front", "aswell": "as well", "incase": "in case", "atleast": "at least", "eachother": "each other"}
SENTENCE_STARTERS = {"she", "he", "they", "we", "it", "there", "while", "after", "then", "when", "my", "our", "his", "her", "their", "this", "that", "these",
                     "those", "however", "also", "the"}
NOT_SENTENCE_END = {"the", "a", "an", "of", "in", "at", "to", "and", "or", "but", "for", "with", "on", "by", "from", "as", "if", "that", "than", "so", "is", "was",
                    "are", "were", "be", "been", "not", "no", "my", "our", "his", "her", "their", "your", "its", "who", "which", "what", "when", "where", "into",
                    "about", "over", "under", "near", "between", "through", "during", "before", "after", "while", "because", "although"}
UNBALANCED = "Quotation marks are not balanced in this answer (one opening or closing mark is missing); needs a human"


def _qn(text: str) -> int:
    """Double quotation marks in a token; 5'3" (inches) is a unit, not a quotation mark."""
    return sum(1 for i, c in enumerate(text) if c in QUOTES and not (i and text[i - 1].isdigit()))


def _split(text: str) -> tuple[str, str, str]:
    m = re.match(r"^(\W*)(.*?)(\W*)$", text, re.S)
    return (m.group(1), m.group(2), m.group(3)) if m else ("", text, "")


def _match_case(orig: str, corr: str) -> str:
    if len(orig) > 1 and orig.isupper():
        return corr.upper()
    if orig[:1].isupper():
        return corr[:1].upper() + corr[1:]
    return corr


def symbol_edits(toks: list[Tok], protected: set[int], make_op) -> tuple[list, list[dict]]:
    """Returns (ops, skipped). `make_op(i1, i2, replacement, reason, category, confidence)` builds the EditOp."""
    ops: list = []
    skipped: list[dict] = []

    def add(i1, i2, repl, reason, cat, conf):
        ops.append(make_op(i1, i2, repl, reason, cat, conf))

    def same(a, b):
        return toks[a].page == toks[b].page and toks[a].line == toks[b].line

    # opening / closing state of every double quotation mark; it restarts with every question / template text,
    # so one missing mark cannot spoil the rest of the report
    before: list[int] = [0] * len(toks)
    parity, last_q = 0, None
    for j, t in enumerate(toks):
        if j in protected:
            if parity and last_q is not None:
                skipped.append({"page": toks[last_q].page, "original": toks[last_q].text, "proposed": None, "reason": UNBALANCED})
            parity, last_q = 0, None
            continue
        before[j] = parity
        n = _qn(t.text)
        if n:
            last_q = j
            parity = (parity + n) % 2
    if parity and last_q is not None:
        skipped.append({"page": toks[last_q].page, "original": toks[last_q].text, "proposed": None, "reason": UNBALANCED})

    for j, t in enumerate(toks):
        if j in protected:
            continue
        txt = t.text
        if re.search(r"://|@|www\.", txt, re.I):
            continue
        prev = toks[j - 1] if j and same(j - 1, j) and (j - 1) not in protected else None
        nxt = toks[j + 1] if j + 1 < len(toks) and same(j, j + 1) and (j + 1) not in protected else None
        nq = _qn(txt)

        # --- a quotation mark standing alone: no space inside the quotation
        if txt in ('"', "“", "”"):
            if before[j] == 0 and nxt:
                add(j, j + 2, txt + nxt.text, "Space after the opening quotation mark removed", "quotation", 0.9)
            elif before[j] == 1 and prev:
                add(j - 1, j + 1, prev.text + txt, "Space before the closing quotation mark removed", "quotation", 0.9)
            continue
        # --- full stop / comma belongs inside the closing quotation mark (US style): here". -> here."
        m = re.fullmatch(r"(.*[A-Za-z0-9)])([\"”])([.,])", txt)
        if m and nq == 1 and before[j] == 1:
            add(j, j + 1, m.group(1) + m.group(3) + m.group(2), "Full stop / comma goes inside the closing quotation mark", "quotation", 0.8)
            continue
        # --- no space after a closing quotation mark that ends a sentence: here."I -> here." I
        m = re.fullmatch(r"(.*[.,!?][\"”])([A-Z][a-z]+.*)", txt)
        if m and nq == 1 and before[j] == 1:
            add(j, j + 1, m.group(1) + " " + m.group(2), "Space added after the closing quotation mark", "quotation", 0.85)
            continue
        # --- opening quotation mark glued to the word before it: said"I -> said "I
        m = re.fullmatch(r"([A-Za-z]+,?)([\"“])([A-Za-z].*)", txt)
        if m and nq == 1 and before[j] == 0:
            add(j, j + 1, m.group(1) + " " + m.group(2) + m.group(3), "Space added before the opening quotation mark", "quotation", 0.85)
            continue
        # --- brackets: ( my name ) -> (my name)
        if txt == "(" and nxt:
            add(j, j + 2, "(" + nxt.text, "Space after the opening bracket removed", "symbols", 0.9)
            continue
        if re.fullmatch(r"\)[.,;:!?]*", txt) and prev and not prev.text.endswith(")"):
            add(j - 1, j + 1, prev.text + txt, "Space before the closing bracket removed", "symbols", 0.9)
            continue
        if re.fullmatch(r"[A-Za-z]{2,}\([A-Za-z].*", txt):
            add(j, j + 1, re.sub(r"^([A-Za-z]{2,})\(", r"\1 (", txt), "Space added before the opening bracket", "symbols", 0.8)
            continue
        # --- % and $ stand next to the figure
        if txt == "%" and prev and re.fullmatch(r"\d+(\.\d+)?", prev.text):
            add(j - 1, j + 1, prev.text + "%", "Space before “%” removed", "symbols", 0.85)
            continue
        if txt == "$" and nxt and re.fullmatch(r"\d[\d,]*(\.\d+)?[.,]?", nxt.text):
            add(j, j + 2, "$" + nxt.text, "Space after “$” removed", "symbols", 0.85)
            continue
        # --- ellipsis and odd apostrophes
        if re.search(r"\.{4,}", txt) and re.search(r"[A-Za-z]", txt):
            add(j, j + 1, re.sub(r"\.{4,}", "...", txt), "Too many dots reduced to an ellipsis", "symbols", 0.85)
            continue
        if re.fullmatch(r"[A-Za-z]+[`´][a-z]+[.,!?;:]?", txt):
            add(j, j + 1, re.sub(r"[`´]", "'", txt), "Wrong apostrophe symbol replaced", "symbols", 0.85)
            continue
        lead, core, trail = _split(txt)
        lw = core.lower()
        if lw in MISSING_APOS and lead == "":
            fix = _match_case(core, MISSING_APOS[lw])
            add(j, j + 1, fix + trail, f"Apostrophe missing: “{core}” → “{fix}”", "symbols", 0.85)
            continue
        if lw in RUN_TOGETHER and lead == "":
            fix = _match_case(core, RUN_TOGETHER[lw])
            add(j, j + 1, fix + trail, f"Two words written as one: “{core}” → “{fix}”", "grammar", 0.85)
            continue
        # --- stray symbols are only flagged: removing them could remove something the shopper meant
        if re.fullmatch(r"[*^~|\\<>_]{1,4}", txt):
            skipped.append({"page": t.page, "original": txt, "proposed": None, "reason": f"Stray symbol “{txt}”; needs a human"})
            continue
        # --- two sentences run together: "said no While she ..." -> "said no. While she ..."
        if nxt and re.fullmatch(r"[a-z]{3,}", txt) and txt not in NOT_SENTENCE_END and re.fullmatch(r"[A-Z][a-z]*", nxt.text) and nxt.norm in SENTENCE_STARTERS:
            add(j, j + 1, txt + ".", f"Full stop added before “{nxt.text}” (two sentences run together)", "sentence", 0.7)
            continue
    return ops, skipped
