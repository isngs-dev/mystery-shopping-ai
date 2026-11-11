"""Project-level QC: give a project to a QC lead, follow its progress, download the project report."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import get_current_user, needs
from ..models import Project, User
from ..permissions import accessible_project_ids, get_project_or_404, has_perm
from ..services import project_qc

router = APIRouter(prefix="/api/qc/projects", tags=["project-qc"])


def _reviewer_view(user: User) -> User:
    if not has_perm(user, "qc.report"):            # QC leads, manager, admins: not editors or the client's contact
        raise HTTPException(403, "Project QC progress is for reviewers, managers and admins.")
    return user


@router.get("")
def my_projects(mine: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Projects the user can see with their QC progress; mine=true keeps only the projects handed to this user."""
    _reviewer_view(user)
    ids = accessible_project_ids(db, user)
    projects = [p for p in db.execute(select(Project).where(Project.is_active.is_(True)).order_by(Project.client_id, Project.name)).scalars()
                if ids is None or p.id in ids]
    return project_qc.overview(db, projects, user, mine)


@router.get("/{project_id}")
def project_progress(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _reviewer_view(user)
    return project_qc.summary(db, get_project_or_404(db, user, project_id))


class ReviewerIn(BaseModel):
    reviewer_id: int | None = None


@router.put("/{project_id}/reviewer")
def set_reviewer(project_id: int, body: ReviewerIn, request: Request, user: User = Depends(needs("projects.assign_reviewer")),
                 db: Session = Depends(get_db)):
    """Hand the project to a QC lead: she becomes reviewer of all its forms and QC is queued for every complete pair."""
    project = get_project_or_404(db, user, project_id)
    reviewer = None
    if body.reviewer_id is not None:
        reviewer = db.get(User, body.reviewer_id)
        if not reviewer:
            raise HTTPException(404, "Reviewer not found.")
    return project_qc.assign_project(db, user, project, reviewer, request)


@router.post("/{project_id}/run", status_code=202)
def run_missing(project_id: int, request: Request, user: User = Depends(needs("qc.run")), db: Session = Depends(get_db)):
    """Queue QC for every form of the project that has no analysis yet (and retry the ones whose analysis failed)."""
    project = get_project_or_404(db, user, project_id)
    analysis = project_qc.queue_analysis(db, user, project)
    audit(db, user, "project.qc_queued", entity_type="project", entity_id=project.id, project_id=project.id,
          detail={k: v for k, v in analysis.items() if k != "run_ids"}, request=request)
    db.commit()
    project_qc.start_runs(analysis["run_ids"])
    return {k: v for k, v in analysis.items() if k != "run_ids"}


@router.get("/{project_id}/report")
def project_report(project_id: int, request: Request, user: User = Depends(needs("qc.report")), db: Session = Depends(get_db)):
    project = get_project_or_404(db, user, project_id)
    data, base = project_qc.project_report(db, project, user)
    audit(db, user, "report.project_qc_downloaded", entity_type="project", entity_id=project.id, project_id=project.id,
          detail={"bytes": len(data)}, request=request)
    db.commit()
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{base}.pdf"', "Cache-Control": "no-store"})


@router.get("/{project_id}/report.xlsx")
def project_report_excel(project_id: int, request: Request, user: User = Depends(needs("qc.report")), db: Session = Depends(get_db)):
    """The project's QC report as an Excel sheet in the layout of the team's tracker: one row per form."""
    from ..services import project_excel

    project = get_project_or_404(db, user, project_id)
    data, name = project_excel.build_project_excel(db, project, user)
    audit(db, user, "report.project_qc_excel_downloaded", entity_type="project", entity_id=project.id, project_id=project.id,
          detail={"bytes": len(data)}, request=request)
    db.commit()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/{project_id}/reports.zip")
def project_reports_zip(project_id: int, request: Request, user: User = Depends(needs("qc.report")), db: Session = Depends(get_db)):
    """The project summary plus the QC report of every analysed form."""
    project = get_project_or_404(db, user, project_id)
    data, name = project_qc.report_bundle(db, project, user)
    audit(db, user, "report.project_qc_bundle_downloaded", entity_type="project", entity_id=project.id, project_id=project.id,
          detail={"bytes": len(data)}, request=request)
    db.commit()
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
