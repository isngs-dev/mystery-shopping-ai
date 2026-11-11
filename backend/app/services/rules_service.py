"""Rule library: baseline rules, applicability, versioning and the approval workflow."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import utcnow
from ..enums import RuleStatus
from ..models import Form, QCRule, RuleVersion, User
from . import error_taxonomy as tx

# The general QC rules. Each one belongs to exactly one sub-type of the fixed error table (error_taxonomy.RULE_SUBTYPE);
# the category stored on the rule is the table's category.
BASELINE_RULES = [
    ("GEN-SPELL", "Spelling", "spelling", "minor", "builtin", {"builtin": "spelling", "allowed_terms": []},
     "Words must be spelled correctly. Add brand names or project terms to the allowed list in a project rule."),
    ("GEN-PUNCT", "No space before punctuation", "extra_space", "minor", "builtin", {"builtin": "punctuation_spacing"},
     "No space is allowed before commas, full stops, colons or semicolons."),
    ("GEN-NOSPACE", "Space after punctuation", "extra_space", "minor", "builtin", {"builtin": "missing_space"},
     "A comma, full stop, colon or semicolon must be followed by a space."),
    ("GEN-DOUBLEPUNCT", "No doubled punctuation", "punctuation", "minor", "builtin", {"builtin": "double_punctuation"},
     "The same punctuation mark must not be repeated (“..”, “,,”, “!!”)."),
    ("GEN-CAPS", "Capital letters", "punctuation", "minor", "builtin", {"builtin": "capitalization"},
     "Sentences start with a capital letter and the pronoun I is capitalised."),
    ("GEN-PROFANITY", "No profanity", "profanity", "critical", "builtin", {"builtin": "profanity"},
     "Foul or offensive language must not appear in the report."),
    ("GEN-NUMBERS", "Figures, dates and times must match the RAW report", "meaning_changed", "critical", "builtin",
     {"builtin": "numbers"}, "Editors must not change numeric values, times, dates or amounts reported by the shopper. "
                            "A changed date or time is reported as Incorrect Date and Time."),
    ("GEN-NEGATION", "Negation must not change", "meaning_changed", "critical", "builtin", {"builtin": "negation"},
     "Adding or removing words such as ‘not’, ‘no’, ‘never’ can reverse meaning and must be justified."),
    ("GEN-OMISSION", "No unexplained removal of RAW content", "meaning_changed", "critical", "builtin",
     {"builtin": "omission", "min_words": 6}, "Substantive shopper observations must not be deleted during editing."),
    ("GEN-ADDITION", "No unsupported added content", "meaning_changed", "critical", "builtin",
     {"builtin": "addition", "min_words": 8}, "Content added by the editor must be supported by the shopper’s RAW data."),
    ("GEN-AI-MEANING", "Edits must preserve the shopper’s factual meaning", "meaning_changed", "critical", "ai_guideline", {},
     "Compare each edit with the RAW text. Flag edits that change what the shopper observed, add unsupported claims, "
     "remove facts, or turn objective statements into opinions."),
]


def in_qc(rv: RuleVersion) -> bool:
    """Only rules that belong to the fixed error table are QC rules (page-furniture profiles just hide template text)."""
    return rv.check_type == "template_profile" or tx.is_valid(rv.error_subtype)


def sync_taxonomy(db: Session) -> int:
    """Give every known rule its sub-type and the table's category (idempotent). Rules that are not on the table keep no sub-type
    and therefore never run. Returns the number of rule versions changed."""
    n = 0
    for rv in db.execute(select(RuleVersion)).scalars():
        sub = tx.RULE_SUBTYPE.get(rv.rule.rule_key)
        if not sub:
            if (rv.check_type != "template_profile" and not tx.is_valid(rv.error_subtype)
                    and rv.status in (RuleStatus.PUBLISHED.value, RuleStatus.PENDING_APPROVAL.value, RuleStatus.DRAFT.value)):
                rv.status = RuleStatus.RETIRED.value          # not on the QC error table: it would never run, so it leaves the library
                rv.change_note = ((rv.change_note or "") + " · Retired: not part of the fixed QC error table.").strip(" ·")
                n += 1
            continue
        cat = tx.category_of(sub).key
        if rv.error_subtype != sub or rv.category != cat:
            rv.error_subtype, rv.category = sub, cat
            n += 1
    if n:
        db.commit()
    return n


def applicable(rv: RuleVersion, form: Form) -> bool:
    proj = form.project
    return ((rv.client_id is None or rv.client_id == proj.client_id)
            and (rv.project_id is None or rv.project_id == proj.id)
            and (rv.form_type is None or rv.form_type == form.form_type))


def published_versions(db: Session, on: date | None = None) -> list[RuleVersion]:
    on = on or date.today()
    rows = db.execute(select(RuleVersion).where(RuleVersion.status == RuleStatus.PUBLISHED.value,
                                                RuleVersion.effective_date <= on)
                      .order_by(RuleVersion.rule_id, RuleVersion.version)).scalars().all()
    latest: dict[int, RuleVersion] = {}
    for rv in rows:
        latest[rv.rule_id] = rv  # ascending order => last wins
    return list(latest.values())


def rules_for_form(db: Session, form: Form) -> list[RuleVersion]:
    return [rv for rv in published_versions(db) if in_qc(rv) and applicable(rv, form)]


def scope_level(rv: RuleVersion) -> str:
    if rv.project_id:
        return "project"
    if rv.client_id:
        return "client"
    if rv.form_type:
        return "form"
    return "general"


def create_rule(db: Session, user: User | None, *, rule_key: str, published: bool = False, **fields) -> RuleVersion:
    rule = db.execute(select(QCRule).where(QCRule.rule_key == rule_key)).scalar_one_or_none()
    if rule is None:
        rule = QCRule(rule_key=rule_key)
        db.add(rule)
        db.flush()
    rv = RuleVersion(rule_id=rule.id, version=1, created_by=user.id if user else None,
                     owner_id=fields.pop("owner_id", user.id if user else None), **fields)
    rv.rule = rule
    if published:
        rv.status = RuleStatus.PUBLISHED.value
        rv.approved_at = utcnow()
        rv.change_note = rv.change_note or "Baseline rule"
    db.add(rv)
    db.flush()
    return rv


def seed_baseline_rules(db: Session) -> int:
    n = 0
    for key, name, category, severity, check_type, config, desc in BASELINE_RULES:
        if db.execute(select(QCRule).where(QCRule.rule_key == key)).scalar_one_or_none():
            continue
        create_rule(db, None, rule_key=key, published=True, name=name, category=tx.category_of(category).key, error_subtype=category,
                    severity=severity, check_type=check_type, config=config, description=desc, change_note="Baseline rule (system seed)")
        n += 1
    db.commit()
    return n


def new_version_from(db: Session, user: User, base: RuleVersion, changes: dict, change_note: str) -> RuleVersion:
    top = max(v.version for v in base.rule.versions)
    data = {c: getattr(base, c) for c in ("name", "description", "category", "error_subtype", "severity", "client_id", "project_id",
                                          "form_type", "check_type", "config", "examples", "exceptions", "effective_date", "owner_id")}
    data.update({k: v for k, v in changes.items() if k in data})
    rv = RuleVersion(rule_id=base.rule_id, version=top + 1, status=RuleStatus.DRAFT.value, created_by=user.id,
                     change_note=change_note, **data)
    db.add(rv)
    db.flush()
    audit(db, user, "rule.version_created", entity_type="rule_version", entity_id=rv.id,
          detail={"rule_key": base.rule.rule_key, "version": rv.version, "note": change_note})
    return rv
