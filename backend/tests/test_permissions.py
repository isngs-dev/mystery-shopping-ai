from fastapi.testclient import TestClient

from app.main import app
from app.sample_docs import make_pair

from .conftest import PASSWORD
from .helpers import intake, upload


def test_unauthenticated_and_csrf(api, ctx):
    c = TestClient(app)
    assert c.get("/api/forms").status_code == 401
    assert c.get("/api/dashboard").status_code == 401
    a = api("manager")
    # cookie session + unsafe method without the CSRF header is rejected
    r = a.c.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "Another-Passw0rd-1"})
    assert r.status_code == 403


def test_login_lockout_and_deactivation(api, ctx):
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "editor@test.local", "password": "wrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "editor@test.local", "password": PASSWORD}).status_code == 429
    sa = api("sa")
    ed = api("qc2")
    assert ed.get("/api/auth/me").status_code == 200
    r = sa.patch(f"/api/users/{ctx['users']['qc2']}", json={"is_active": False})
    assert r.status_code == 200
    assert ed.get("/api/auth/me").status_code == 401           # session revoked immediately


def test_account_hierarchy(api, ctx):
    admin, sa = api("admin"), api("sa")
    # only the Super Admin creates accounts / assigns roles
    body = {"email": "new.person@acme-qc.com", "name": "New Person", "role": "editor"}
    assert admin.post("/api/users", json=body).status_code == 403
    assert api("manager").post("/api/users", json=body).status_code == 403
    r = sa.post("/api/users", json=body)
    assert r.status_code == 201 and r.json()["temporary_password"]
    uid = r.json()["user"]["id"]
    assert r.json()["user"]["must_change_password"] is True
    # new account has no implicit project access
    login = TestClient(app)
    tp = r.json()["temporary_password"]
    assert login.post("/api/auth/login", json={"email": "new.person@acme-qc.com", "password": tp}).status_code == 200
    assert login.get("/api/projects", headers={"X-Requested-With": "XMLHttpRequest"}).json() == []
    # admin may grant access only within own projects, and not to peers
    assert admin.put(f"/api/users/{uid}/access", json={"project_ids": [ctx["p1"]]}).status_code == 200
    assert admin.put(f"/api/users/{uid}/access", json={"project_ids": [ctx["p1"], ctx["p2"]]}).status_code == 403
    assert admin.put(f"/api/users/{ctx['users']['sa']}/access", json={"project_ids": [ctx["p1"]]}).status_code == 403
    # cannot demote the last super admin / deactivate self
    assert sa.patch(f"/api/users/{ctx['users']['sa']}", json={"is_active": False}).status_code == 409
    assert sa.patch(f"/api/users/{ctx['users']['sa']}", json={"role": "admin"}).status_code == 409
    # weak passwords rejected
    assert sa.post("/api/users", json={**body, "email": "x.y@acme-qc.com", "password": "short"}).status_code == 422
    actions = {e["action"] for e in sa.get("/api/audit?limit=200").json()["items"]}
    assert {"user.created", "access.changed"} <= actions


def test_scope_isolation_and_role_gates(api, ctx):
    form_id, _, (raw, edited) = intake(api, ctx)
    # editor not assigned to the form cannot see or fetch it
    e2 = api("editor2")
    assert e2.get("/api/forms").json()["total"] == 0
    assert e2.get(f"/api/forms/{form_id}").status_code == 404
    doc_id = api("manager").get(f"/api/forms/{form_id}").json()["raw_document_id"]
    assert e2.get(f"/api/documents/{doc_id}/file").status_code == 404
    assert api("editor").get(f"/api/documents/{doc_id}/file").status_code == 200
    # editors can't upload RAW; QC reviewers can't upload anything; QC can't alter PDFs
    assert upload(api("editor"), ctx["p1"], "FORM090_1.pdf", raw).json()["results"][0]["error"]
    assert upload(api("qc"), ctx["p1"], "FORM090_1.pdf", raw).status_code == 403
    # users without project access cannot see another client's project
    assert [p["id"] for p in api("manager").get("/api/projects").json()] == [ctx["p1"]]
    assert upload(api("manager"), ctx["p2"], "FORM091_1.pdf", raw).json()["results"][0]["error"]
    # QC decisions need the reviewer permission; admins / super admins / editors cannot approve. A Manager is an all-round
    # lead and may review (never her own edits: the independent-review guard is checked separately in the workflow tests)
    for who in ("admin", "sa", "editor"):
        assert api(who).post(f"/api/forms/{form_id}/decision", json={"decision": "approved"}).status_code == 403
    assert api("manager").post(f"/api/forms/{form_id}/decision", json={"decision": "approved"}).status_code != 403
    # client POC sees nothing until a form is approved
    assert api("poc").get("/api/forms").json()["total"] == 0
    # reporting users get dashboards but no documents
    assert api("viewer").get("/api/dashboard").status_code == 200
    doc = api("viewer").get(f"/api/documents/{doc_id}/file")
    assert doc.status_code == 403
    # audit trail restricted
    assert api("editor").get("/api/audit").status_code == 403
    assert api("qc").get("/api/audit").status_code == 403
    assert api("admin").get("/api/audit").status_code == 200


def test_state_machine_rejects_illegal_moves(api, ctx):
    form_id, _, _ = intake(api, ctx, submit=False)
    qc = api("qc")
    assert qc.post(f"/api/forms/{form_id}/decision", json={"decision": "approved"}).status_code == 409   # not in QC review
    assert api("manager").post(f"/api/forms/{form_id}/status", json={"action": "close"}).status_code == 409
    raw, edited, _ = make_pair(1, "clean")
    # editor assigned to another form cannot submit here
    assert upload(api("editor2"), ctx["p1"], "FORM001_2.pdf", edited).json()["failed"] == 1


def test_independent_review_policy(api, ctx):
    """A reviewer who is also the assigned editor/uploader cannot take the QC decision."""
    from app.db import SessionLocal
    from app.models import Form
    form_id, _, _ = intake(api, ctx)
    db = SessionLocal()
    f = db.get(Form, form_id)
    f.assigned_editor_id = ctx["users"]["qc"]       # simulate a conflict of interest
    db.commit()
    db.close()
    qc = api("qc")
    findings = qc.get(f"/api/forms/{form_id}/qc").json()["findings"]
    r = qc.post(f"/api/findings/{findings[0]['id']}/review", json={"action": "reject"})
    assert r.status_code == 403
    assert qc.post(f"/api/forms/{form_id}/decision", json={"decision": "manual_review_required", "summary": "x"}).status_code == 403


def test_demo_login_is_development_only(api, ctx, monkeypatch):
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import User
    import app.routers.auth as auth_router
    c = TestClient(app)
    # inactive in the test/production environments
    assert c.get("/api/auth/demo-accounts").json() == {"enabled": False, "accounts": []}
    assert c.post("/api/auth/demo-login", json={"user_id": ctx["users"]["qc"]}).status_code == 404
    # even in development it needs loopback and a SAMPLE account
    s = get_settings()
    monkeypatch.setattr(s, "env", "development")
    assert c.get("/api/auth/demo-accounts").json()["enabled"] is False          # TestClient host is not loopback
    monkeypatch.setattr(auth_router, "_is_loopback", lambda r: True)
    db = SessionLocal()
    sample = db.get(User, ctx["users"]["qc"])
    sample.is_sample = True
    db.commit()
    db.close()
    accts = c.get("/api/auth/demo-accounts").json()
    assert accts["enabled"] and [a["id"] for a in accts["accounts"]] == [ctx["users"]["qc"]]
    assert "password_hash" not in str(accts)
    assert c.post("/api/auth/demo-login", json={"user_id": ctx["users"]["sa"]}).status_code == 404   # not a sample account
    r = c.post("/api/auth/demo-login", json={"user_id": ctx["users"]["qc"]})
    assert r.status_code == 200 and r.json()["user"]["role"] == "qc_reviewer"
    assert c.get("/api/auth/me").status_code == 200
    # the working team can be offered by e-mail domain, and the SAMPLE accounts hidden
    db = SessionLocal()
    team_domain = db.get(User, ctx["users"]["sa"]).email.rsplit("@", 1)[-1]
    db.close()
    monkeypatch.setattr(s, "demo_login_domains", team_domain)
    monkeypatch.setattr(s, "demo_login_show_samples", False)
    ids = {a["id"] for a in c.get("/api/auth/demo-accounts").json()["accounts"]}
    assert ctx["users"]["sa"] in ids and ctx["users"]["qc"] in ids        # the sample qc account shares the test domain
    monkeypatch.setattr(s, "demo_login_domains", "isngs.example")
    assert c.get("/api/auth/demo-accounts").json()["accounts"] == []
    assert c.post("/api/auth/demo-login", json={"user_id": ctx["users"]["qc"]}).status_code == 404
    monkeypatch.setattr(s, "demo_login_show_samples", True)
    monkeypatch.setattr(s, "demo_login_enabled", False)
    assert c.post("/api/auth/demo-login", json={"user_id": ctx["users"]["qc"]}).status_code == 404
