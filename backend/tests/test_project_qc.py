"""A project handed to a QC lead: every form gets analysed and lands in her queue, with a progress view and reports."""
import io
import zipfile

from app.db import SessionLocal
from app.models import Finding, Form, QCRun
from app.sample_docs import make_pair

from .helpers import upload


def _forget_analysis(form_id):
    """Simulate a form whose edited PDF arrived but whose QC has not been run yet."""
    db = SessionLocal()
    for r in db.query(QCRun).filter(QCRun.form_id == form_id).all():
        db.query(Finding).filter(Finding.run_id == r.id).delete()
        db.delete(r)
    db.commit()
    db.close()


def _project(api, ctx):
    """FORM001 + FORM002 are complete pairs without any analysis, FORM003 only has its RAW."""
    ids = []
    mgr, ed = api("manager"), api("editor")
    for n, code in enumerate(("FORM001", "FORM002", "FORM003"), 1):
        raw, edited, _ = make_pair(n, "mixed")
        form_id = upload(mgr, ctx["p1"], f"{code}_1.pdf", raw).json()["results"][0]["form_id"]
        ids.append(form_id)
        if code != "FORM003":
            assert mgr.post(f"/api/forms/{form_id}/assign", json={"editor_id": ctx["users"]["editor"]}).status_code == 200
            assert ed.post(f"/api/forms/{form_id}/start").status_code == 200
            assert upload(ed, ctx["p1"], f"{code}_2.pdf", edited).json()["uploaded"] == 1
            _forget_analysis(form_id)
    return ids


def test_assigning_a_project_queues_qc_for_every_form(api, ctx):
    f1, f2, f3 = _project(api, ctx)
    mgr, qc = api("manager"), api("qc")
    # not handed over yet: nothing is in the reviewer's queue
    assert qc.get("/api/forms?mine=true").json()["total"] == 0
    assert qc.get("/api/qc/projects?mine=true").json() == []

    r = mgr.put(f"/api/qc/projects/{ctx['p1']}/reviewer", json={"reviewer_id": ctx["users"]["qc"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["forms"] == 3 and out["assigned"] == 3 and out["reviewer"]["name"] == "QC"
    assert out["analysis"]["queued"] == 2 and out["analysis"]["waiting_edited"] == 1

    # the reviewer now owns every form, QC has run on the complete pairs, the RAW-only form waits for its edit
    mine = qc.get("/api/forms?mine=true").json()
    assert mine["total"] == 3 and {f["assigned_reviewer"]["name"] for f in mine["items"]} == {"QC"}
    prog = qc.get(f"/api/qc/projects/{ctx['p1']}").json()
    stages = {r["form_code"]: r for r in prog["forms"]}
    assert prog["reviewer"]["name"] == "QC"
    assert stages["FORM001"]["stage"] == "to_review" and stages["FORM001"]["findings"]["total"] > 0 and stages["FORM001"]["review"]["pending"] > 0
    assert stages["FORM002"]["stage"] == "to_review"
    assert stages["FORM003"]["stage"] == "waiting_edited"
    assert prog["totals"]["pending_findings"] == stages["FORM001"]["review"]["pending"] + stages["FORM002"]["review"]["pending"]
    assert qc.get("/api/qc/projects?mine=true").json()[0]["to_review"] == 2
    assert api("qc2").get("/api/qc/projects?mine=true").json() == []

    # a second assignment does not analyse again
    again = mgr.put(f"/api/qc/projects/{ctx['p1']}/reviewer", json={"reviewer_id": ctx["users"]["qc"]}).json()
    assert again["analysis"]["queued"] == 0 and again["analysis"]["already_done"] == 2 and again["assigned"] == 0

    # the reviewer reviews by hand; the form moves on to "decided"
    for f in qc.get(f"/api/forms/{f1}/qc").json()["findings"]:
        assert qc.post(f"/api/findings/{f['id']}/review", json={"action": "accept"}).status_code == 200
    assert qc.post(f"/api/forms/{f1}/decision", json={"decision": "re_edit_required", "feedback_to_editor": "Fix the errors."}).status_code == 200
    prog = qc.get(f"/api/qc/projects/{ctx['p1']}").json()
    assert {r["form_code"]: r["stage"] for r in prog["forms"]}["FORM001"] == "decided" and prog["totals"]["decided"] == 1

    # a form that arrives later follows the project to the same reviewer
    raw, _, _ = make_pair(4, "mixed")
    new_id = upload(mgr, ctx["p1"], "FORM004_1.pdf", raw).json()["results"][0]["form_id"]
    assert qc.get(f"/api/forms/{new_id}").json()["assigned_reviewer"]["name"] == "QC"


def test_project_reports(api, ctx):
    f1, f2, f3 = _project(api, ctx)
    api("manager").put(f"/api/qc/projects/{ctx['p1']}/reviewer", json={"reviewer_id": ctx["users"]["qc"]})
    qc = api("qc")
    pdf = qc.get(f"/api/qc/projects/{ctx['p1']}/report")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and "attachment" in pdf.headers["content-disposition"]
    z = qc.get(f"/api/qc/projects/{ctx['p1']}/reports.zip")
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "00_Project_summary.pdf" in names and sum(n.startswith("QC_Report_FORM") for n in names) == 2     # FORM003 has no analysis
    assert api("editor").get(f"/api/qc/projects/{ctx['p1']}/report").status_code == 403
    assert api("qc2").get(f"/api/qc/projects/{ctx['p2']}/report").status_code == 404                        # outside their projects
    assert api("poc").get(f"/api/qc/projects/{ctx['p1']}/report").status_code == 403


def test_who_may_hand_over_a_project(api, ctx):
    _project(api, ctx)
    body = {"reviewer_id": ctx["users"]["qc"]}
    url = f"/api/qc/projects/{ctx['p1']}/reviewer"
    for who in ("qc", "editor", "viewer", "owner"):
        assert api(who).put(url, json=body).status_code == 403
    assert api("manager").put(url, json={"reviewer_id": ctx["users"]["editor"]}).status_code == 422        # an editor cannot own a project's QC
    assert api("manager").put(url, json={"reviewer_id": 99999}).status_code == 404
    assert api("admin").put(url, json={"reviewer_id": ctx["users"]["qc2"]}).status_code == 200
    assert api("sa").put(url, json={"reviewer_id": ctx["users"]["qc"]}).status_code == 200
    # the second reviewer no longer owns the forms
    assert api("qc2").get("/api/forms?mine=true").json()["total"] == 0
    assert api("qc").get("/api/forms?mine=true").json()["total"] == 3
    # the manager can take a project herself (she is a QC lead too)
    assert api("manager").put(url, json={"reviewer_id": ctx["users"]["manager"]}).status_code == 200
    # outside their projects
    assert api("manager").put(f"/api/qc/projects/{ctx['p2']}/reviewer", json=body).status_code == 404
    # clearing the owner keeps the forms where they are
    assert api("sa").put(url, json={"reviewer_id": None}).status_code == 200
    assert api("manager").get("/api/forms?mine=true").json()["total"] == 3


def test_reviewer_never_gets_work_they_edited(api, ctx):
    f1, f2, f3 = _project(api, ctx)
    db = SessionLocal()
    db.get(Form, f3).assigned_editor_id = ctx["users"]["qc"]          # this reviewer also edited FORM003
    db.commit()
    db.close()
    out = api("manager").put(f"/api/qc/projects/{ctx['p1']}/reviewer", json={"reviewer_id": ctx["users"]["qc"]}).json()
    assert out["conflicts"] == ["FORM003"] and out["assigned"] == 2
    assert api("qc").get("/api/forms?mine=true").json()["total"] == 3      # FORM003 still shows because she is its editor, not its reviewer
    assert api("qc").get(f"/api/forms/{f3}").json()["assigned_reviewer"] is None


def test_run_missing_retries_failed_and_skips_finished(api, ctx):
    f1, f2, f3 = _project(api, ctx)
    mgr = api("manager")
    r = mgr.post(f"/api/qc/projects/{ctx['p1']}/run")
    assert r.status_code == 202 and r.json()["queued"] == 2 and r.json()["waiting_edited"] == 1
    again = mgr.post(f"/api/qc/projects/{ctx['p1']}/run").json()
    assert again["queued"] == 0 and again["already_done"] == 2
    # a failed analysis is retried
    db = SessionLocal()
    run = db.query(QCRun).filter(QCRun.form_id == f1).order_by(QCRun.id.desc()).first()
    run.status, run.error = "failed", "boom"
    db.commit()
    db.close()
    assert mgr.post(f"/api/qc/projects/{ctx['p1']}/run").json()["queued"] == 1
    assert api("viewer").post(f"/api/qc/projects/{ctx['p1']}/run").status_code == 403


def test_a_drive_folder_belongs_to_the_person_it_is_named_after(api, ctx):
    from app.models import Project, ProjectReviewer, UserProjectAccess
    from app.services.project_qc import adopt_folder_owner, folder_owner
    db = SessionLocal()
    try:
        assert folder_owner(db, "Store Visits_QC2").name == "QC2" and folder_owner(db, "Deli_Manager").name == "MANAGER"
        assert folder_owner(db, "Deli_Nobody") is None and folder_owner(db, "") is None
        p = Project(client_id=db.get(Project, ctx["p1"]).client_id, name="Branch_QC2", folder="Branch_QC2")
        db.add(p)
        db.flush()
        owner = adopt_folder_owner(db, p, ctx["users"]["manager"])
        db.commit()
        assert owner.id == ctx["users"]["qc2"] and db.get(ProjectReviewer, p.id).reviewer_id == owner.id
        assert db.get(UserProjectAccess, (owner.id, p.id)) is not None
        assert db.get(UserProjectAccess, (ctx["users"]["qc"], p.id)) is None          # the other QC lead does not see it
    finally:
        db.close()


def test_project_excel_report_follows_the_tracker_layout(api, ctx):
    import io

    from openpyxl import load_workbook

    f1, f2, f3 = _project(api, ctx)
    mgr, qc = api("manager"), api("qc")
    assert mgr.put(f"/api/qc/projects/{ctx['p1']}/reviewer", json={"reviewer_id": ctx["users"]["qc"]}).status_code == 200
    r = qc.get(f"/api/qc/projects/{ctx['p1']}/report.xlsx")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"] and ".xlsx" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["QC Report", "Findings", "Summary"]
    ws = wb["QC Report"]
    header = [c.value for c in ws[1]]
    assert header == ["Input date", "Form", "Class / Location", "Shop ID", "Shop Date", "Input Status", "Editor", "Editor Name", "Transaction Date",
                      "OPS Status", "Comments to shopper", "Editing Type", "QC Member", "QC Date", "QC Status", "Feedback", "Client Name",
                      "Client Escalation", "Company's Name", "Error Category", "Platform", "Incomplete Status", "Duplicates"]
    rows = {ws.cell(i, 4).value: {h: ws.cell(i, k + 1).value for k, h in enumerate(header)} for i in range(2, ws.max_row + 1)}
    assert set(rows) == {"FORM001", "FORM002", "FORM003"}                       # one row per form of the project
    a, c = rows["FORM001"], rows["FORM003"]
    assert a["QC Member"] == "QC" and a["QC Status"] == "Request Revision" and a["Editing Type"] == "Manually Edited"
    assert a["Feedback"].startswith("QC score ") and "Contradiction" in a["Feedback"] and a["Client Name"] == "Acme Retail"
    assert set(a["Error Category"].split(", ")) <= {"Grammar", "Missed Instruction", "Contradication", "Wrong Location/ Put on Different category"}
    assert a["Error Category"]
    assert c["QC Status"] in (None, "") and "Waiting for the edited PDF" in c["Feedback"]                  # RAW only: not analysed yet
    fs = wb["Findings"]
    assert fs.max_row > 5 and [x.value for x in fs[1]][:5] == ["Shop ID", "QC Member", "Category", "Marks", "Error (QC error table)"]
    assert any(ws_.value == "Average automated QC score" for ws_ in wb["Summary"]["A"])
    # editors never receive it; the download is audited
    assert api("editor").get(f"/api/qc/projects/{ctx['p1']}/report.xlsx").status_code == 403
    assert "report.project_qc_excel_downloaded" in {e["action"] for e in api("sa").get("/api/audit?action=report").json()["items"]}


def test_excel_cells_never_start_a_formula():
    from app.services.project_excel import _safe

    assert _safe("=HYPERLINK(\"http://evil\")") == "'=HYPERLINK(\"http://evil\")" and _safe("@SUM(A1)").startswith("'") and _safe("plain") == "plain"
