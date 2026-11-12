import io
import json
import zipfile

import httpx
import pymupdf
import pytest
from openpyxl import Workbook

from app.db import SessionLocal
from app.models import Client, Document, ExceptionItem, Form, Project, QCRun, UserProjectAccess
from app.sample_docs import _render
from app.services.drive_sources import GoogleDriveSource, SourceError
from sqlalchemy import select


def review_pdf(sid: str, narrative: str, smalltalk: str = "She asked how I was doing.") -> bytes:
    """Anonymised imitation of a survey-platform 'Admin Shop Review' print-out (header, boilerplate, narrative, reviewer panel)."""
    return _render([
        f"{sid} - Admin Shop Review", "Shop Date: 10/22/2026", "Shopper #478451: Test Shopper", "Shopper Phone #: 201-555-0100", f"Shop Ref#: {sid}",
        "General Information", "Please be sure to detail your interaction with the banker.",
        "Engage in conversation by making small talk during your transaction? Yes No", "If yes, please explain:", smalltalk,
        "Any additional comments on your overall experience?", narrative,
        "REVIEWER CONTROLS", "REPLY-TO: apcqcteam@example.com (reviewer's email)", f"TO: shopper{sid}@example.com", "Comments to shopper"])


RAW_N = "The teller didn't greet me and she charged $100 today . She said hi welcome to Valley."
ED_N = "The teller did not greet me and she charged $100 today . She said, hi welcome to Valley. She wasn't rude."


def tracker_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "October"
    ws.append(["Input date", "Form", "Class / Location", "Shop ID", "Shop Date", "Input Status", "Editor", "Editor Name", "Transaction Date", "OPS Status",
               "Comments to shopper", "QC Member", "QC Status", "Feedback", "Error Category"])
    ws.append(["10/1/2026", "Kids Empire", "KE3 Mesa (AZ US)", "5113895", "09/30/26", "Completed", "iSN Team", "Editor One", "10/1/2026", "Finalized", "10/1- Finalized-iSN"])
    ws.append(["10/2/2026", "Collegiate", "GAMD Annapolis", "5115022", "09/28/26", "Completed", "iSN Team", "Editor Two", "10/2/2026", "Incomplete", "Please clarify.", "Qc One", "Incomplete"])
    ws.append(["", "Login Details Platform Link", "", "", "", "", "", "", "", "", "ID: someone Password: hunter2"])          # credentials row must never be imported
    ws.append(["", "Error Categories", "", "Zions Bank: Authentication Call"])                                                    # reference row, not a tracker row
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def drive_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        top = "Mystery Shopping_QC/"
        z.writestr(top + "APC_Checklist.docx", b"PK-not-a-real-docx")
        z.writestr(top + "HOU guidelines.pdf", b"%PDF-1.4 guidelines placeholder")
        z.writestr(top + "HSB_Multi-Projects_Q4 2026.xlsx", tracker_xlsx())
        for i in range(5):
            sid = str(9900001 + i)
            z.writestr(f"{top}APC/Valley Bank_Geeta/{sid}_1.pdf", review_pdf(sid, RAW_N))
            z.writestr(f"{top}APC/Valley Bank_Geeta/{sid}_2.pdf", review_pdf(sid, ED_N))
        z.writestr(top + "APC/Valley Bank_Geeta/9900010_3.pdf", review_pdf("9900010", RAW_N))          # suffix with no defined meaning
        z.writestr(top + "APC/Valley Bank_Geeta/final report copy.pdf", review_pdf("9", RAW_N))        # bad name
        z.writestr(top + "APC/Valley Bank_Geeta/9900011_2.pdf", review_pdf("9900011", ED_N))           # edited without RAW
        for i, sid in enumerate(("9900021", "9900022", "9900023", "9900024")):
            z.writestr(f"{top}CXE/HOU_Geeta/{sid}_1.pdf", review_pdf(sid, "I recieved my order and the associate was kind."))   # RAW only -> AI edit
    return buf.getvalue()


def post_zip(sa, data=None, **form):
    return sa.post("/api/integrations/drive/import-zip", data={"ai_edit_missing": "true", "run_qc": "true", **form},
                   files=[("file", ("Mystery Shopping_QC.zip", data or drive_zip(), "application/zip"))])


ZIP_BYTES: dict = {}


@pytest.fixture
def imported(api, ctx):
    sa = api("manager")       # Drive import / sync belongs to the manager role (Geeta); `sa` below is that importing client
    ZIP_BYTES['b'] = drive_zip()
    r = post_zip(sa, ZIP_BYTES['b'])
    assert r.status_code == 202, r.text
    job = sa.get(f"/api/integrations/drive/jobs/{r.json()['id']}").json()
    assert job["status"] == "completed", job
    # QC has already run on everything; assigning a project only decides who reviews it (and gives that QC lead access)
    db = SessionLocal()
    pids = [p.id for p in db.query(Project).filter(Project.name.in_(["Valley Bank_Geeta", "HOU_Geeta"]))]
    db.close()
    for pid in pids:
        assert sa.put(f"/api/qc/projects/{pid}/reviewer", json={"reviewer_id": ctx["users"]["qc"]}).status_code == 200
    return sa, job


def test_zip_import_builds_hierarchy_runs_pipeline_and_flags_problems(imported):
    sa, job = imported
    st = job["stats"]
    assert st["clients_created"] == 2 and st["projects_created"] == 2
    assert st["raw_imported"] == 9 and st["edited_imported"] == 5 and st["qc_runs"] == 5 and st["ai_edited"] == 4          # QC never waits for an assignment
    assert st["missing_raw"] == 1 and st["naming_errors"] == 1 and st["unmapped_suffix_files"] == 1
    assert st["tracker_rows"] == 2                                              # credentials + reference rows were not imported
    assert st["rules_submitted"] == 15                                         # only checklist items that are on the QC error table (+ 2 page-furniture profiles)
    db = SessionLocal()
    names = {(c.name, p.name) for c in db.query(Client) for p in c.projects}
    assert {("APC", "Valley Bank_Geeta"), ("CXE", "HOU_Geeta")} <= names
    kinds = {e.issue_type for e in db.query(ExceptionItem)}
    assert {"unmapped_suffix", "naming_error", "missing_raw"} <= kinds
    f = db.query(Form).filter_by(form_code="9900001").one()
    assert {d.kind: d.stored_name for d in f.documents} == {"raw": "9900001_1.pdf", "edited": "9900001_2.pdf"} and f.status == "qc_review"
    hou = db.query(Form).filter_by(form_code="9900021").one()
    assert [d.origin for d in hou.documents if d.kind == "edited"] == ["ai_assisted"] and hou.status == "qc_review"
    raw = next(d for d in hou.documents if d.kind == "raw")
    from app.storage import get_storage
    import hashlib
    assert hashlib.sha256(get_storage().get(raw.storage_key)).hexdigest() == raw.sha256 and "recieved" in "".join(
        p.text for p in raw.pages)                                                # RAW untouched
    db.close()
    # the checklists/guidelines were recognised but nothing was copied from them blindly
    assert any("APC_Checklist.docx" in line for line in job["log"])


def test_credentials_in_source_documents_are_never_stored(imported):
    sa, _ = imported
    items = sa.get("/api/integrations/tracker?limit=200").json()["items"]
    assert {i["shop_id"] for i in items} == {"5113895", "5115022"}
    blob = json.dumps(items) + json.dumps(sa.get("/api/rules").json()) + json.dumps(sa.get("/api/integrations/drive/jobs").json())
    assert "hunter2" not in blob and "iSN2024" not in blob and "apcqcteam" not in blob.replace("apcqcteam@example.com", "")
    summ = sa.get("/api/integrations/tracker/summary").json()
    assert summ["total"] == 2 and {s["name"] for s in summ["by_status"]} == {"Finalized", "Incomplete"}


def test_import_is_idempotent(api, ctx, imported):
    sa, _ = imported
    # a freshly generated archive differs byte-for-byte (PDF timestamps), so the identical bytes are re-sent
    again = sa.get(f"/api/integrations/drive/jobs/{post_zip(sa, ZIP_BYTES['b']).json()['id']}").json()
    assert again["status"] == "completed" and again["stats"].get("raw_imported", 0) == 0 and again["stats"]["already_imported"] >= 9
    assert not again["stats"].get("raw_changed") and not again["stats"].get("edited_resubmitted")
    db = SessionLocal()
    assert db.query(Document).count() == 9 + 5 + 4                              # raw + human edited + AI edited, no duplicates
    db.close()


def test_rules_need_approval_then_apply_and_template_text_is_protected(api, ctx, imported):
    sa, _ = imported
    db = SessionLocal()
    form_id = db.query(Form).filter_by(form_code="9900001").one().id
    for p in db.query(Project).filter(Project.name.in_(["Valley Bank_Geeta", "HOU_Geeta"])):
        assert db.get(UserProjectAccess, (ctx["users"]["manager"], p.id)), "import should grant new projects to admin/manager roles"
        assert db.get(UserProjectAccess, (ctx["users"]["qc"], p.id)), "assigning a project gives the QC lead access to it"
    db.commit()
    db.close()
    qc = api("qc")
    labels = lambda: {f["rule_label"].split(" v")[0] for f in qc.get(f"/api/forms/{form_id}/qc").json()["findings"] if f["rule_label"]}      # noqa: E731
    assert not any(l.startswith("APC-") for l in labels())                       # pending rules are NOT applied yet

    pend = [r for r in api("owner").get("/api/rules?status=pending_approval").json()]
    assert {r["rule_key"] for r in pend} >= {"APC-AVOID-PHRASES", "CXE-ASSOCIATE-TERM", "APC-TEMPLATE"}
    assert not {"APC-NO-CONTRACTIONS", "APC-NO-AMOUNTS", "CXE-PRODUCT-QUESTION"} & {r["rule_key"] for r in pend}      # not on the error table => not imported
    assert sa.post("/api/rules/bulk-decision", json={"version_ids": [r["id"] for r in pend], "approve": True}).status_code == 403      # the importing manager can author rules but never approve them
    res = api("owner").post("/api/rules/bulk-decision", json={"version_ids": [r["id"] for r in pend], "approve": True}).json()
    assert len(res["done"]) == len(pend) and not res["refused"]
    assert api("owner").post("/api/rules/bulk-decision", json={"version_ids": [pend[0]["id"]], "approve": True}).json()["refused"][0]["reason"] == "not awaiting approval"

    assert qc.post(f"/api/forms/{form_id}/qc/run").status_code == 202
    ws = qc.get(f"/api/forms/{form_id}/qc").json()
    found = {(f["rule_label"].split(" v")[0], f["type"]) for f in ws["findings"] if f["rule_label"]}
    assert ("APC-AVOID-PHRASES", "missed_correction") in found                    # "today": Incorrect Terminology, left by the editor
    assert not any(l in {"APC-NO-AMOUNTS", "APC-NO-CONTRACTIONS"} for l, _ in found)       # retired: not part of the QC error table
    # shopper phone / e-mail in the page header and reviewer panel are template text; QC only ever reports the error table
    assert all(f["category"] in {"grammar", "missed_instruction", "contradiction", "wrong_location"} and f["error_subtype"] for f in ws["findings"])
    assert {"terminology", "extra_space"} <= {f["error_subtype"] for f in ws["findings"]}             # “today” and the “today .” space
    prot = ws["run"]["stats"]["protected_tokens"]
    assert prot["edited"] > 10 and prot["template_lines"] > 3 and "APC-TEMPLATE v1" in prot["profile"]


def test_qc_report_pdf_and_batch_zip(api, ctx, imported):
    sa, _ = imported
    db = SessionLocal()
    ids = [f.id for f in db.query(Form).filter(Form.form_code.in_(["9900001", "9900002"]))]
    for p in db.query(Project):
        for key in ("qc",):
            db.add(UserProjectAccess(user_id=ctx["users"][key], project_id=p.id)) if not db.get(UserProjectAccess, (ctx["users"][key], p.id)) else None
    db.commit()
    db.close()
    qc = api("qc")
    r = qc.get(f"/api/forms/{ids[0]}/qc-report")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert "attachment" in r.headers["content-disposition"] and "QC_Report_9900001" in r.headers["content-disposition"]
    text = " ".join(p.get_text() for p in pymupdf.open(stream=r.content, filetype="pdf"))
    assert "AUTOMATED QC REPORT" in text and "Findings in detail" in text and "RAW evidence" in text         # generated without any human step
    # after a human decision the same report becomes FINAL
    for f in qc.get(f"/api/forms/{ids[0]}/qc").json()["findings"]:
        qc.post(f"/api/findings/{f['id']}/review", json={"action": "accept"})
    assert qc.post(f"/api/forms/{ids[0]}/decision", json={"decision": "re_edit_required", "feedback_to_editor": "Fix the listed items."}).status_code == 200
    final = " ".join(p.get_text() for p in pymupdf.open(stream=qc.get(f"/api/forms/{ids[0]}/qc-report").content, filetype="pdf"))
    assert "FINAL" in final and "RE EDIT REQUIRED" in final.upper() and "Fix the listed items" in final
    z = qc.post("/api/reports/qc-batch", json={"form_ids": ids + [999999]})
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert z.headers["content-type"] == "application/zip" and sum(n.endswith(".pdf") for n in names) == 2 and "SKIPPED.txt" in names
    # roles that must not receive evidence-bearing reports
    assert api("editor").get(f"/api/forms/{ids[0]}/qc-report").status_code == 403
    assert api("viewer").get(f"/api/forms/{ids[0]}/qc-report").status_code == 403
    assert "report.qc_downloaded" in {e["action"] for e in api("sa").get("/api/audit?action=report").json()["items"]}


def test_import_permissions_and_safe_sources(api, ctx):
    for who in ("admin", "sa", "editor", "qc"):      # Drive import is for the manager only
        assert api(who).post("/api/integrations/drive/import-zip", files=[("file", ("x.zip", b"x", "application/zip"))]).status_code == 403
        assert api(who).get("/api/integrations/drive/status").status_code == 403
    assert api("qc").get("/api/integrations/tracker").status_code == 200 and api("editor").get("/api/integrations/tracker").status_code == 403
    sa = api("manager")
    assert sa.post("/api/integrations/drive/import-zip", files=[("file", ("notes.txt", b"x", "text/plain"))]).status_code == 422
    r = post_zip(sa, b"this is not a zip")
    assert sa.get(f"/api/integrations/drive/jobs/{r.json()['id']}").json()["status"] == "failed"
    assert sa.post("/api/integrations/drive/import-local", json={"path": "C:/Windows"}).status_code == 422          # outside IMPORT_LOCAL_ROOT
    assert sa.post("/api/integrations/drive/import-local", json={"path": "../../etc"}).status_code == 422
    st = sa.get("/api/integrations/drive/status").json()
    assert st["configured"] is False and "service account" in st["help"]
    assert sa.post("/api/integrations/drive/sync", json={"folder_id": "abc"}).status_code == 409                    # honest: not configured


def test_zip_slip_entries_are_ignored():
    from app.services.drive_sources import ZipSource
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "evil.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("../../evil.pdf", b"x")
        z.writestr("Root/ok/1_1.pdf", b"x")
    src = ZipSource(p)
    flat = [c.name for c in src.children(src.root())]
    assert "evil.pdf" not in flat and flat == ["ok"]


def test_google_drive_source_with_mocked_api():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        assert req.headers["authorization"] == "Bearer tok"
        path = req.url.path
        if path.endswith("/files/ROOT"):
            return httpx.Response(200, json={"name": "Mystery Shopping_QC"})
        if path.endswith("/files") and "ROOT" in req.url.params["q"]:
            return httpx.Response(200, json={"files": [{"id": "F1", "name": "APC", "mimeType": "application/vnd.google-apps.folder"},
                                                       {"id": "P1", "name": "3_1.pdf", "mimeType": "application/pdf", "size": "5"}]})
        if path.endswith("/files/P1") and req.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"%PDF-")
        if path.endswith("/files/DENIED"):
            return httpx.Response(403)
        return httpx.Response(404)

    src = GoogleDriveSource("ROOT", token_provider=lambda: "tok", client=httpx.Client(transport=httpx.MockTransport(handler)))
    root = src.root()
    kids = src.children(root)
    assert root.name == "Mystery Shopping_QC" and [(k.name, k.is_dir) for k in kids] == [("APC", True), ("3_1.pdf", False)]
    assert src.read(kids[1]) == b"%PDF-"
    with pytest.raises(SourceError, match="not shared|refused"):
        GoogleDriveSource("DENIED", token_provider=lambda: "tok", client=httpx.Client(transport=httpx.MockTransport(handler))).root()


def test_file_name_styles_used_in_drive():
    from app.services.drive_import import parse_pdf_name as p
    assert p("9910001_1.pdf") == ("9910001", 1) and p("3353196 - Admin Shop Review-2.pdf") == ("3353196", 2)
    assert p("131227-1.pdf") == ("131227", 1) and p("131227-2.pdf") == ("131227", 2)
    assert p("42386 - 1.pdf") == ("42386", 1) and p("3354395 -2.pdf") == ("3354395", 2)
    assert p("23695714 (1).pdf") == ("23695714", 1) and p("23695714 (2).pdf") == ("23695714", 2)
    assert p("3353722 - Admin Shop Review.pdf") == ("3353722", 1)          # unnumbered = the RAW, its edit is "3353722 - 2.pdf"
    assert p("notes.pdf") is None and p("Checklist - 2026.pdf") is None and p("12 - 1.pdf") is None
