"""Deterministic QC checks. Each check is bound to a published, versioned rule so every finding can
cite the exact rule version that produced it. Straightforward validations live here; contextual
judgement ("ai_guideline" rules) is delegated to the AI adapter by the pipeline."""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from functools import lru_cache

from ..models import RuleVersion
from .compare import PUNCT, Comparison, Tok, locate
from .error_taxonomy import is_valid

SENT_END = (".", "!", "?")
ABBREV = {"mr.", "mrs.", "ms.", "dr.", "st.", "no.", "vs.", "etc.", "e.g.", "i.e.", "inc.", "ltd.", "approx.", "a.m.", "p.m."}
CONTRACTIONS = {
    "don't", "can't", "won't", "isn't", "wasn't", "couldn't", "wouldn't", "shouldn't", "didn't", "doesn't", "aren't",
    "weren't", "haven't", "hasn't", "hadn't", "i'm", "it's", "that's", "there's", "they're", "we're", "you're",
    "i've", "we've", "they've", "you've", "i'll", "we'll", "he's", "she's", "let's", "what's", "who's", "here's",
}
REPEAT_OK = {"had", "that", "very", "bye", "no", "so", "ha"}
NEGATORS = {"not", "no", "never", "none", "nobody", "nothing", "neither", "nor", "cannot", "without", "n't"}
NUM_RE = re.compile(r"\d{1,2}:\d{2}|\$?\d[\d,]*(?:\.\d+)?%?")      # a time is one value (10:30), tried before plain numbers
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
DATE_RE = re.compile(r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.? \d{1,2}", re.I)
PROFANITY = {"fuck", "fucking", "fucked", "fucker", "shit", "shitty", "bullshit", "bitch", "bitches", "bastard", "asshole", "arsehole",
             "crap", "dick", "dickhead", "piss", "pissed", "slut", "whore", "cunt", "nigger", "nigga", "retard", "retarded", "wtf", "motherfucker"}
INJECTION_RE = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+(instructions|rules|prompts?)"
    r"|system\s+prompt|you\s+are\s+now\s+(an?\s+)?(ai|assistant|model)|as\s+an\s+ai\s+(language\s+)?model"
    r"|approve\s+this\s+(form|report)\s+(automatically|without)|do\s+not\s+(report|flag)\s+(any\s+)?(errors|issues|findings)",
    re.IGNORECASE)
EMAIL_RE = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
PHONE_RE = r"(?<!\d)(?:\+?\d{1,2}[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)"


@dataclass
class Candidate:
    type: str
    category: str
    severity: str
    title: str
    description: str
    rule: RuleVersion | None
    confidence: float | None = None
    raw_span: tuple[int, int] | None = None
    edited_span: tuple[int, int] | None = None
    suggestion: str | None = None
    needs_human: bool = False
    key: tuple = ()
    source: str = "deterministic"
    subtype: str | None = None       # sub-type of the fixed error table; defaults to the rule's own sub-type


@dataclass
class Ctx:
    cmp: Comparison
    raw_pages: list
    edited_pages: list
    candidates: list[Candidate] = field(default_factory=list)
    skipped_rules: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    protected_raw: set[int] = field(default_factory=set)       # template / header / ignored text (never a finding)
    protected_edited: set[int] = field(default_factory=set)

    def changes(self):
        """Changed passages that are not entirely inside protected (template) text."""
        out = []
        for ch in self.cmp.changes:
            r_in = all(i in self.protected_raw for i in range(*ch.raw)) if ch.raw[1] > ch.raw[0] else True
            e_in = all(i in self.protected_edited for i in range(*ch.edited)) if ch.edited[1] > ch.edited[0] else True
            if not (r_in and e_in):
                out.append(ch)
        return out

    @property
    def raw(self) -> list[Tok]:
        return self.cmp.raw

    @property
    def edited(self) -> list[Tok]:
        return self.cmp.edited


# --------------------------------------------------------------------------------- helpers
def _origin(ctx: Ctx, j1: int, j2: int) -> tuple[str, tuple[int, int] | None]:
    """missed_correction if the flagged edited text is unchanged from RAW, else new_error."""
    if any(j in ctx.cmp.changed_edited for j in range(j1, j2)):
        return "new_error", None
    raws = [ctx.cmp.edited_to_raw[j] for j in range(j1, j2) if j in ctx.cmp.edited_to_raw]
    if raws:
        return "missed_correction", (min(raws), max(raws) + 1)
    return "new_error", None


def _add(ctx: Ctx, c: Candidate) -> None:
    if c.subtype is None and c.rule is not None:
        c.subtype = c.rule.error_subtype
    if not is_valid(c.subtype):
        return          # QC only reports what is on the error table
    if c.edited_span and all(i in ctx.protected_edited for i in range(*c.edited_span)):
        return
    if not c.edited_span and c.raw_span and all(i in ctx.protected_raw for i in range(*c.raw_span)):
        return
    if c.key in {x.key for x in ctx.candidates if x.key} and c.key:
        return
    ctx.candidates.append(c)


def _regex_spans(toks: list[Tok], pattern: str, flags=re.IGNORECASE) -> list[tuple[int, int]]:
    text, starts, pos = [], [], 0
    for t in toks:
        starts.append(pos)
        text.append(t.text)
        pos += len(t.text) + 1
    joined = " ".join(text)
    spans = []
    for m in re.finditer(pattern, joined, flags):
        if m.end() == m.start():
            continue
        i1 = bisect.bisect_right(starts, m.start()) - 1
        i2 = bisect.bisect_left(starts, m.end())
        spans.append((max(i1, 0), max(i2, i1 + 1)))
    return spans


def find_all(toks: list[Tok], phrase: str) -> list[tuple[int, int]]:
    words = [w.strip(PUNCT).lower() for w in phrase.split() if w.strip(PUNCT)]
    n = len(words)
    out = []
    if not n:
        return out
    for i in range(0, len(toks) - n + 1):
        if all(toks[i + k].norm == words[k] for k in range(n)):
            out.append((i, i + n))
    return out


@lru_cache
def _speller():
    from spellchecker import SpellChecker

    return SpellChecker(language="en")


# --------------------------------------------------------------------------------- built-in checks
def check_spelling(ctx: Ctx, rv: RuleVersion) -> None:
    spell = _speller()
    allowed = {str(t).lower() for t in rv.config.get("allowed_terms", [])}
    toks = ctx.edited
    counts: dict[str, int] = {}
    for t in toks:
        counts[t.norm] = counts.get(t.norm, 0) + 1
    for j, t in enumerate(toks):
        if j in ctx.protected_edited:
            continue
        w = t.text.strip(PUNCT).replace("’", "'")
        if len(w) < 3 or not re.match(r"^[A-Za-z][A-Za-z'\-]*$", w) or w.isupper():
            continue
        prev = toks[j - 1] if j > 0 else None
        sentence_start = prev is None or prev.text.endswith((".", "!", "?", ":")) or prev.page != t.page or prev.line != t.line
        if w[0].isupper() and not sentence_start:
            continue  # probable proper noun
        bad = []
        for part in w.split("-"):
            lw = part.lower()
            if len(lw) < 3 or lw in allowed or lw in CONTRACTIONS:
                continue
            if lw.endswith("'s"):
                lw = lw[:-2]
            lw = lw.replace("'", "")
            if lw in allowed or not lw:
                continue
            if spell.unknown([lw]):
                bad.append(lw)
        if not bad:
            continue
        if counts.get(t.norm, 0) >= 3 and t.norm in {x.norm for x in ctx.raw}:
            continue  # recurring term already in the source: likely a name/term
        word = bad[0]
        corr = spell.correction(word)
        cands = [c for c in (spell.candidates(word) or []) if c != word][:3]
        origin, rspan = _origin(ctx, j, j + 1)
        conf = 0.85 if corr and corr != word else 0.6
        suggestion = None
        if corr and corr != word:
            suggestion = f"Replace “{t.text.strip(PUNCT)}” with “{corr}”" + (f" (other candidates: {', '.join(sorted(cands))})" if len(cands) > 1 else "")
        _add(ctx, Candidate(
            origin, "spelling", rv.severity,
            ("Spelling error not corrected" if origin == "missed_correction" else "Spelling error introduced in edit"),
            f"“{t.text.strip(PUNCT)}” is not a recognised word in the standard dictionary."
            + (" It is unchanged from the RAW report." if origin == "missed_correction" else " It appears in text changed by the editor."),
            rv, conf, rspan, (j, j + 1), suggestion, needs_human=conf < 0.7, key=("spell", rv.id, j)))


def check_repeated_words(ctx: Ctx, rv: RuleVersion) -> None:
    toks = ctx.edited
    for j in range(len(toks) - 1):
        a, b = toks[j], toks[j + 1]
        if a.norm == b.norm and a.norm.isalpha() and len(a.norm) > 1 and a.norm not in REPEAT_OK and a.page == b.page:
            origin, rspan = _origin(ctx, j, j + 2)
            _add(ctx, Candidate(origin, "grammar", rv.severity, "Repeated word",
                                f"The word “{a.text.strip(PUNCT)}” appears twice in a row.", rv, 0.9, rspan, (j, j + 2),
                                f"Remove the duplicate “{a.text.strip(PUNCT)}”.", key=("rep", rv.id, j)))


def check_punct_space(ctx: Ctx, rv: RuleVersion) -> None:
    toks = ctx.edited
    for j in range(1, len(toks)):
        t = toks[j]
        if t.text in {",", ".", ";", ":", "!", "?"} and toks[j - 1].page == t.page and toks[j - 1].line == t.line:
            if t.text == ":" and toks[j - 1].text.isdigit() and j + 1 < len(toks) and toks[j + 1].text.isdigit():
                continue            # "11 : 00" - the hour and minute boxes of a time field
            origin, rspan = _origin(ctx, j - 1, j + 1)
            _add(ctx, Candidate(origin, "punctuation", rv.severity, "Space before punctuation",
                                f"A space precedes “{t.text}” after “{toks[j - 1].text}”.", rv, 0.8, rspan, (j - 1, j + 1),
                                f"Join as “{toks[j - 1].text}{t.text}”.", key=("psp", rv.id, j)))


def check_capitalization(ctx: Ctx, rv: RuleVersion) -> None:
    toks = ctx.edited
    for j in range(1, len(toks)):
        p, t = toks[j - 1], toks[j]
        w = t.text.strip(PUNCT)
        if not w or not w[0].isalpha() or j in ctx.protected_edited or "://" in w or "." in w[1:-1]:
            continue
        if p.page == t.page and p.text.endswith(SENT_END) and len(p.text) > 2 and p.text.lower() not in ABBREV and w[0].islower() \
                and p.line != -1 and not p.text.rstrip(".!?").isdigit():
            origin, rspan = _origin(ctx, j - 1, j + 1)
            _add(ctx, Candidate(origin, "grammar", rv.severity, "Sentence does not start with a capital",
                                f"“{w}” follows a full stop but is not capitalised.", rv, 0.75, rspan, (j - 1, j + 1),
                                f"Capitalise to “{w[0].upper() + w[1:]}”.", key=("cap", rv.id, j)))
        if w == "i" and t.text.strip(PUNCT) == "i":
            origin, rspan = _origin(ctx, j, j + 1)
            _add(ctx, Candidate(origin, "grammar", rv.severity, "Pronoun “I” not capitalised", "Standalone “i” should be “I”.",
                                rv, 0.85, rspan, (j, j + 1), "Change “i” to “I”.", key=("capi", rv.id, j)))


def check_articles(ctx: Ctx, rv: RuleVersion) -> None:
    toks = ctx.edited
    vowel_sound_exceptions = {"one", "once", "ones", "useful", "user", "users", "usual", "united", "uniform", "unit", "european"}
    silent_h = {"hour", "hours", "honest", "honor", "honour", "heir"}
    for j in range(len(toks) - 1):
        a, nxt = toks[j].norm, toks[j + 1].norm
        if not nxt or not nxt[0].isalpha() or toks[j].page != toks[j + 1].page:
            continue
        bad = None
        if a == "a" and nxt[0] in "aeio" and nxt not in vowel_sound_exceptions:
            bad = ("an", f"“a {toks[j + 1].text}” should be “an {toks[j + 1].text}”.")
        elif a == "an" and nxt[0] not in "aeiouh" and nxt not in vowel_sound_exceptions | silent_h:
            bad = ("a", f"“an {toks[j + 1].text}” should be “a {toks[j + 1].text}”.")
        if bad:
            origin, rspan = _origin(ctx, j, j + 2)
            _add(ctx, Candidate(origin, "grammar", rv.severity, "Incorrect article (a/an)", bad[1], rv, 0.65, rspan, (j, j + 2),
                                f"Use “{bad[0]}”.", needs_human=True, key=("art", rv.id, j)))


def _pii(ctx: Ctx, rv: RuleVersion) -> None:
    patterns = rv.config.get("patterns") or [EMAIL_RE, PHONE_RE]
    for pat in patterns:
        for i1, i2 in _regex_spans(ctx.edited, pat):
            origin, rspan = _origin(ctx, i1, i2)
            _add(ctx, Candidate(origin, "pii", rv.severity, "Personal contact detail present",
                                "The report contains what looks like a personal email address or phone number; "
                                "shopper/third-party contact details must not appear in deliverables.", rv, 0.8, rspan, (i1, i2),
                                "Remove or redact the contact detail unless the client brief explicitly requires it.",
                                needs_human=True, key=("pii", rv.id, i1)))


def _noise(text: str) -> bool:
    """Page furniture or unreadable extraction (URLs, reference numbers, font-encoding garbage) is not the shopper's report."""
    if re.search(r"https?://|www\.|\.php|\.asp", text, re.I):
        return True
    words = text.split()
    real = [w for w in words if re.fullmatch(r"[A-Za-z][a-z'’\-]{2,}[.,;:!?]?|[A-Z][a-z]+", w)]
    return len(words) >= 8 and len(real) / len(words) < 0.4


def _numbers_and_negation(ctx: Ctx, rv: RuleVersion, kind: str) -> None:
    for ch in ctx.changes():
        rt = " ".join(ctx.raw[i].text for i in range(*ch.raw) if i not in ctx.protected_raw)           # page furniture / stamps never count
        et = " ".join(ctx.edited[i].text for i in range(*ch.edited) if i not in ctx.protected_edited)
        if _noise(rt) or _noise(et):
            continue
        if kind == "numbers":
            rn, en = sorted(NUM_RE.findall(rt)), sorted(NUM_RE.findall(et))
            if rn == en:
                continue
            desc = f"RAW contains {rn or 'no figures'} but the edited text contains {en or 'no figures'} in the same passage."
            sugg = "Check the shopper’s original data. Restore the RAW value unless the change is a confirmed correction."
            ftype, conf = "meaning_change", 0.9
            changed = set(rn) ^ set(en)
            if any(TIME_RE.match(x) for x in changed) or DATE_RE.search(rt) or DATE_RE.search(et):
                title, subtype = "Date / time changed", "incorrect_date_time"          # Missed Instruction
            else:
                title, subtype = "Figure or amount changed", "meaning_changed"          # Contradiction
        else:
            rn = sum(1 for w in re.findall(r"[A-Za-z']+", rt.lower()) if w in NEGATORS or w.endswith("n't"))
            en = sum(1 for w in re.findall(r"[A-Za-z']+", et.lower()) if w in NEGATORS or w.endswith("n't"))
            if rn == en:
                continue
            title = "Negation added or removed"
            desc = "The number of negating words differs between RAW and edited text, which can reverse the meaning."
            sugg = "Confirm the edited statement still reflects what the shopper actually observed."
            ftype, conf, subtype = "meaning_change", 0.8, "meaning_changed"
        _add(ctx, Candidate(ftype, "meaning_change", rv.severity, title, desc, rv, conf,
                            ch.raw if ch.raw[1] > ch.raw[0] else None, ch.edited if ch.edited[1] > ch.edited[0] else None,
                            sugg, needs_human=True, key=(kind, rv.id, ch.id), subtype=subtype))


def check_omission_addition(ctx: Ctx, rv: RuleVersion, kind: str) -> None:
    min_words = int(rv.config.get("min_words", 6 if kind == "omission" else 8))
    for ch in ctx.changes():
        if _noise(ch.raw_text) or _noise(ch.edited_text):
            continue
        rl, el = ch.raw[1] - ch.raw[0], ch.edited[1] - ch.edited[0]
        if kind == "omission":
            hit = (ch.op == "delete" and rl >= min_words) or (ch.op == "replace" and rl >= min_words and el <= rl * 0.4)
            if not hit:
                continue
            _add(ctx, Candidate("missing_info", "omission", rv.severity, "Content removed from the RAW report",
                                f"{rl} words of the RAW report are absent or heavily reduced in the edited version.", rv, 0.8,
                                ch.raw, ch.edited if el else None,
                                "Confirm the removed content is not factual shopper observation; restore it if it is required.",
                                needs_human=True, key=("omit", rv.id, ch.id)))
        else:
            hit = (ch.op == "insert" and el >= min_words) or (ch.op == "replace" and el >= max(10, min_words) and el >= rl * 2.5)
            if not hit:
                continue
            _add(ctx, Candidate("meaning_change", "added_content", rv.severity, "New content not present in the RAW report",
                                f"{el} words were added by the editor. Added statements must be supported by the shopper’s RAW data.",
                                rv, 0.7, ch.raw if rl else None, ch.edited,
                                "Verify the added text is supported by the RAW report; remove anything the shopper did not state.",
                                needs_human=True, key=("add", rv.id, ch.id)))


def check_required_labels(ctx: Ctx, rv: RuleVersion) -> None:
    labels = rv.config.get("labels") or []
    for label in labels:
        def inspect(toks: list[Tok]):
            span = locate(toks, label)
            if not span:
                return None, False
            i2 = span[1]
            base = toks[span[0]]
            vals = []
            k = i2
            while k < len(toks) and toks[k].page == base.page and toks[k].line == base.line:
                if toks[k].text.strip("_-: ") != "":
                    vals.append(toks[k])
                k += 1
            return span, bool(vals)

        espan, efilled = inspect(ctx.edited)
        rspan, rfilled = inspect(ctx.raw)
        if espan and efilled:
            continue
        if not espan:
            desc = f"Required field “{label}” was not found in the edited report."
            if rspan:
                desc += " It exists in the RAW report, so it was removed during editing."
            _add(ctx, Candidate("missing_info", "completeness", rv.severity, f"Required field missing: {label}", desc, rv, 0.85,
                                rspan, None, f"Add “{label}” with the value from the RAW report.",
                                key=("req", rv.id, label)))
        else:
            desc = f"Required field “{label}” is present but has no value."
            if rfilled:
                desc += " The RAW report has a value, so it was lost in editing."
            _add(ctx, Candidate("missing_info", "completeness", rv.severity, f"Required field empty: {label}", desc, rv, 0.85,
                                rspan, espan, "Provide the value as stated by the shopper.", key=("req", rv.id, label)))


def check_forbidden_terms(ctx: Ctx, rv: RuleVersion) -> None:
    for term in rv.config.get("terms", []):
        for i1, i2 in find_all(ctx.edited, str(term)):
            origin, rspan = _origin(ctx, i1, i2)
            _add(ctx, Candidate(origin, rv.category, rv.severity, f"Prohibited term: “{term}”",
                                (rv.description or rv.name) + f" The term “{term}” must not appear.", rv, 0.9, rspan, (i1, i2),
                                rv.config.get("suggestion") or "Rephrase to comply with the rule.", key=("term", rv.id, i1)))


_NESTED = re.compile(r"\((?:[^()]*[+*])\)[+*]")


def check_regex_forbidden(ctx: Ctx, rv: RuleVersion) -> None:
    pat = rv.config.get("pattern", "")
    if not pat or len(pat) > 300 or _NESTED.search(pat):
        ctx.skipped_rules.append({"rule": rv.name, "reason": "Pattern missing, too long, or unsafe (nested quantifiers)."})
        return
    try:
        spans = _regex_spans(ctx.edited, pat, 0 if rv.config.get("case_sensitive") else re.IGNORECASE)
    except re.error as exc:
        ctx.skipped_rules.append({"rule": rv.name, "reason": f"Invalid pattern: {exc}"})
        return
    for i1, i2 in spans:
        origin, rspan = _origin(ctx, i1, i2)
        _add(ctx, Candidate(origin, rv.category, rv.severity, rv.name, rv.config.get("message") or rv.description, rv, 0.85,
                            rspan, (i1, i2), rv.config.get("suggestion"), key=("rx", rv.id, i1)))


def check_max_sentence(ctx: Ctx, rv: RuleVersion) -> None:
    mx = int(rv.config.get("max", 40))
    start = 0
    toks = ctx.edited
    for j, t in enumerate(toks):
        if t.text.endswith(SENT_END) or j == len(toks) - 1:
            if j - start + 1 > mx:
                origin, rspan = _origin(ctx, start, j + 1)
                _add(ctx, Candidate(origin, rv.category, rv.severity, "Sentence too long",
                                    f"Sentence has {j - start + 1} words (limit {mx}).", rv, 0.9, rspan, (start, min(j + 1, start + 40)),
                                    "Split the sentence.", key=("long", rv.id, start)))
            start = j + 1


def check_missing_space(ctx: Ctx, rv: RuleVersion) -> None:
    """“store.The” / “said,hello”: no space after punctuation (Extra Space/No space)."""
    glued = re.compile(r"^[A-Za-z]{2,}[,;:][A-Za-z]{2,}$|^[a-z]{3,}\.[A-Z][a-z]{2,}$")
    for j, t in enumerate(ctx.edited):
        if "@" in t.text or "www" in t.text.lower() or "http" in t.text.lower() or not glued.match(t.text.strip("()\"“”'’")):
            continue
        origin, rspan = _origin(ctx, j, j + 1)
        _add(ctx, Candidate(origin, "extra_space", rv.severity, "No space after punctuation",
                            f"“{t.text}” has no space after its punctuation mark.", rv, 0.8, rspan, (j, j + 1),
                            "Insert a space after the punctuation mark.", key=("nsp", rv.id, j)))


def check_double_punctuation(ctx: Ctx, rv: RuleVersion) -> None:
    bad = re.compile(r"(?<!\.)\.\.(?!\.)|,,|;;|::|!!|\?\?|,\.|\.,")
    for j, t in enumerate(ctx.edited):
        if bad.search(t.text) and "http" not in t.text.lower() and "www" not in t.text.lower():
            origin, rspan = _origin(ctx, j, j + 1)
            _add(ctx, Candidate(origin, "punctuation", rv.severity, "Doubled punctuation",
                                f"“{t.text}” repeats or stacks punctuation marks.", rv, 0.85, rspan, (j, j + 1),
                                "Keep a single punctuation mark.", key=("dp", rv.id, j)))


def check_profanity(ctx: Ctx, rv: RuleVersion) -> None:
    words = PROFANITY | {str(w).lower() for w in rv.config.get("words", [])}
    for j, t in enumerate(ctx.edited):
        if t.norm in words:
            origin, rspan = _origin(ctx, j, j + 1)
            _add(ctx, Candidate(origin, "profanity", rv.severity, "Profanity in the report",
                                f"“{t.text.strip(PUNCT)}” is foul or offensive language.", rv, 0.9, rspan, (j, j + 1),
                                "Remove the word or rephrase neutrally.", needs_human=True, key=("prof", rv.id, j)))


def injection_hits(cmp: Comparison) -> int:
    """Instruction-like text inside a document (prompt injection). It is never followed and is not a QC error: the pipeline
    only warns the reviewer about it."""
    return sum(len(_regex_spans(toks, INJECTION_RE.pattern)) for toks in (cmp.edited, cmp.raw))


BUILTINS = {
    "spelling": check_spelling,
    "repeated_words": check_repeated_words,
    "punctuation_spacing": check_punct_space,
    "capitalization": check_capitalization,
    "articles": check_articles,
    "missing_space": check_missing_space,
    "double_punctuation": check_double_punctuation,
    "profanity": check_profanity,
    "pii": _pii,
    "numbers": lambda c, r: _numbers_and_negation(c, r, "numbers"),
    "negation": lambda c, r: _numbers_and_negation(c, r, "negation"),
    "omission": lambda c, r: check_omission_addition(c, r, "omission"),
    "addition": lambda c, r: check_omission_addition(c, r, "addition"),
}
CHECKS = {
    "required_labels": check_required_labels,
    "forbidden_terms": check_forbidden_terms,
    "regex_forbidden": check_regex_forbidden,
    "max_sentence_words": check_max_sentence,
}


def run_deterministic(ctx: Ctx, rules: list[RuleVersion]) -> list[RuleVersion]:
    """Run every applicable deterministic rule. Returns the ai_guideline rules left for the AI stage."""
    ai_rules = []
    for rv in rules:
        if rv.check_type == "template_profile":
            continue  # consumed by template_guard before the checks run
        if rv.check_type == "ai_guideline":
            ai_rules.append(rv)
            continue
        fn = BUILTINS.get(rv.config.get("builtin")) if rv.check_type == "builtin" else CHECKS.get(rv.check_type)
        if not fn:
            ctx.skipped_rules.append({"rule": rv.name, "reason": f"Unknown check type {rv.check_type}."})
            continue
        fn(ctx, rv)
    return ai_rules
