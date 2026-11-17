"""API of the Chrome extension (see services/ext_assist.py). Authenticated with a bearer token from POST /api/ext/login."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user, needs
from ..models import Form, User
from ..permissions import get_form_or_404, has_perm
from ..security import create_token
from ..services import ext_assist, ext_review
from ..services.ai import resolve_provider
from . import auth as auth_router

router = APIRouter(prefix="/api/ext", tags=["extension"])


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)


def _caps(user: User) -> dict:
    return {"can_confirm": has_perm(user, "docs.ai_confirm"), "can_add_rules": has_perm(user, "ext.rules")}


def _ai_state() -> dict:
    provider, status, msg = resolve_provider()
    return {"usable": provider is not None, "status": status.value, "model": provider.model if provider else None, "message": None if provider else msg}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Same checks as the website login (lock-out, audit); returns a bearer token for the extension instead of setting a cookie."""
    out = auth_router.login(auth_router.LoginIn(email=body.email, password=body.password), request, response, db)
    response.delete_cookie(get_settings().cookie_name, path="/")
    user = db.get(User, out["user"]["id"])
    if not has_perm(user, "docs.ai_edit"):
        raise HTTPException(403, "Your role cannot use the editing extension.")
    return {"token": create_token(user.id, user.token_version), "user": {**out["user"], **_caps(user)}, "ai": _ai_state()}


@router.get("/me")
def me(user: User = Depends(needs("docs.ai_edit"))):
    return {"user": {"id": user.id, "name": user.name, "role": user.role, **_caps(user)}, "ai": _ai_state()}


class IdentifyIn(BaseModel):
    drive_id: str | None = Field(default=None, max_length=200)
    filename: str | None = Field(default=None, max_length=300)


@router.post("/identify")
def identify(body: IdentifyIn, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    if not (body.drive_id or body.filename):
        raise HTTPException(422, "Send the Drive file id or the file name.")
    out = ext_assist.identify(db, user, drive_id=body.drive_id, filename=body.filename)
    if out.get("found"):
        out["review"] = ext_review.review_state(db, db.get(Form, out["form"]["id"]))
    return {**out, **_caps(user), "ai": _ai_state()}


class ProposeIn(BaseModel):
    feedback: str | None = Field(default=None, max_length=3000)
    rejected: list[dict] = Field(default_factory=list, max_length=100)
    proposal_id: int | None = None
    client_id: int | None = None                                           # the human switched the form to another client's rules
    item_feedback: list[dict] = Field(default_factory=list, max_length=60)  # comments on single edits: {id, original, replacement, comment}


def _guard(db: Session, user: User, form_id: int):
    form = get_form_or_404(db, user, form_id)
    return form


def _run(fn):
    try:
        return fn()
    except ext_assist.ExtError as exc:
        raise HTTPException(exc.status, exc.message)


@router.post("/forms/{form_id}/propose")
def propose(form_id: int, body: ProposeIn, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    form = _guard(db, user, form_id)
    return _run(lambda: ext_assist.propose(db, form, user, feedback=body.feedback, rejected=body.rejected, proposal_id=body.proposal_id,
                                                  client_id=body.client_id, item_feedback=body.item_feedback))


class FinalizeIn(BaseModel):
    proposal_id: int
    accepted: list[str] = Field(default_factory=list, max_length=500)
    changes: dict[str, str] = Field(default_factory=dict)                   # the human's own wording per edit id
    comments: dict[str, str] = Field(default_factory=dict)                  # comments typed on single edits (kept for the leader's summary)
    note: str | None = Field(default=None, max_length=1000)


@router.post("/forms/{form_id}/finalize", status_code=202)
def finalize(form_id: int, body: FinalizeIn, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    """The person who cross-checked the AI's edits forwards the form: the edited PDF is created from the accepted edits and QC starts."""
    form = _guard(db, user, form_id)
    return _run(lambda: ext_assist.finalize(db, form, user, proposal_id=body.proposal_id, accepted=body.accepted, changes=body.changes,
                                            comments=body.comments, note=body.note))


@router.get("/forms/{form_id}/proposal")
def open_proposal(form_id: int, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    """The proposal that waits for the leader (or was sent back), so the editor / leader sees the cross-checked edits; null when there is none."""
    form = _guard(db, user, form_id)
    ae = ext_review.current(db, form)
    return {"proposal": ext_review.payload(db, form, ae) if ae else None}


class AskIn(BaseModel):
    edit_id: str = Field(max_length=40)
    comment: str = Field(min_length=2, max_length=1000)


@router.post("/forms/{form_id}/proposals/{proposal_id}/ask")
def ask(form_id: int, proposal_id: int, body: AskIn, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    form = _guard(db, user, form_id)
    return _run(lambda: ext_review.ask_item(db, form, user, proposal_id=proposal_id, edit_id=body.edit_id, comment=body.comment))


class AddIn(BaseModel):
    original: str = Field(min_length=1, max_length=400)
    replacement: str = Field(min_length=1, max_length=400)
    category: str = "grammar"


@router.post("/forms/{form_id}/proposals/{proposal_id}/add")
def add_edit(form_id: int, proposal_id: int, body: AddIn, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    form = _guard(db, user, form_id)
    return _run(lambda: ext_review.add_manual(db, form, user, proposal_id=proposal_id, original=body.original, replacement=body.replacement, category=body.category))


class SubmitIn(BaseModel):
    accepted: list[str] = Field(default_factory=list, max_length=500)
    changes: dict[str, str] = Field(default_factory=dict)
    comments: dict[str, str] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=1000)


@router.post("/forms/{form_id}/proposals/{proposal_id}/submit")
def submit(form_id: int, proposal_id: int, body: SubmitIn, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    """Hand the cross-checked edits to the leader."""
    form = _guard(db, user, form_id)
    return _run(lambda: ext_review.submit_review(db, form, user, proposal_id=proposal_id, accepted=body.accepted, changes=body.changes, comments=body.comments, note=body.note))


class BackIn(BaseModel):
    note: str = Field(min_length=2, max_length=1000)


@router.post("/forms/{form_id}/proposals/{proposal_id}/send-back")
def send_back(form_id: int, proposal_id: int, body: BackIn, user: User = Depends(needs("docs.ai_confirm")), db: Session = Depends(get_db)):
    form = _guard(db, user, form_id)
    return _run(lambda: ext_review.send_back(db, form, user, proposal_id=proposal_id, note=body.note))


@router.get("/review-queue")
def review_queue(user: User = Depends(needs("docs.ai_confirm")), db: Session = Depends(get_db)):
    return {"items": ext_review.queue(db, user)}


@router.get("/forwarded")
def forwarded(user: User = Depends(needs("docs.ai_confirm")), db: Session = Depends(get_db)):
    """Leader: the forms that were cross-checked and forwarded to QC, with what the editor accepted, dismissed, reworded and commented."""
    return {"items": ext_review.forwarded(db, user)}


class RuleIn(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5, max_length=2000)
    error_subtype: str = Field(max_length=40)
    scope: str = Field(default="client", pattern="^(client|project)$")
    examples: str | None = Field(default=None, max_length=1000)
    client_id: int | None = None


@router.post("/forms/{form_id}/rules", status_code=201)
def add_rule(form_id: int, body: RuleIn, user: User = Depends(needs("ext.rules")), db: Session = Depends(get_db)):
    """Leader only: a new grammar rule for the client / project of this form."""
    form = _guard(db, user, form_id)
    return _run(lambda: ext_review.add_rule(db, user, form, name=body.name, description=body.description, error_subtype=body.error_subtype, scope=body.scope,
                                            examples=body.examples, client_id=body.client_id))


@router.get("/forms/{form_id}/status")
def form_status(form_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ext_assist.status(db, get_form_or_404(db, user, form_id))
