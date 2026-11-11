"""Give every Drive project to the person its folder is named after, and show each QC lead only their own projects.

    python scripts/arrange_by_drive.py            # dry run
    python scripts/arrange_by_drive.py --apply

"Valley Bank_Geeta" belongs to Geeta, "Orlando_Devansh" to Devansh, "Oil Change_Saradiya" to Saradhya. The owner becomes the project's QC
lead (all its forms land in her queue), admins and the manager keep seeing everything, and other QC leads lose access to projects that are
not theirs. Projects whose folder name matches nobody stay with the admins/manager until someone assigns them under QC Projects.
New Drive folders are arranged the same way automatically while importing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.enums import Role  # noqa: E402
from app.models import Project, ProjectReviewer, User, UserProjectAccess  # noqa: E402
from app.services import project_qc  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = SessionLocal()
    actor = db.execute(select(User).where(User.role == Role.SUPER_ADMIN.value, User.is_active.is_(True)).order_by(User.id)).scalars().first()
    projects = db.execute(select(Project).where(Project.is_active.is_(True)).order_by(Project.client_id, Project.name)).scalars().all()
    leads = {u.id: u for u in db.execute(select(User).where(User.role == Role.QC_REVIEWER.value, User.is_active.is_(True))).scalars()}
    owners: dict[int, User | None] = {}
    for p in projects:
        owners[p.id] = project_qc.folder_owner(db, p.name)
        print(f"{p.client.name:<12}/ {p.name:<24} -> {owners[p.id].name if owners[p.id] else '(no match: admins and manager only)'}")
    if not args.apply:
        print("\nDry run only. Re-run with --apply.")
        return
    for p in projects:
        owner = owners[p.id]
        if owner and (db.get(ProjectReviewer, p.id) is None or db.get(ProjectReviewer, p.id).reviewer_id != owner.id):
            out = project_qc.assign_project(db, actor, p, owner)
            project_qc.start_runs(out["analysis"]["run_ids"])
            print(f"assigned {p.name} -> {owner.name}: {out['assigned']} form(s), {len(out['conflicts'])} conflict(s), QC queued {out['analysis']['queued']}")
        for uid in leads:                        # a QC lead only sees her own projects
            if not (owner and owner.id == uid):
                db.execute(delete(UserProjectAccess).where(UserProjectAccess.user_id == uid, UserProjectAccess.project_id == p.id))
    db.commit()
    for u in db.execute(select(User).where(User.is_active.is_(True)).order_by(User.id)).scalars():
        n = db.execute(select(UserProjectAccess).where(UserProjectAccess.user_id == u.id)).scalars().all()
        print(f"{u.name:<10}{u.role:<12}sees {len(n)} project(s)")


if __name__ == "__main__":
    main()
