"""Project coordinators (Rahat, like the manager Geeta) see every project and hand it to a QC lead; the lead then sees its forms."""
from app.db import SessionLocal
from app.models import Client, Project, UserProjectAccess


def _new_project(name="Valley_QC"):
    db = SessionLocal()
    client = db.query(Client).first()
    p = Project(client_id=client.id, name=name, folder=name)
    db.add(p)
    db.commit()
    pid = p.id
    db.close()
    return pid


def test_coordinator_sees_every_project_and_assigns_it(api, ctx):
    q2 = ctx["users"]["qc2"]
    sa = api("sa")
    pid = _new_project()
    # a plain QC lead cannot assign and does not see the unassigned project
    qc = api("qc")
    assert qc.put(f"/api/qc/projects/{pid}/reviewer", json={"reviewer_id": ctx["users"]["qc"]}).status_code == 403
    assert pid not in [p["id"] for p in qc.get("/api/qc/projects").json()]
    # the Super Admin makes qc2 a coordinator: every project becomes visible and she may assign
    r = sa.patch(f"/api/users/{q2}", json={"is_coordinator": True})
    assert r.status_code == 200, r.text
    co = api("qc2")
    assert "projects.assign_reviewer" in co.get("/api/auth/me").json()["permissions"]
    seen = {p["id"]: p for p in co.get("/api/qc/projects").json()}
    assert pid in seen and seen[pid]["reviewer"] is None
    assert seen[pid]["suggested_reviewer"]["name"] == "QC"            # the folder name “Valley_QC” is only a suggestion
    db = SessionLocal()
    assert db.get(UserProjectAccess, (q2, pid)) is not None
    db.close()
    names = {u["name"] for u in co.get(f"/api/users/directory?roles=qc_reviewer,manager&project_id={pid}&for_project_assignment=true").json()}
    assert {"QC", "MANAGER"} <= names                                  # leads without access yet can still be chosen
    assert qc.get(f"/api/users/directory?roles=qc_reviewer&project_id={pid}&for_project_assignment=true").status_code == 403
    # coordinator assigns: the QC lead now sees the project; nobody else does
    assert co.put(f"/api/qc/projects/{pid}/reviewer", json={"reviewer_id": ctx["users"]["qc"]}).status_code == 200
    assert pid in [p["id"] for p in api("qc").get("/api/qc/projects?mine=true").json()]
    assert api("qc").get(f"/api/qc/projects/{pid}").status_code == 200
    # coordinators have no other manager rights
    assert co.get("/api/integrations/drive/status").status_code == 403
    # handing it to someone else takes it away from the previous lead, never from the coordinator
    assert co.put(f"/api/qc/projects/{pid}/reviewer", json={"reviewer_id": ctx["users"]["manager"]}).status_code == 200
    assert api("qc").get(f"/api/qc/projects/{pid}").status_code in (403, 404)
    assert co.get(f"/api/qc/projects/{pid}").status_code == 200


def test_new_drive_projects_wait_for_assignment(api, ctx):
    from app.config import get_settings
    from app.models import ImportJob, ProjectReviewer
    from app.services import drive_import

    assert get_settings().auto_assign_by_folder_name is False
    api("sa").patch(f"/api/users/{ctx['users']['qc2']}", json={"is_coordinator": True})
    db = SessionLocal()
    job = ImportJob(source="test", kind="zip", status="running", created_by=ctx["users"]["manager"])
    db.add(job)
    db.flush()
    rep = drive_import.Reporter(db, job)
    p = drive_import.get_project(db, db.query(Client).first(), "Orlando_QC", db.get(drive_import.User, ctx["users"]["manager"]), rep)
    db.commit()
    assert db.get(ProjectReviewer, p.id) is None                                  # not handed to “QC” just because of the folder name
    assert db.get(UserProjectAccess, (ctx["users"]["qc2"], p.id)) is not None     # the coordinator sees it straight away
    assert db.get(UserProjectAccess, (ctx["users"]["manager"], p.id)) is not None
    assert db.get(UserProjectAccess, (ctx["users"]["qc"], p.id)) is None          # ... and so does nobody else among the QC leads
    assert any("waiting for a coordinator" in l for l in rep.lines)
    db.close()
