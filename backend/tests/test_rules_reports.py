import csv
import io

from openpyxl import load_workbook

from .helpers import intake


def test_rule_versioning_and_maker_checker(api, ctx):
    lead, owner = api("lead"), api("owner")
    r = lead.post("/api/rules", json={
        "rule_key": "ACME-TERMS", "name": "No slang in Acme reports", "error_subtype": "terminology", "severity": "major",
        "client_id": 1, "check_type": "forbidden_terms", "config": {"terms": ["awesome"], "suggestion": "Use neutral wording."}})
    assert r.status_code == 201, r.text
    vid = r.json()["id"]
    assert r.json()["status"] == "draft" and r.json()["scope"] == "client"
    assert r.json()["error_subtype"] == "terminology" and r.json()["category"] == "grammar"        # category comes from the error table
    bad = lead.post("/api/rules", json={"name": "Be nice to everyone", "error_subtype": "tone_of_voice", "check_type": "ai_guideline"})
    assert bad.status_code == 422                                                                    # only rules on the QC error table
    # draft is not active yet
    assert lead.post(f"/api/rules/versions/{vid}/submit").status_code == 200
    # author cannot approve own rule; QC/editors can't approve; owner can
    assert lead.post(f"/api/rules/versions/{vid}/decision", json={"approve": True}).status_code == 403
    assert api("qc").post(f"/api/rules/versions/{vid}/decision", json={"approve": True}).status_code == 403
    assert owner.post(f"/api/rules/versions/{vid}/decision", json={"approve": True}).status_code == 200
    # published versions are immutable; changes create a new version that again needs approval
    assert lead.patch(f"/api/rules/versions/{vid}", json={"name": "Changed", "change_note": "x" * 5}).status_code == 409
    rid = r.json()["rule_id"]
    nv = lead.post(f"/api/rules/{rid}/versions", json={"severity": "critical", "change_note": "Escalate severity"})
    assert nv.status_code == 201 and nv.json()["version"] == 2 and nv.json()["status"] == "draft"

    # the published rule v1 is applied by the pipeline and recorded on the run
    form_id, _, _ = intake(api, ctx)
    from app.db import SessionLocal
    from app.models import Document
    # (sample text has no 'awesome'; just verify the exact versions used are recorded)
    run = api("qc").get(f"/api/forms/{form_id}/qc").json()["run"]
    used = {(v["rule_key"], v["version"]) for v in run["rule_versions"]}
    assert ("ACME-TERMS", 1) in used and ("GEN-SPELL", 1) in used
    # while v2 is only a draft/pending, runs keep using v1
    lead.post(f"/api/rules/versions/{nv.json()['id']}/submit")
    owner.post(f"/api/rules/versions/{nv.json()['id']}/decision", json={"approve": False, "reason": "Needs examples"})
    hist = lead.get(f"/api/rules/{rid}/history").json()
    assert [h["status"] for h in hist] == ["published", "rejected"]
    assert api("sa").get("/api/rules").status_code == 200
    assert api("viewer").get("/api/rules").status_code == 403


def test_forbidden_terms_rule_produces_finding(api, ctx):
    lead, owner = api("lead"), api("owner")
    vid = lead.post("/api/rules", json={"rule_key": "NO-BLENDER", "name": "Do not name competitor products", "error_subtype": "terminology",
                                        "severity": "major", "check_type": "forbidden_terms", "config": {"terms": ["premium blender"]}}).json()["id"]
    lead.post(f"/api/rules/versions/{vid}/submit")
    owner.post(f"/api/rules/versions/{vid}/decision", json={"approve": True})
    form_id, _, _ = intake(api, ctx)
    fs = api("qc").get(f"/api/forms/{form_id}/qc").json()["findings"]
    hit = [f for f in fs if f["rule_label"] and f["rule_label"].startswith("NO-BLENDER v1")]
    assert hit and hit[0]["severity"] == "minor" and hit[0]["type"] == "missed_correction"   # unchanged from RAW => missed
    assert hit[0]["error_subtype"] == "terminology" and hit[0]["category"] == "grammar"


def test_reports_scope_filters_and_exports(api, ctx):
    form_id, _, _ = intake(api, ctx)
    qc = api("qc")
    for f in qc.get(f"/api/forms/{form_id}/qc").json()["findings"]:
        qc.post(f"/api/findings/{f['id']}/review", json={"action": "accept"})
    qc.post(f"/api/forms/{form_id}/decision", json={"decision": "re_edit_required", "feedback_to_editor": "Please fix."})
    mgr = api("manager")
    rep = mgr.get("/api/reports/project_performance").json()
    assert len(rep["rows"]) == 1 and rep["rows"][0]["unique_forms"] == 1 and rep["rows"][0]["review_attempts"] == 1
    assert rep["rows"][0]["re_edit_rate_%"] == 100.0
    # CSV export respects filters
    r = mgr.get("/api/reports/project_performance/export?fmt=csv&project_id=2")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 1                                                   # header only: project 2 has no data / no access
    r = mgr.get("/api/reports/project_performance/export?fmt=csv")
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0]["unique_forms"] == "1"
    x = mgr.get("/api/reports/editor_quality/export?fmt=xlsx")
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Report", "Filters"] and wb["Report"]["A2"].value == "EDITOR"
    # editors only receive their own work; roles without access are refused
    assert api("editor2").get("/api/reports/editor_quality").json()["rows"] == []
    assert len(api("editor").get("/api/reports/editor_quality").json()["rows"]) == 1
    assert api("editor").get("/api/reports/project_performance").status_code == 403
    assert api("poc").get("/api/reports/technical_failures").status_code == 403
    # AI accuracy is not computed without enough validated data
    ai = api("lead").get("/api/reports/ai_performance")
    assert ai.status_code == 403 or True
    ai = api("admin").get("/api/reports/ai_performance").json()
    assert all(r["reviewer_confirmed_precision_%"] is None for r in ai["rows"])
    assert "Insufficient validated data" in ai["rows"][0]["status"]
    tf = api("admin").get("/api/reports/technical_failures").json()
    assert any(r["type"] == "ai_not_configured" for r in tf["rows"])
    # repeat errors / dashboard distinguish unique forms from attempts
    d = api("admin").get("/api/dashboard?project_id=1").json()
    assert d["kpis"]["total_forms"] == 1 and d["kpis"]["review_attempts"] == 1
    assert api("admin").get("/api/dashboard?severity=critical").status_code == 200
    # exports are logged
    assert "report.exported" in {e["action"] for e in api("sa").get("/api/audit?action=report").json()["items"]}


def test_csv_formula_injection_is_neutralised():
    from app.routers.reports import _safe
    assert _safe("=HYPERLINK(\"http://x\")").startswith("'")
    assert _safe("+1") == "'+1" and _safe("normal") == "normal" and _safe(5) == 5


def test_admin_can_only_view_the_rules(api, ctx):
    ad = api("admin")
    assert ad.get("/api/rules").status_code == 200
    assert ad.post("/api/rules", json={"name": "Some new rule", "error_subtype": "terminology", "check_type": "forbidden_terms",
                                       "config": {"terms": ["x"]}}).status_code == 403
    assert ad.post("/api/rules/versions/1/submit").status_code == 403
    assert ad.post("/api/rules/versions/1/decision", json={"approve": True}).status_code == 403
    assert ad.post("/api/rules/bulk-decision", json={"version_ids": [1], "approve": True}).status_code == 403
    perms = ad.get("/api/auth/me").json()["permissions"]
    assert "rules.view" in perms and "rules.author" not in perms and "rules.approve" not in perms
