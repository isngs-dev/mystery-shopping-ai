"""QC rules distilled from the client documents in the Drive folder.

Sources (Drive folder "Mystery Shopping_QC"):
  APC_Checklist.docx · Valley Drive Up QC Instructions.docx   -> client APC
  CXE_Checklist.docx · HOU guidelines.pdf (CXE narrative style) -> client CXE

Rules are created as DRAFTS and immediately SUBMITTED for approval: they only affect QC runs once an authorised
approver (different from the importer) publishes them. Deterministic checks need no AI; "ai_guideline" rules are
evaluated only when an approved AI provider is configured and are reported as "not evaluated" otherwise.

Credentials that appear in the source documents (platform logins) are deliberately NOT copied anywhere.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import utcnow
from ..enums import RuleStatus
from ..models import Client, QCRule
from . import error_taxonomy as tx
from .rules_service import create_rule

# Lines of a printed survey-platform review page that are page furniture / reviewer panel, not the shopper's narrative.
PLATFORM_IGNORE = [
    r"^\d{1,2}/\d{1,2}/\d{2,4},? \d{1,2}:\d{2} ?[ap]m", r"admin shop review", r"https?://|www\.", r"shopper #|shopper phone|shop ref#",
    r"^(from|to|reply-to):", r"^\d{1,2}:\d{2}$", r"^current font size",
    r"overall score|^\d+%:|branch avg|survey avg|n/a answers are omitted|click here if manager",
    r"gps verifier|rulez|show questions hidden|spell check|download guidelines|private review comments",
    r"reviewer controls|shop status|review history|reviewer status|event log|last event|shopper grade|shopper pay|contact shopper|email shopper|"
    r"add private comment|add a shopper citation|email this shop|send shop as link|send score|send email to|send pdf|shop process options|"
    r"shop set up|update survey|view shopper|dump mode|send comments to shopper|\(email address",
    r"^\d{1,2}-\d{1,2}-\d{4} \d{1,2}:\d{2}",
]
CONTRACTION = r"\b(?:[A-Za-z]+n['’]t|(?:I|you|we|they|he|she|it|that|there|here|what|who|let)['’](?:m|re|ve|ll|d|s))\b"

PACK: list[dict] = [
    # ------------------------------------------------------------------------------------------- APC
    dict(key="APC-TEMPLATE", client="APC", source="APC_Checklist.docx", name="Survey-platform review page: ignore page furniture", category="template",
         severity="info", check_type="template_profile", config={"ignore_line_patterns": PLATFORM_IGNORE},
         description="Printed review pages contain headers, URLs, shopper contact details, reviewer controls and event logs. These lines are never checked or edited."),
    dict(key="APC-NO-CONTRACTIONS", client="APC", source="APC_Checklist.docx (Editing step 6)", name="No contractions in comments", category="style", severity="minor",
         check_type="regex_forbidden", config={"pattern": CONTRACTION, "message": "Contractions must be written in full form (e.g. “did not”, “I am”).",
                                               "suggestion": "Write the words in full."},
         description="While checking the comment, ensure all words are written in their full form.", examples="Wrong: didn't, she's. Right: did not, she is."),
    dict(key="APC-NO-AMOUNTS", client="APC", source="APC_Checklist.docx (Valley Drive-up and Teller)", name="No “$” sign or amounts in comments", category="content_policy",
         severity="major", check_type="regex_forbidden",
         config={"pattern": r"\$\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)? dollars?\b", "message": "Remove sentences that contain a “$” sign or any amount.",
                 "suggestion": "Remove the sentence that mentions the amount."},
         description="Remove those sentences that contain the “$” sign or any amount mentioned."),
    dict(key="APC-AVOID-PHRASES", client="APC", source="APC_Checklist.docx (Valley Drive-up and Teller)", name="Phrases that must not appear", category="content_policy",
         severity="major", check_type="regex_forbidden",
         config={"pattern": r"\b(?:today|tonight|weekend)\b|\bi asked (?:them|him|her) for (?:their|his|her) name\b|\bhello,? (?:mr|mrs|ms|miss)\b",
                 "message": "“today”, “tonight”, “weekend”, “Hello, Mr./Ms. …” and “I asked them for their name” must not be in the report.",
                 "suggestion": "Rephrase (e.g. “that day”) or remove."},
         description="Look out for “I asked them for their name”, “today”, “tonight”, “Hello, Mr. xyz”, “Weekend”. They should not be there."),
    dict(key="APC-SHOPPER-ALONE", client="APC", source="APC_Checklist.docx", name="Shopper must perform the shop alone", category="instruction_violation", severity="major",
         check_type="ai_guideline", config={}, description="The shopper must perform the shop alone and cannot take someone. Flag any mention of a companion (e.g. “my husband and I”, “we”, “my friend”)."),
    dict(key="APC-PAST-TENSE", client="APC", source="APC_Checklist.docx", name="Comments in the past tense, complete sentences", category="grammar", severity="minor",
         check_type="ai_guideline", config={}, description="All sentences must be in the past tense and complete; unnecessary sentences are removed; commentary is never copied and pasted from section to section."),
    dict(key="APC-NO-IDENTITY-OR-OFFENSIVE", client="APC", source="APC_Checklist.docx · Valley Drive Up QC Instructions", name="No foul language, racial comments or shopper identity",
         category="content_policy", severity="critical", check_type="ai_guideline", config={},
         description="Remove racial comments from employee descriptions. No foul language. The identity of the shopper must not be revealed; keep small-talk explanations generic so they cannot identify the shopper."),
    dict(key="APC-NEGATIVE-NEEDS-COMMENT", client="APC", source="APC_Checklist.docx · Valley Drive Up QC Instructions", name="Negative markings need supporting comments", category="completeness",
         severity="major", check_type="ai_guideline", config={},
         description="Every NO/negative marking needs a clarifying comment. Wait time beyond 3 minutes needs a supporting comment. For Teller, if the greeting took longer than 10 seconds a comment with the exact time is required."),
    dict(key="APC-MARKING-COMMENT-CONSISTENCY", client="APC", source="Valley Drive Up QC Instructions (1).docx", name="Comments must agree with the Yes/No markings", category="meaning_change",
         severity="major", check_type="ai_guideline", config={},
         description="Commentary must not contradict the yes/no questions (e.g. if the teller smiled the smile question must be YES). If any casual-conversation option other than “None of the above” is checked, the small-talk question must be YES with a brief explanation. A NO for “understand the banker through the voice-box” needs supporting commentary."),
    dict(key="APC-BANKER-DETAILS", client="APC", source="Valley Drive Up QC Instructions (1).docx", name="Banker name and counts", category="completeness", severity="minor",
         check_type="ai_guideline", config={}, description="Banker name must be first and last name, otherwise the shop goes to HOLD B. Number of bankers may be “unable to determine” but never zero. Service wait time is the wait to be assisted, not the whole transaction."),
    # ------------------------------------------------------------------------------------------- CXE
    dict(key="CXE-TEMPLATE", client="CXE", source="CXE_Checklist.docx", name="Survey-platform review page: ignore page furniture", category="template", severity="info",
         check_type="template_profile", config={"ignore_line_patterns": PLATFORM_IGNORE},
         description="Printed review pages contain headers, URLs, shopper contact details, reviewer controls and event logs. These lines are never checked or edited."),
    dict(key="CXE-ASSOCIATE-TERM", client="CXE", source="CXE_Checklist.docx · HOU guidelines.pdf", name="Say “associate”, not “employee”", category="style", severity="minor",
         check_type="forbidden_terms", config={"terms": ["employee", "employees"], "suggestion": "Use “associate”."},
         description="The word employee needs to be changed to ‘associate’."),
    dict(key="CXE-NO-MYSTERY-TERMS", client="CXE", source="HOU guidelines.pdf (Security)", name="Do not use “mystery shop” / “secret shopper”", category="content_policy", severity="major",
         check_type="forbidden_terms", config={"terms": ["mystery shop", "mystery shopper", "mystery shopping", "secret shopper"], "suggestion": "Say “evaluation” or “audit”."},
         description="Do not use the terms “mystery shops” or “secret shopper”."),
    dict(key="CXE-AGE-STYLE", client="CXE", source="HOU guidelines.pdf (Narrative style)", name="Ages: “in her 20s” (no apostrophe)", category="style", severity="minor",
         check_type="regex_forbidden", config={"pattern": r"\b\d0['’]s\b", "message": "Write ages as numbers without an apostrophe (e.g. “in her 20s”).", "suggestion": "Remove the apostrophe."},
         description="Report age as: The associate had blonde hair and was in her 20s (numbers and no apostrophe)."),
    dict(key="CXE-AM-PM-STYLE", client="CXE", source="HOU guidelines.pdf (Narrative style)", name="“AM” / “PM” upper case, no periods", category="style", severity="minor",
         check_type="regex_forbidden", config={"case_sensitive": True, "pattern": r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm|a\.m\.|p\.m\.|A\.M\.|P\.M\.|Am|Pm)(?![A-Za-z])",
                                               "message": "Write “AM” or “PM” in upper case without periods.", "suggestion": "Use e.g. “10:30 AM”."},
         description="Please refer to “AM” or “PM” (upper case, no periods)."),
    dict(key="CXE-QUOTE-PUNCTUATION", client="CXE", source="HOU guidelines.pdf (Narrative style)", name="Quotes: ending punctuation inside the quotation marks", category="style", severity="minor",
         check_type="regex_forbidden", config={"pattern": r"(?<=[A-Za-z0-9])[\"”][.,]", "message": "Ending punctuation goes inside the quotation marks.", "suggestion": "Move the comma/period inside the quote."},
         description="Use of quotations: He said, “Hello,” then smiled and said, “How may I help you?” — all ending punctuation goes inside the quotation marks."),
    dict(key="CXE-QUOTE-COMMA", client="CXE", source="HOU guidelines.pdf (Narrative style)", name="Quotes: comma before the quote", category="style", severity="minor",
         check_type="regex_forbidden", config={"pattern": r"\b(?:said|asked|replied|responded|told me|stated|answered)\s+[\"“]", "message": "Use a comma before a quote (e.g. He said, “Hello”).",
                                               "suggestion": "Add a comma before the opening quotation mark."},
         description="Use a comma before the quote."),
    dict(key="CXE-QUOTE-CAPITAL", client="CXE", source="HOU guidelines.pdf (Narrative style)", name="Quotes: capitalise the beginning of the quote", category="style", severity="minor",
         check_type="regex_forbidden", config={"case_sensitive": True, "pattern": r"(?:^|\s)[\"“][a-z]", "message": "Capitalise the first word inside a quotation.", "suggestion": "Capitalise the first letter of the quote."},
         description="Capitalize the beginning of a quote."),
    dict(key="CXE-BNA-MINUTE-SUITE", client="CXE", source="CXE_Checklist.docx", name="BNA Minute Suite: never finalize, hold for client check", category="instruction_violation",
         severity="info", check_type="forbidden_terms", config={"terms": ["minute suite"], "suggestion": "Place the shop on Hold for client check; never finalize it."},
         description="Never finalize the BNA Minute Suite shop. It needs to be put on Hold for client check."),
    dict(key="CXE-PRODUCT-QUESTION", client="CXE", source="CXE_Checklist.docx · HOU guidelines.pdf", name="Open-ended product question asked (concessions)", category="completeness", severity="major",
         check_type="ai_guideline", config={}, description="For concession evaluations the shopper must ask an open-ended product-related question that cannot be answered with yes/no or a single word; without it the report cannot be accepted."),
    dict(key="CXE-ASSOCIATE-IDENTIFIED", client="CXE", source="HOU guidelines.pdf", name="Associate name or detailed description", category="completeness", severity="major",
         check_type="ai_guideline", config={}, description="Accepted concession deliverables include the associate's name; if impossible, a detailed description (hair, tattoos, piercings, glasses, outfit). Never describe an associate by race or weight."),
    dict(key="CXE-NEGATIVE-SUPPORT", client="CXE", source="CXE_Checklist.docx · HOU guidelines.pdf", name="Negative / N/A answers have supporting comments", category="completeness", severity="major",
         check_type="ai_guideline", config={}, description="All negative markings or questions marked N/A must have a supporting comment; there is a comment on every question and a brief synopsis. Verify date/time/receipt details against the report."),
    dict(key="CXE-NARRATIVE-STYLE", client="CXE", source="HOU guidelines.pdf", name="Narrative in paragraphs, objective, no alcohol mention", category="style", severity="minor",
         check_type="ai_guideline", config={}, description="Comments are written in paragraph form. Do not mention alcohol in the submitted form (unless it is the only item available, then call it a “beverage”/“drink”)."),
]


def apply_pack(db: Session, user) -> dict:
    """Create the pack's rules for clients that exist; submit them for approval. Idempotent by rule key."""
    submitted = existing = skipped = 0
    clients = {c.name: c for c in db.execute(select(Client)).scalars()}
    for r in PACK:
        client = clients.get(r["client"])
        if client is None or (r["check_type"] != "template_profile" and r["key"] not in tx.RULE_SUBTYPE):
            skipped += 1            # QC only uses the fixed error table: checklist items outside it are not imported
            continue
        if db.execute(select(QCRule).where(QCRule.rule_key == r["key"])).scalar_one_or_none():
            existing += 1
            continue
        sub = tx.RULE_SUBTYPE.get(r["key"])      # rules that are not on the fixed error table are imported without a sub-type and never run
        rv = create_rule(db, user, rule_key=r["key"], name=r["name"], description=r["description"],
                         category=tx.category_of(sub).key if sub else r["category"], error_subtype=sub, severity=r["severity"],
                         client_id=client.id, check_type=r["check_type"], config=r["config"], examples=r.get("examples"),
                         effective_date=date.today(), change_note=f"Imported from Drive document: {r['source']}")
        rv.status, rv.submitted_at = RuleStatus.PENDING_APPROVAL.value, utcnow()
        audit(db, user, "rule.submitted", entity_type="rule_version", entity_id=rv.id, detail={"rule_key": r["key"], "version": 1, "source": r["source"]})
        submitted += 1
    db.commit()
    return {"submitted": submitted, "existing": existing, "skipped": skipped}
