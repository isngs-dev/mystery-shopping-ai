"""Central role/permission matrix plus data-scope helpers. Enforced server-side in every router."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .enums import FormStatus, Role
from .models import Form, Project, User, UserProjectAccess

R = Role
ALL = set(Role)

PERMISSIONS: dict[str, set[Role]] = {
    "users.manage": {R.SUPER_ADMIN},
    "orgs.manage": {R.SUPER_ADMIN},                       # clients & projects
    "access.grant": {R.SUPER_ADMIN, R.ADMIN},             # project access (admin: only within own projects)
    "audit.view": {R.SUPER_ADMIN, R.ADMIN},
    "system.view": {R.SUPER_ADMIN, R.ADMIN, R.AI_LEAD},
    "dashboard.view": ALL,
    "forms.view": ALL,
    "forms.assign": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER},
    "projects.assign_reviewer": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER},      # hand a whole project to a QC lead: QC runs on all its forms
    "docs.upload_raw": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER},
    "docs.upload_edited": {R.MANAGER, R.EDITOR},
    "docs.ai_edit": {R.ADMIN, R.MANAGER, R.EDITOR},       # run the AI editor on a form
    "qc.report": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.QC_REVIEWER},     # QC report PDF (contains evidence + reviewer data)
    "integrations.manage": {R.MANAGER},                                   # Drive import / sync: only the manager (Geeta); others see the imported data
    "tracker.view": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.QC_REVIEWER, R.REPORTING, R.OPS_SPONSOR, R.PROCESS_OWNER},
    "media.view": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.EDITOR, R.QC_REVIEWER},           # recordings contain staff / customer voices
    "media.manage": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER},
    "aiedit.view": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.EDITOR, R.QC_REVIEWER},
    "docs.view": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.EDITOR, R.QC_REVIEWER, R.CLIENT_POC},
    "repository.exceptions": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER},
    "qc.run": {R.ADMIN, R.MANAGER, R.QC_REVIEWER},
    "qc.view": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.QC_REVIEWER, R.EDITOR, R.CLIENT_POC},
    "qc.review": {R.QC_REVIEWER, R.MANAGER},              # editors cannot review; nobody reviews work they edited (see ALLOW_SELF_REVIEW)
    "workflow.start": {R.EDITOR, R.MANAGER},
    "workflow.close": {R.ADMIN, R.MANAGER, R.QC_REVIEWER},
    "workflow.escalate": {R.QC_REVIEWER, R.MANAGER, R.ADMIN},
    "workflow.resolve_manual": {R.ADMIN, R.MANAGER, R.PROCESS_OWNER, R.QC_REVIEWER},
    "workflow.block": {R.ADMIN, R.MANAGER, R.QC_REVIEWER},
    "rules.view": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.QC_REVIEWER, R.AI_LEAD, R.PROCESS_OWNER, R.OPS_SPONSOR,
                   R.EDITOR},
    "rules.author": {R.SUPER_ADMIN, R.MANAGER, R.AI_LEAD, R.PROCESS_OWNER, R.QC_REVIEWER},      # Admin (Shreyas) only views the rules
    "rules.approve": {R.SUPER_ADMIN, R.PROCESS_OWNER, R.OPS_SPONSOR},
    "reports.view": ALL,
    "reports.export": ALL,
    "comments.add": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.EDITOR, R.QC_REVIEWER},
}

# Which reports each role may open (data is additionally scoped to their projects / own work).
REPORT_ACCESS: dict[str, set[Role]] = {
    "project_performance": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.REPORTING, R.OPS_SPONSOR, R.PROCESS_OWNER,
                            R.CLIENT_POC, R.QC_REVIEWER},
    "editor_quality": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.REPORTING, R.OPS_SPONSOR, R.PROCESS_OWNER, R.EDITOR},
    "reviewer_workload": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.REPORTING, R.OPS_SPONSOR, R.PROCESS_OWNER,
                          R.QC_REVIEWER},
    "backlog_turnaround": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.REPORTING, R.OPS_SPONSOR, R.PROCESS_OWNER,
                           R.QC_REVIEWER, R.EDITOR},
    "ai_performance": {R.SUPER_ADMIN, R.ADMIN, R.AI_LEAD, R.PROCESS_OWNER, R.OPS_SPONSOR, R.REPORTING},
    "technical_failures": {R.SUPER_ADMIN, R.ADMIN, R.AI_LEAD, R.PROCESS_OWNER},
    "repeat_errors": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.REPORTING, R.OPS_SPONSOR, R.PROCESS_OWNER,
                      R.QC_REVIEWER, R.EDITOR, R.AI_LEAD},
    "qc_scorecard": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.REPORTING, R.OPS_SPONSOR, R.PROCESS_OWNER, R.QC_REVIEWER, R.EDITOR},
    "qc_form_scores": {R.SUPER_ADMIN, R.ADMIN, R.MANAGER, R.REPORTING, R.OPS_SPONSOR, R.PROCESS_OWNER, R.QC_REVIEWER, R.EDITOR},
}


# Rights a project coordinator (User.is_coordinator) gets on top of their role.
COORDINATOR_PERMS = {"projects.assign_reviewer"}


def has_perm(user: User, perm: str) -> bool:
    return Role(user.role) in PERMISSIONS.get(perm, set()) or (bool(user.is_coordinator) and perm in COORDINATOR_PERMS)


def require_perm(user: User, perm: str) -> None:
    if not has_perm(user, perm):
        raise HTTPException(403, f"Your role does not permit this action ({perm}).")


def permission_list(user: User) -> list[str]:
    return sorted(p for p in PERMISSIONS if has_perm(user, p))


# ---------------------------------------------------------------- data scope
def granted_project_ids(db: Session, user: User) -> set[int]:
    rows = db.execute(select(UserProjectAccess.project_id).where(UserProjectAccess.user_id == user.id)).all()
    return {r[0] for r in rows}


def accessible_project_ids(db: Session, user: User) -> set[int] | None:
    """None means unrestricted (Super Admin only). Everyone else needs explicit grants."""
    if Role(user.role) == Role.SUPER_ADMIN:
        return None
    ids = granted_project_ids(db, user)
    if Role(user.role) == Role.EDITOR:  # editors also see projects of forms assigned to them
        rows = db.execute(select(Form.project_id).where(Form.assigned_editor_id == user.id).distinct()).all()
        ids |= {r[0] for r in rows}
    return ids


def form_scope_clause(db: Session, user: User):
    """SQLAlchemy WHERE clause restricting Form rows to what the user may see."""
    role = Role(user.role)
    if role == Role.SUPER_ADMIN:
        return Form.id.is_not(None)
    if role == Role.EDITOR:
        return Form.assigned_editor_id == user.id
    ids = granted_project_ids(db, user)
    clause = Form.project_id.in_(ids) if ids else Form.id.is_(None)
    if role == Role.CLIENT_POC:
        clause = clause & Form.status.in_([FormStatus.APPROVED.value, FormStatus.CLOSED.value])
    return clause


def project_scope_ids(db: Session, user: User) -> set[int] | None:
    return accessible_project_ids(db, user)


def get_form_or_404(db: Session, user: User, form_id: int) -> Form:
    form = db.execute(select(Form).where(Form.id == form_id).where(form_scope_clause(db, user))).scalar_one_or_none()
    if not form:
        raise HTTPException(404, "Form not found or not within your access.")
    return form


def get_project_or_404(db: Session, user: User, project_id: int) -> Project:
    project = db.get(Project, project_id)
    ids = accessible_project_ids(db, user)
    if not project or (ids is not None and project_id not in ids):
        raise HTTPException(404, "Project not found or not within your access.")
    return project
