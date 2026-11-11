"""Accounts, access, clients/projects, audit trail, system status."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user, needs
from ..enums import ROLE_LEVEL, Role
from ..models import AuditEvent, Client, Form, Project, QCRun, User, UserProjectAccess
from ..permissions import accessible_project_ids, has_perm
from ..security import generate_temp_password, hash_password, password_problems
from ..serializers import audit_out, project_out, user_brief, user_out
from ..services.ai import resolve_provider
from ..services.extraction import ocr_status
from ..storage import get_storage, safe_segment

router = APIRouter(prefix="/api", tags=["admin"])


# ----------------------------------------------------------------------------- users
class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    role: Role
    password: str | None = None
    project_ids: list[int] = []


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    role: Role | None = None
    is_active: bool | None = None
    is_coordinator: bool | None = None


def _active_super_admins(db: Session) -> int:
    return db.execute(select(func.count()).select_from(User).where(User.role == Role.SUPER_ADMIN.value, User.is_active.is_(True))).scalar_one()


@router.get("/users")
def list_users(actor: User = Depends(needs("users.manage")), db: Session = Depends(get_db)):
    users = db.execute(select(User).order_by(User.role, User.name)).scalars().all()
    access = {}
    for a in db.execute(select(UserProjectAccess)).scalars():
        access.setdefault(a.user_id, []).append(a.project_id)
    return [{**user_out(u), "project_ids": sorted(access.get(u.id, []))} for u in users]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, request: Request, actor: User = Depends(needs("users.manage")), db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(409, "A user with this email already exists.")
    temp = body.password or generate_temp_password()
    problems = password_problems(temp)
    if problems:
        raise HTTPException(422, "Password must contain " + ", ".join(problems) + ".")
    user = User(email=email, name=body.name.strip(), role=body.role.value, password_hash=hash_password(temp),
                must_change_password=True, created_by=actor.id)
    db.add(user)
    db.flush()
    for pid in set(body.project_ids):
        if db.get(Project, pid):
            db.add(UserProjectAccess(user_id=user.id, project_id=pid, granted_by=actor.id))
    audit(db, actor, "user.created", entity_type="user", entity_id=user.id,
          detail={"email": email, "role": body.role.value, "projects": body.project_ids}, request=request)
    db.commit()
    # The temporary password is shown exactly once; it is never stored in clear text.
    return {"user": user_out(user), "temporary_password": temp}


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request, actor: User = Depends(needs("users.manage")),
                db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found.")
    changes = {}
    if body.name is not None and body.name != user.name:
        changes["name"] = [user.name, body.name]
        user.name = body.name.strip()
    if body.role is not None and body.role.value != user.role:
        if user.id == actor.id:
            raise HTTPException(409, "You cannot change your own role.")
        if user.role == Role.SUPER_ADMIN.value and _active_super_admins(db) <= 1:
            raise HTTPException(409, "At least one active Super Admin is required.")
        changes["role"] = [user.role, body.role.value]
        user.role = body.role.value
        user.token_version += 1
    if body.is_coordinator is not None and body.is_coordinator != bool(user.is_coordinator):
        changes["is_coordinator"] = [bool(user.is_coordinator), body.is_coordinator]
        user.is_coordinator = body.is_coordinator
        if body.is_coordinator:
            from ..services import project_qc
            db.flush()
            project_qc.ensure_coordinator_access(db)          # a new coordinator sees every project right away
    if body.is_active is not None and body.is_active != user.is_active:
        if user.id == actor.id and not body.is_active:
            raise HTTPException(409, "You cannot deactivate your own account.")
        if not body.is_active and user.role == Role.SUPER_ADMIN.value and _active_super_admins(db) <= 1:
            raise HTTPException(409, "At least one active Super Admin is required.")
        changes["is_active"] = [user.is_active, body.is_active]
        user.is_active = body.is_active
        user.token_version += 1  # revokes sessions immediately
    if changes:
        audit(db, actor, "user.updated", entity_type="user", entity_id=user.id, detail=changes, request=request)
    db.commit()
    return user_out(user)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, request: Request, actor: User = Depends(needs("users.manage")), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found.")
    temp = generate_temp_password()
    user.password_hash = hash_password(temp)
    user.must_change_password = True
    user.token_version += 1
    audit(db, actor, "user.password_reset", entity_type="user", entity_id=user.id, request=request)
    db.commit()
    return {"temporary_password": temp}


@router.get("/users/directory")
def directory(roles: str = Query("editor,manager,qc_reviewer"), project_id: int | None = None, for_project_assignment: bool = False,
              actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """People who can be assigned work. Editors are listed globally; others must have access to the project, except when a
    coordinator / manager is choosing the QC lead for a whole project (the lead gets access when assigned)."""
    if not (has_perm(actor, "forms.assign") or (for_project_assignment and has_perm(actor, "projects.assign_reviewer"))):
        raise HTTPException(403, "Your role does not permit this action (forms.assign).")
    wanted = [r for r in roles.split(",") if r]
    users = db.execute(select(User).where(User.is_active.is_(True), User.role.in_(wanted)).order_by(User.name)).scalars().all()
    out = []
    for u in users:
        if u.role != Role.EDITOR.value and project_id and not for_project_assignment:
            ids = accessible_project_ids(db, u)
            if ids is not None and project_id not in ids:
                continue
        out.append(user_brief(u))
    return out


@router.get("/users/access-list")
def access_list(actor: User = Depends(needs("access.grant")), db: Session = Depends(get_db)):
    """Accounts whose project access the caller may manage (Admins: lower-level roles only)."""
    users = db.execute(select(User).where(User.is_active.is_(True)).order_by(User.role, User.name)).scalars().all()
    if actor.role != Role.SUPER_ADMIN.value:
        users = [u for u in users if ROLE_LEVEL[Role(u.role)] > ROLE_LEVEL[Role(actor.role)]]
    access: dict[int, list[int]] = {}
    for a in db.execute(select(UserProjectAccess)).scalars():
        access.setdefault(a.user_id, []).append(a.project_id)
    return [{**user_out(u), "project_ids": sorted(access.get(u.id, []))} for u in users]


@router.get("/users/{user_id}/access")
def get_access(user_id: int, actor: User = Depends(needs("access.grant")), db: Session = Depends(get_db)):
    rows = db.execute(select(UserProjectAccess.project_id).where(UserProjectAccess.user_id == user_id)).all()
    return {"project_ids": sorted(r[0] for r in rows)}


class AccessIn(BaseModel):
    project_ids: list[int]


@router.put("/users/{user_id}/access")
def set_access(user_id: int, body: AccessIn, request: Request, actor: User = Depends(needs("access.grant")),
               db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found.")
    if actor.role != Role.SUPER_ADMIN.value:
        # Admins manage operational access only: for lower-level roles and only within projects they hold themselves.
        if ROLE_LEVEL[Role(target.role)] <= ROLE_LEVEL[Role(actor.role)]:
            raise HTTPException(403, "Admins cannot change access for accounts at their own level or above.")
        mine = accessible_project_ids(db, actor) or set()
        manageable = mine
    else:
        manageable = None
    current = {r[0] for r in db.execute(select(UserProjectAccess.project_id).where(UserProjectAccess.user_id == user_id)).all()}
    desired = set(body.project_ids)
    for pid in desired:
        if not db.get(Project, pid):
            raise HTTPException(422, f"Project {pid} does not exist.")
    add, remove = desired - current, current - desired
    if manageable is not None:
        if (add | remove) - manageable:
            raise HTTPException(403, "You can only change access to projects you have access to yourself.")
    for pid in add:
        db.add(UserProjectAccess(user_id=user_id, project_id=pid, granted_by=actor.id))
    for pid in remove:
        row = db.get(UserProjectAccess, (user_id, pid))
        if row:
            db.delete(row)
    if add or remove:
        audit(db, actor, "access.changed", entity_type="user", entity_id=user_id,
              detail={"granted": sorted(add), "revoked": sorted(remove)}, request=request)
    db.commit()
    return {"project_ids": sorted(desired)}


# ----------------------------------------------------------------------------- clients / projects
class ClientIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class ProjectIn(BaseModel):
    client_id: int
    name: str = Field(min_length=2, max_length=160)
    form_type: str = "standard"
    editor_user_id: int | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    form_type: str | None = None
    is_active: bool | None = None
    editor_user_id: int | None = None


@router.get("/clients")
def list_clients(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ids = accessible_project_ids(db, actor)
    q = select(Client).order_by(Client.name)
    clients = db.execute(q).scalars().all()
    if ids is not None:
        clients = [c for c in clients if any(p.id in ids for p in c.projects)]
    return [{"id": c.id, "name": c.name, "folder": c.folder, "is_active": c.is_active, "is_sample": c.is_sample,
             "project_count": len([p for p in c.projects if ids is None or p.id in ids])} for c in clients]


@router.post("/clients", status_code=201)
def create_client(body: ClientIn, request: Request, actor: User = Depends(needs("orgs.manage")), db: Session = Depends(get_db)):
    name = body.name.strip()
    folder = safe_segment(name).replace(" ", "_")
    if db.execute(select(Client).where(or_(Client.name == name, Client.folder == folder))).scalar_one_or_none():
        raise HTTPException(409, "A client with this name already exists.")
    c = Client(name=name, folder=folder)
    db.add(c)
    db.flush()
    audit(db, actor, "client.created", entity_type="client", entity_id=c.id, detail={"name": name}, request=request)
    db.commit()
    return {"id": c.id, "name": c.name, "folder": c.folder}


@router.get("/projects")
def list_projects(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ids = accessible_project_ids(db, actor)
    projects = db.execute(select(Project).order_by(Project.client_id, Project.name)).scalars().all()
    return [project_out(p) for p in projects if ids is None or p.id in ids]


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, request: Request, actor: User = Depends(needs("orgs.manage")), db: Session = Depends(get_db)):
    client = db.get(Client, body.client_id)
    if not client:
        raise HTTPException(404, "Client not found.")
    folder = safe_segment(body.name).replace(" ", "_")
    if db.execute(select(Project).where(Project.client_id == client.id, Project.folder == folder)).scalar_one_or_none():
        raise HTTPException(409, "This client already has a project/editor folder with that name.")
    p = Project(client_id=client.id, name=body.name.strip(), folder=folder, form_type=body.form_type,
                editor_user_id=body.editor_user_id)
    db.add(p)
    db.flush()
    audit(db, actor, "project.created", entity_type="project", entity_id=p.id, project_id=p.id,
          detail={"client": client.name, "name": p.name}, request=request)
    db.commit()
    return project_out(p)


@router.patch("/projects/{project_id}")
def update_project(project_id: int, body: ProjectUpdate, request: Request, actor: User = Depends(needs("orgs.manage")),
                   db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found.")
    changes = {}
    for field in ("name", "form_type", "is_active", "editor_user_id"):
        v = getattr(body, field)
        if v is not None and v != getattr(p, field):
            changes[field] = [getattr(p, field), v]
            setattr(p, field, v)  # the on-disk folder name is intentionally immutable
    if changes:
        audit(db, actor, "project.updated", entity_type="project", entity_id=p.id, project_id=p.id, detail=changes, request=request)
    db.commit()
    return project_out(p)


# ----------------------------------------------------------------------------- audit
@router.get("/audit")
def audit_trail(action: str | None = None, actor_id: int | None = None, form_id: int | None = None, project_id: int | None = None,
                q: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
                limit: int = Query(100, le=500), offset: int = 0, user: User = Depends(needs("audit.view")),
                db: Session = Depends(get_db)):
    stmt = select(AuditEvent)
    ids = accessible_project_ids(db, user)
    if ids is not None:  # admins only see events tied to projects they can access
        stmt = stmt.where(AuditEvent.project_id.in_(ids) if ids else AuditEvent.id.is_(None))
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if actor_id:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    if form_id:
        stmt = stmt.where(AuditEvent.form_id == form_id)
    if project_id:
        stmt = stmt.where(AuditEvent.project_id == project_id)
    if date_from:
        stmt = stmt.where(AuditEvent.created_at >= date_from)
    if date_to:
        stmt = stmt.where(AuditEvent.created_at <= date_to)
    if q:
        stmt = stmt.where(or_(AuditEvent.action.ilike(f"%{q}%"), AuditEvent.actor_label.ilike(f"%{q}%"),
                              AuditEvent.entity_type.ilike(f"%{q}%")))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(AuditEvent.id.desc()).limit(limit).offset(offset)).scalars().all()
    return {"total": total, "items": [audit_out(e) for e in rows]}


# ----------------------------------------------------------------------------- system
@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok"}


@router.get("/system/status")
def system_status(user: User = Depends(needs("system.view")), db: Session = Depends(get_db)):
    s = get_settings()
    provider, ai_status, ai_msg = resolve_provider()
    try:
        storage = get_storage().health()
    except Exception as exc:  # noqa: BLE001
        storage = {"configured": False, "detail": str(exc)}
    failed = db.execute(select(func.count()).select_from(QCRun).where(QCRun.status == "failed")).scalar_one()
    return {
        "environment": s.env, "database": s.database_url.split(":")[0], "storage": storage, "ocr": ocr_status(),
        "ai": {"configured_provider": s.ai_provider, "usable": provider is not None, "status": ai_status.value, "message": ai_msg,
               "model": provider.model if provider else None, "allow_external": s.ai_allow_external},
        "policy": {"allow_self_review": s.allow_self_review, "max_upload_mb": s.max_upload_mb},
        "forms": db.execute(select(func.count()).select_from(Form)).scalar_one(), "failed_runs": failed,
    }
