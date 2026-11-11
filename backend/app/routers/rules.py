"""QC rule library: versioned rules with maker-checker approval before anything reaches production."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db, utcnow
from ..deps import needs
from ..enums import RuleStatus
from ..models import Client, Project, QCRule, RuleVersion, User
from ..serializers import rule_out
from ..services import error_taxonomy as tx, rules_service

router = APIRouter(prefix="/api/rules", tags=["rules"])
CHECK_TYPES = ("builtin", "forbidden_terms", "regex_forbidden", "required_labels", "max_sentence_words", "ai_guideline", "template_profile")
SEVERITIES = ("critical", "major", "minor", "info")


class RuleIn(BaseModel):
    rule_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9\-]{3,64}$")
    name: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=4000)
    error_subtype: str = Field(max_length=40)      # one entry of the fixed QC error table
    severity: str = "minor"
    client_id: int | None = None
    project_id: int | None = None
    form_type: str | None = None
    check_type: str = "ai_guideline"
    config: dict = {}
    examples: str | None = None
    exceptions: str | None = None
    effective_date: date | None = None
    change_note: str | None = None


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = None
    error_subtype: str | None = None
    severity: str | None = None
    client_id: int | None = None
    project_id: int | None = None
    form_type: str | None = None
    check_type: str | None = None
    config: dict | None = None
    examples: str | None = None
    exceptions: str | None = None
    effective_date: date | None = None
    change_note: str = Field(min_length=3, max_length=1000)


def _validate(body, db: Session) -> None:
    sub = getattr(body, "error_subtype", None)
    if sub is not None and not tx.is_valid(sub):
        raise HTTPException(422, "A QC rule must belong to one error of the fixed QC error table.")
    ct = getattr(body, "check_type", None)
    if ct and ct not in CHECK_TYPES:
        raise HTTPException(422, f"check_type must be one of {', '.join(CHECK_TYPES)}.")
    sev = getattr(body, "severity", None)
    if sev and sev not in SEVERITIES:
        raise HTTPException(422, "Invalid severity.")
    if getattr(body, "client_id", None) and not db.get(Client, body.client_id):
        raise HTTPException(422, "Unknown client.")
    if getattr(body, "project_id", None) and not db.get(Project, body.project_id):
        raise HTTPException(422, "Unknown project.")


def _owner_map(db: Session) -> dict[int, User]:
    return {u.id: u for u in db.execute(select(User)).scalars()}


@router.get("")
def list_rules(status: str | None = None, only_latest: bool = True, user: User = Depends(needs("rules.view")), db: Session = Depends(get_db)):
    rows = db.execute(select(RuleVersion).order_by(RuleVersion.rule_id, RuleVersion.version)).scalars().all()
    owners = _owner_map(db)
    if only_latest:
        latest: dict[int, RuleVersion] = {}
        for rv in rows:
            latest[rv.rule_id] = rv
        rows = list(latest.values())
    if status:
        rows = [r for r in rows if r.status == status]
    published = {rv.rule_id: rv.version for rv in rules_service.published_versions(db)}
    out = []
    for rv in rows:
        item = rule_out(rv, owners.get(rv.owner_id))
        item["active_version"] = published.get(rv.rule_id)
        out.append(item)
    return out


@router.get("/{rule_id}/history")
def history(rule_id: int, user: User = Depends(needs("rules.view")), db: Session = Depends(get_db)):
    rule = db.get(QCRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found.")
    owners = _owner_map(db)
    return [{**rule_out(v, owners.get(v.owner_id)), "created_by_name": owners[v.created_by].name if v.created_by in owners else "system",
             "approved_by_name": owners[v.approved_by].name if v.approved_by in owners else None} for v in rule.versions]


@router.post("", status_code=201)
def create(body: RuleIn, request: Request, user: User = Depends(needs("rules.author")), db: Session = Depends(get_db)):
    _validate(body, db)
    key = (body.rule_key or "RULE-" + utcnow().strftime("%y%m%d%H%M%S")).upper()
    if db.execute(select(QCRule).where(QCRule.rule_key == key)).scalar_one_or_none():
        raise HTTPException(409, "A rule with this key already exists.")
    rv = rules_service.create_rule(
        db, user, rule_key=key, name=body.name.strip(), description=body.description, category=tx.category_of(body.error_subtype).key,
        error_subtype=body.error_subtype, severity=body.severity,
        client_id=body.client_id, project_id=body.project_id, form_type=body.form_type, check_type=body.check_type,
        config=body.config, examples=body.examples, exceptions=body.exceptions,
        effective_date=body.effective_date or date.today(), change_note=body.change_note or "Initial version")
    audit(db, user, "rule.created", entity_type="rule_version", entity_id=rv.id, detail={"rule_key": key}, request=request)
    db.commit()
    return rule_out(rv, user)


@router.post("/{rule_id}/versions", status_code=201)
def new_version(rule_id: int, body: RuleUpdate, request: Request, user: User = Depends(needs("rules.author")), db: Session = Depends(get_db)):
    rule = db.get(QCRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found.")
    if any(v.status in (RuleStatus.DRAFT.value, RuleStatus.PENDING_APPROVAL.value) for v in rule.versions):
        raise HTTPException(409, "This rule already has an unpublished draft/pending version; finish or reject it first.")
    _validate(body, db)
    base = next((v for v in reversed(rule.versions) if v.status == RuleStatus.PUBLISHED.value), rule.versions[-1])
    changes = body.model_dump(exclude={"change_note"}, exclude_unset=True)
    if changes.get("error_subtype"):
        changes["category"] = tx.category_of(changes["error_subtype"]).key
    rv = rules_service.new_version_from(db, user, base, changes, body.change_note)
    db.commit()
    return rule_out(rv, user)


@router.patch("/versions/{version_id}")
def edit_draft(version_id: int, body: RuleUpdate, request: Request, user: User = Depends(needs("rules.author")), db: Session = Depends(get_db)):
    rv = db.get(RuleVersion, version_id)
    if not rv:
        raise HTTPException(404, "Rule version not found.")
    if rv.status != RuleStatus.DRAFT.value:
        raise HTTPException(409, "Only draft versions can be edited; published versions are immutable.")
    _validate(body, db)
    for k, v in body.model_dump(exclude={"change_note"}, exclude_unset=True).items():
        setattr(rv, k, v)
        if k == "error_subtype" and v:
            rv.category = tx.category_of(v).key
    rv.change_note = body.change_note
    audit(db, user, "rule.draft_edited", entity_type="rule_version", entity_id=rv.id, request=request)
    db.commit()
    return rule_out(rv)


@router.post("/versions/{version_id}/submit")
def submit(version_id: int, request: Request, user: User = Depends(needs("rules.author")), db: Session = Depends(get_db)):
    rv = db.get(RuleVersion, version_id)
    if not rv or rv.status != RuleStatus.DRAFT.value:
        raise HTTPException(409, "Only draft versions can be submitted for approval.")
    rv.status, rv.submitted_at = RuleStatus.PENDING_APPROVAL.value, utcnow()
    audit(db, user, "rule.submitted", entity_type="rule_version", entity_id=rv.id, detail={"rule_key": rv.rule.rule_key, "version": rv.version}, request=request)
    db.commit()
    return rule_out(rv)


class DecisionIn(BaseModel):
    approve: bool
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/versions/{version_id}/decision")
def approve(version_id: int, body: DecisionIn, request: Request, user: User = Depends(needs("rules.approve")), db: Session = Depends(get_db)):
    rv = db.get(RuleVersion, version_id)
    if not rv or rv.status != RuleStatus.PENDING_APPROVAL.value:
        raise HTTPException(409, "This version is not awaiting approval.")
    if rv.created_by == user.id:
        raise HTTPException(403, "Separation of duties: the author of a rule version cannot approve it.")
    if body.approve:
        for old in rv.rule.versions:
            if old.status == RuleStatus.PUBLISHED.value:
                old.status = RuleStatus.RETIRED.value
        rv.status, rv.approved_by, rv.approved_at = RuleStatus.PUBLISHED.value, user.id, utcnow()
    else:
        if not (body.reason or "").strip():
            raise HTTPException(422, "A rejection reason is required.")
        rv.status, rv.rejection_reason = RuleStatus.REJECTED.value, body.reason
    audit(db, user, "rule.approved" if body.approve else "rule.rejected", entity_type="rule_version", entity_id=rv.id,
          detail={"rule_key": rv.rule.rule_key, "version": rv.version, "reason": body.reason}, request=request)
    db.commit()
    return rule_out(rv)


class BulkDecisionIn(BaseModel):
    version_ids: list[int] = Field(min_length=1, max_length=100)
    approve: bool
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/bulk-decision")
def bulk_decision(body: BulkDecisionIn, request: Request, user: User = Depends(needs("rules.approve")), db: Session = Depends(get_db)):
    """Approve (publish) or reject several pending rule versions. Each is still checked individually: the author of a
    version can never approve it, and only versions awaiting approval are touched."""
    if not body.approve and not (body.reason or "").strip():
        raise HTTPException(422, "A rejection reason is required.")
    done, refused = [], []
    for vid in body.version_ids:
        rv = db.get(RuleVersion, vid)
        if not rv or rv.status != RuleStatus.PENDING_APPROVAL.value:
            refused.append({"id": vid, "reason": "not awaiting approval"})
            continue
        if rv.created_by == user.id:
            refused.append({"id": vid, "rule_key": rv.rule.rule_key, "reason": "you authored this version (separation of duties)"})
            continue
        if body.approve:
            for old in rv.rule.versions:
                if old.status == RuleStatus.PUBLISHED.value:
                    old.status = RuleStatus.RETIRED.value
            rv.status, rv.approved_by, rv.approved_at = RuleStatus.PUBLISHED.value, user.id, utcnow()
        else:
            rv.status, rv.rejection_reason = RuleStatus.REJECTED.value, body.reason
        audit(db, user, "rule.approved" if body.approve else "rule.rejected", entity_type="rule_version", entity_id=rv.id,
              detail={"rule_key": rv.rule.rule_key, "version": rv.version, "bulk": True, "reason": body.reason}, request=request)
        done.append(rv.rule.rule_key)
    db.commit()
    return {"done": done, "refused": refused}
