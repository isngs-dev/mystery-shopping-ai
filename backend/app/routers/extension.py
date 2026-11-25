"""API of the Chrome extension (see services/ext_assist.py). Authenticated with a bearer token from POST /api/ext/login."""
from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user, needs
from ..models import Form, User
from ..permissions import get_form_or_404, has_perm
from ..security import create_token
from ..services import ext_assist, ext_drive_import, ext_guidelines, ext_review, ext_text, password_reset
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


class ForgotIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class ResetIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    code: str = Field(min_length=4, max_length=20)
    new_password: str = Field(min_length=1, max_length=256)


@router.post("/forgot")
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    """Forgot password: e-mails a one-time code to the account's address (same answer for any e-mail)."""
    return password_reset.request_code(db, body.email, request.client.host if request.client else None)


@router.post("/reset")
def reset(body: ResetIn, db: Session = Depends(get_db)):
    """Sets a new password with the e-mailed code; this also lifts the failed-login lock-out."""
    try:
        return password_reset.confirm(db, body.email, body.code, body.new_password)
    except password_reset.ResetError as exc:
        raise HTTPException(exc.status, exc.message)


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


class AnswerIn(BaseModel):
    id: str = Field(max_length=60)
    question: str = Field(default="", max_length=600)
    text: str = Field(max_length=8000)


class TextProposeIn(BaseModel):
    """A form that is a web page (Intelli-Shop shop review): the questions with the shopper's answers, and the header text that names the client."""
    answers: list[AnswerIn] = Field(max_length=120)
    client_hint: str | None = Field(default=None, max_length=300)
    client_id: int | None = None
    feedback: str | None = Field(default=None, max_length=3000)
    rejected: list[dict] = Field(default_factory=list, max_length=100)
    item_feedback: list[dict] = Field(default_factory=list, max_length=60)
    guidelines: list[str] = Field(default_factory=list, max_length=6)       # fingerprints of the guideline files this page links (their learned rules apply)
    project: str | None = Field(default=None, max_length=200)             # the page header (project / shop type): rules typed for that project apply


class GuidelineIn(BaseModel):
    client_hint: str | None = Field(default=None, max_length=300)
    project: str | None = Field(default=None, max_length=200)             # the page header: the project / shop type
    sha256: str = Field(default="", max_length=64)
    filename: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=1000)
    content_b64: str | None = Field(default=None, max_length=9_000_000)     # sent only when the platform asked for it (a file it has not seen)


@router.post("/guidelines/learn")
def learn_guidelines(body: GuidelineIn, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    """The extension found the client's guideline file on the provider's website. Known file: nothing to do. New file: the AI reads it once and drafts editing rules."""
    content = None
    if body.content_b64:
        try:
            content = base64.b64decode(body.content_b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "The file content is not valid base64.")
        if len(content) > 6_500_000:
            raise HTTPException(413, "The guideline file is too large.")
    return ext_guidelines.learn(db, user, client_hint=body.client_hint, project=body.project, sha256=body.sha256, filename=body.filename,
                                source_url=body.source_url, content=content)


class UploadIn(BaseModel):
    client_name: str | None = Field(default=None, max_length=160)
    client_id: int | None = None
    filename: str = Field(max_length=200)
    content_b64: str = Field(max_length=9_000_000)
    scope: str = Field(default="client", pattern="^(client|project)$")
    project: str | None = Field(default=None, max_length=200)
    create: bool = False                                                   # create the client when its name is new (Managers)
    auto: bool = False                                                     # take the client from the file name when it names one


@router.get("/clients")
def clients_list(user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    """The clients of the platform (for the upload box of the Rules tab)."""
    from sqlalchemy import select as _select

    from ..models import Client

    return {"clients": [{"id": c.id, "name": c.name} for c in db.execute(_select(Client).where(Client.is_active.is_(True)).order_by(Client.name)).scalars()],
            "can_create": has_perm(user, "ext.rules")}


@router.post("/guidelines/upload")
def upload_guidelines(body: UploadIn, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    """A guideline / checklist / rules file (PDF, Word, Excel, text) uploaded for a client: the AI turns it into editing rules that apply to every form of that client."""
    try:
        content = base64.b64decode(body.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "The file content is not valid base64.")
    if len(content) > 6_500_000:
        raise HTTPException(413, "The file is larger than 6 MB.")
    return ext_guidelines.upload(db, user, client_name=body.client_name, client_id=body.client_id, filename=body.filename, content=content, scope=body.scope,
                                 project=body.project, create=body.create, auto=body.auto, can_create=has_perm(user, "ext.rules"))


class DriveImportIn(BaseModel):
    folder: str = Field(min_length=1, max_length=400)                      # a Drive folder link, its ID, or just its name
    mode: str = Field(default="client", pattern="^(client|general)$")


@router.post("/guidelines/drive")
def drive_import_start(body: DriveImportIn, background: BackgroundTasks, user: User = Depends(needs("ext.rules")), db: Session = Depends(get_db)):
    """Manager: import the guideline / checklist files of a Google Drive folder (read with the platform's Drive access) into editing rules, in the background."""
    return ext_drive_import.start(db, user, body.folder, body.mode, background)


@router.get("/guidelines/drive/{job}")
def drive_import_status(job: str, user: User = Depends(needs("ext.rules"))):
    out = ext_drive_import.status(job)
    if out is None:
        raise HTTPException(404, "That import is not known (the server may have restarted). Start it again: files already read are skipped.")
    return out


@router.get("/guidelines/files")
def guideline_files(client_id: int, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    return {"files": ext_guidelines.list_files(db, client_id)}


@router.delete("/guidelines/files/{file_id}")
def guideline_file_remove(file_id: int, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    out = ext_guidelines.remove_file(db, user, file_id, can_manage=has_perm(user, "ext.rules"))
    if out["status"] == "forbidden":
        raise HTTPException(403, out["message"])
    if out["status"] == "not_found":
        raise HTTPException(404, "That file is not there any more.")
    return out


class ManualRulesIn(BaseModel):
    client_hint: str | None = Field(default=None, max_length=300)
    project: str | None = Field(default=None, max_length=200)
    text: str = Field(max_length=12000)
    scope: str = Field(default="project", pattern="^(project|client)$")


@router.post("/guidelines/manual")
def add_manual_rules(body: ManualRulesIn, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    """Rules typed by a person (the Rules tab): one per line, for this project or the whole client. Drafts, used for editing at once."""
    return ext_guidelines.add_manual(db, user, client_hint=body.client_hint, project=body.project, text=body.text, scope=body.scope)


class NoteIn(BaseModel):
    client_hint: str | None = Field(default=None, max_length=300)
    project: str | None = Field(default=None, max_length=200)
    job: str | None = Field(default=None, max_length=40)
    question: str | None = Field(default=None, max_length=600)
    original: str = Field(max_length=600)
    replacement: str = Field(default="", max_length=600)
    decision: str = Field(default="open", max_length=20)
    text: str = Field(max_length=2000)


@router.post("/text/note")
def edit_note(body: NoteIn, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    """The person's own point of view on one suggested edit; kept in the audit trail."""
    return ext_guidelines.note(db, user, client_hint=body.client_hint, project=body.project, job=body.job, question=body.question, original=body.original,
                               replacement=body.replacement, decision=body.decision, text=body.text)


@router.post("/text/propose")
def propose_text(body: TextProposeIn, user: User = Depends(needs("docs.ai_edit")), db: Session = Depends(get_db)):
    """Edits for the answers of a web-page form. Stateless: nothing is stored or written; the page shows the edits and the person decides."""
    if sum(len(a.text) for a in body.answers) > 90000:
        raise HTTPException(413, "This form has too much text for one check.")
    return ext_text.propose_text(db, answers=[a.model_dump() for a in body.answers], client_hint=body.client_hint, client_id=body.client_id,
                                 feedback=body.feedback, rejected=body.rejected, item_feedback=body.item_feedback, guidelines=body.guidelines, project=body.project)


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
