"""The fixed QC error categorisation. This is the ONLY set of things QC looks for.

It mirrors the team's "ERROR Categorization" sheet:

    Total score 100
    Grammar Mistake                       5   (95)   Punctuation · Extra Space/No space · Spelling · Past tense · Incorrect Terminology · Profanity
    Missed Instruction                   15   (85)   Negative not answered · Video not checked thoroughly and BOTD or N/A marked · Audio not evaluated
                                                      correctly · Call Attempt not checked · Incorrect Date and Time
    Contradiction                        20          Comment and Marking do not match · Original sentences changed which changed the meaning ·
                                                      Incorrect option selected as per picture
    Wrong Location / Put on Different    25          Shop was performed incorrectly · Closed location treated as Open · Status Incorrect selected
      category

Every finding (automatic, AI or added by a reviewer) must carry one of these sub-types. A rule that is not mapped to a
sub-type is never run, and the AI is only allowed to answer with these sub-types. ``manual_only`` sub-types depend on the
video / audio / picture / call log, which the system cannot see, so they are checked by the QC reviewer (they appear as a
checklist in the QC workspace and can be added as findings).
"""
from __future__ import annotations

from dataclasses import dataclass

TOTAL_SCORE = 100


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    points: int           # marks deducted from the total score when this category has a confirmed error
    severity: str         # severity given to findings of this category


@dataclass(frozen=True)
class Subtype:
    key: str
    category: str
    label: str
    hint: str             # what the checker looks for (also given to the AI)
    manual_only: bool = False   # needs video / audio / picture / call log: reviewer-checked, never reported by AI


CATEGORIES: list[Category] = [
    Category("grammar", "Grammar Mistake", 5, "minor"),
    Category("missed_instruction", "Missed Instruction", 15, "major"),
    Category("contradiction", "Contradiction", 20, "critical"),
    Category("wrong_location", "Wrong Location / Put on Different category", 25, "critical"),
]

SUBTYPES: list[Subtype] = [
    # ------------------------------------------------------------------ Grammar Mistake (5)
    Subtype("punctuation", "grammar", "Punctuation",
            "Wrong, missing or doubled punctuation marks; a sentence that does not start with a capital letter; quotation punctuation."),
    Subtype("extra_space", "grammar", "Extra Space/No space",
            "A space where there must be none (before , . ; : ! ?) or a missing space after punctuation."),
    Subtype("spelling", "grammar", "Spelling", "A misspelt word."),
    Subtype("past_tense", "grammar", "Past tense",
            "Comments about what happened during the shop must be written in the past tense and in complete sentences."),
    Subtype("terminology", "grammar", "Incorrect Terminology",
            "A word or phrase the client does not allow, or the wrong term used (e.g. “employee” instead of “associate”)."),
    Subtype("profanity", "grammar", "Profanity", "Foul, offensive or racial language in the report."),
    # ------------------------------------------------------------------ Missed Instruction (15)
    Subtype("negative_not_answered", "missed_instruction", "Negative not answered",
            "A negative (No / N/A) marking that has no supporting comment, or a question left without an answer."),
    Subtype("video_not_checked", "missed_instruction", "Video not checked thoroughly and BOTD or N/A marked",
            "The video was not watched thoroughly, or BOTD / N/A was marked although the video shows the answer.", manual_only=True),
    Subtype("audio_not_evaluated", "missed_instruction", "Audio not evaluated correctly",
            "The audio recording was not evaluated correctly.", manual_only=True),
    Subtype("call_attempt_not_checked", "missed_instruction", "Call Attempt not checked",
            "The call attempt was not checked.", manual_only=True),
    Subtype("incorrect_date_time", "missed_instruction", "Incorrect Date and Time",
            "A date or time that is wrong, differs from the RAW report, or does not agree with the rest of the report."),
    # ------------------------------------------------------------------ Contradiction (20)
    Subtype("comment_marking_mismatch", "contradiction", "Comment and Marking do not match",
            "A comment that contradicts the Yes/No (or other) marking it belongs to."),
    Subtype("meaning_changed", "contradiction", "Original sentences changed which changed the meaning",
            "The editor changed, added or removed words so that the meaning of the shopper's original sentence is different."),
    Subtype("incorrect_option_picture", "contradiction", "Incorrect option selected as per picture",
            "The selected option does not agree with the picture.", manual_only=True),
    # ------------------------------------------------------------------ Wrong Location / Put on Different category (25)
    Subtype("shop_performed_incorrectly", "wrong_location", "Shop was performed incorrectly",
            "The shop was not carried out as the instructions require.", manual_only=True),
    Subtype("closed_treated_open", "wrong_location", "Closed location treated as Open",
            "The location was closed, but the shop / report treats it as open."),
    Subtype("status_incorrect", "wrong_location", "Status Incorrect selected",
            "The status selected for the shop is not the correct one."),
]

CATEGORY_BY_KEY = {c.key: c for c in CATEGORIES}
SUBTYPE_BY_KEY = {s.key: s for s in SUBTYPES}

# --------------------------------------------------------------------------------------------------------------
# Which existing rule belongs to which sub-type. Rules not listed here (and not a template profile) are NOT part of
# the fixed QC rules and are never run. Client rules created later must pick a sub-type from the list above.
RULE_SUBTYPE: dict[str, str] = {
    # built-in checks
    "GEN-SPELL": "spelling",
    "GEN-PUNCT": "extra_space",
    "GEN-NOSPACE": "extra_space",
    "GEN-DOUBLEPUNCT": "punctuation",
    "GEN-CAPS": "punctuation",
    "GEN-PROFANITY": "profanity",
    "GEN-NUMBERS": "meaning_changed",       # a time/date difference is re-classified to incorrect_date_time by the check
    "GEN-NEGATION": "meaning_changed",
    "GEN-OMISSION": "meaning_changed",
    "GEN-ADDITION": "meaning_changed",
    "GEN-AI-MEANING": "meaning_changed",
    # client rules imported from the Drive checklists
    "APC-AVOID-PHRASES": "terminology",
    "CXE-ASSOCIATE-TERM": "terminology",
    "CXE-NO-MYSTERY-TERMS": "terminology",
    "APC-PAST-TENSE": "past_tense",
    "APC-NO-IDENTITY-OR-OFFENSIVE": "profanity",
    "APC-NEGATIVE-NEEDS-COMMENT": "negative_not_answered",
    "CXE-NEGATIVE-SUPPORT": "negative_not_answered",
    "APC-MARKING-COMMENT-CONSISTENCY": "comment_marking_mismatch",
    "CXE-AGE-STYLE": "punctuation",
    "CXE-AM-PM-STYLE": "punctuation",
    "CXE-QUOTE-PUNCTUATION": "punctuation",
    "CXE-QUOTE-COMMA": "punctuation",
    "CXE-QUOTE-CAPITAL": "punctuation",
    # sample demo rule (development data only)
    "DEMO-RETAIL-TONE": "terminology",
}


def subtype(key: str | None) -> Subtype | None:
    return SUBTYPE_BY_KEY.get(key or "")


def category_of(sub_key: str | None) -> Category | None:
    s = subtype(sub_key)
    return CATEGORY_BY_KEY[s.category] if s else None


def is_valid(sub_key: str | None) -> bool:
    return bool(sub_key) and sub_key in SUBTYPE_BY_KEY


def label(sub_key: str | None) -> str:
    s = subtype(sub_key)
    return s.label if s else (sub_key or "")


def category_label(cat_key: str | None) -> str:
    c = CATEGORY_BY_KEY.get(cat_key or "")
    return c.label if c else (cat_key or "")


def ai_checklist() -> list[dict]:
    """The sub-types the AI may report (never the ones that need video / audio / pictures)."""
    return [{"subtype": s.key, "category": CATEGORY_BY_KEY[s.category].label, "name": s.label, "look_for": s.hint}
            for s in SUBTYPES if not s.manual_only]


def catalog() -> dict:
    """The sheet as data, for the UI."""
    return {
        "total_score": TOTAL_SCORE,
        "categories": [{"key": c.key, "label": c.label, "points": c.points, "score_after": TOTAL_SCORE - c.points, "severity": c.severity,
                        "subtypes": [{"key": s.key, "label": s.label, "hint": s.hint, "manual_only": s.manual_only}
                                     for s in SUBTYPES if s.category == c.key]} for c in CATEGORIES],
    }


def score(confirmed_subtypes: list[str | None]) -> dict:
    """Score of one form: 100 minus the marks of every category that has at least one confirmed error."""
    cats = {category_of(k).key for k in confirmed_subtypes if category_of(k)}
    lost = {key: CATEGORY_BY_KEY[key].points for key in cats}
    return {"total": TOTAL_SCORE, "score": max(0, TOTAL_SCORE - sum(lost.values())), "deductions": [
        {"category": key, "label": CATEGORY_BY_KEY[key].label, "points": pts} for key, pts in sorted(lost.items(), key=lambda kv: -kv[1])]}
