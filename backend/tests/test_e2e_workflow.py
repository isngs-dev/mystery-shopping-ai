"""Definition-of-done journey: upload pair -> real pipeline -> evidence-linked findings -> human review ->
decision -> history -> dashboard."""
from app.sample_docs import make_pair

from .helpers import intake, upload


def finding_by(findings, **kw):
    return [f for f in findings if all(str(v).lower() in str(f.get(k, "")).lower() for k, v in kw.items())]


def test_full_review_cycle(api, ctx):
    form_id, expected, _ = intake(api, ctx)
    qc = api("qc")
    ws = qc.get(f"/api/forms/{form_id}/qc").json()
    assert ws["form"]["status"] == "qc_review"
    run = ws["run"]
    assert run["status"] == "completed_with_warnings"          # AI not configured => honest warning, not 'completed'
    assert run["ai_status"] == "not_configured"
    assert any("AI analysis was NOT performed" in w for w in run["stats"]["warnings"])
    assert run["rule_versions"], "exact rule versions must be recorded"
    findings = ws["findings"]
    # missed correction vs new error, with real evidence + page numbers
    missed = finding_by(findings, type="missed_correction", edited_excerpt="recieved")
    assert missed and missed[0]["raw_page"] == 1 and missed[0]["edited_page"] == 1 and missed[0]["raw_boxes"] and missed[0]["edited_boxes"]
    assert missed[0]["rule_label"].startswith("GEN-SPELL v1")
    assert "received" in missed[0]["suggested_correction"]
    new = finding_by(findings, type="new_error", edited_excerpt="registar")
    assert new
    amount = finding_by(findings, error_subtype="meaning_changed", edited_excerpt="98.99")      # amount changed
    assert amount and amount[0]["category"] == "contradiction" and amount[0]["points"] == 20
    assert finding_by(findings, error_subtype="meaning_changed", title="Content removed")        # sentence removed
    assert not finding_by(findings, title="Repeated word")                                       # not on the QC error table => never checked
    table = {"grammar", "missed_instruction", "contradiction", "wrong_location"}
    assert all(f["category"] in table and f["error_subtype"] for f in findings), "every finding sits on the fixed error table"
    assert ws["score"]["score"] == 100 and ws["score"]["deductions"] == []                       # nothing confirmed yet
    # the raw typo "mee" was corrected by the editor and must NOT be flagged
    assert not finding_by(findings, edited_excerpt=" mee ")

    # cannot approve with unreviewed findings
    r = qc.post(f"/api/forms/{form_id}/decision", json={"decision": "approved"})
    assert r.status_code == 409 and "not been reviewed" in r.json()["detail"]
    # review everything: accept real errors, reject one as false positive
    for i, f in enumerate(findings):
        action = "reject" if i == 0 else "accept"
        assert qc.post(f"/api/findings/{f['id']}/review", json={"action": action, "comment": "checked"}).status_code == 200
    r = qc.post(f"/api/forms/{form_id}/decision", json={"decision": "approved", "acknowledge_warnings": True})
    assert r.status_code == 409 and "confirmed as errors" in r.json()["detail"]
    sc = qc.get(f"/api/forms/{form_id}/qc").json()["score"]            # Grammar 5 + Contradiction 20 confirmed => 100 - 25
    assert sc["score"] == 100 - sum(d["points"] for d in sc["deductions"]) and {d["category"] for d in sc["deductions"]} >= {"grammar", "contradiction"}
    r = qc.post(f"/api/forms/{form_id}/decision", json={"decision": "re_edit_required", "feedback_to_editor": "Fix spelling and restore the amount."})
    assert r.status_code == 200, r.text
    assert r.json()["form"]["status"] == "re_edit_required"

    # editor sees actionable feedback + confirmed findings only
    ed = api("editor")
    fd = ed.get(f"/api/forms/{form_id}").json()
    assert fd["confirmed_findings"] and any(c["kind"] == "feedback" for c in fd["comments"])
    assert ed.get(f"/api/forms/{form_id}/qc").status_code == 403
    assert ed.post(f"/api/forms/{form_id}/decision", json={"decision": "approved"}).status_code == 403

    # resubmission under the same Form ID; historical documents preserved
    assert ed.post(f"/api/forms/{form_id}/start").status_code == 200
    raw, clean, _ = make_pair(1, "clean")
    r = upload(ed, ctx["p1"], "FORM001_2.pdf", clean)
    res = r.json()["results"][0]
    assert res["ok"] and res["version"] == 2
    detail = ed.get(f"/api/forms/{form_id}").json()
    names = sorted(d["stored_name"] for d in detail["documents"])
    assert names[0] == "FORM001_1.pdf" and names[1] == "FORM001_2.pdf" and names[2].startswith("FORM001_2_r2_")
    assert len(detail["attempts"]) == 1                                                  # earlier review attempt preserved

    ws2 = qc.get(f"/api/forms/{form_id}/qc").json()
    assert ws2["run"]["attempt_no"] == 2 and ws2["can_review"]
    assert not [f for f in ws2["findings"] if f["category"] in ("grammar", "contradiction")]
    for f in ws2["findings"]:
        qc.post(f"/api/findings/{f['id']}/review", json={"action": "reject"})
    r = qc.post(f"/api/forms/{form_id}/decision", json={"decision": "approved", "summary": "Looks good", "acknowledge_warnings": True})
    assert r.status_code == 200, r.text

    # dashboard reflects unique forms vs attempts
    k = api("admin").get("/api/dashboard").json()["kpis"]
    assert k["total_forms"] == 1 and k["approved"] == 1 and k["review_attempts"] == 2 and k["resubmissions"] == 1
    assert k["first_pass_approval_rate"] == 0.0 and k["re_edit_rate"] == 100.0

    # audit trail covers key events
    actions = {e["action"] for e in api("sa").get("/api/audit?limit=500").json()["items"]}
    for a in ("document.uploaded", "document.resubmitted", "qc.run_completed", "qc.decision_saved", "finding.reviewed", "form.editor_assigned"):
        assert a in actions, a


def test_raw_is_immutable_and_duplicates_rejected(api, ctx):
    form_id, _, (raw, edited) = intake(api, ctx)
    mgr = api("manager")
    r = upload(mgr, ctx["p1"], "FORM001_1.pdf", raw)
    assert r.json()["failed"] == 1 and "Duplicate Form ID" in r.json()["results"][0]["error"]
    r = upload(api("editor"), ctx["p1"], "FORM001_2.pdf", edited)                       # identical resubmission
    assert "Identical" in r.json()["results"][0]["error"]
    from app.models import Document
    from app.db import SessionLocal
    from app.storage import get_storage
    import hashlib
    db = SessionLocal()
    d = db.query(Document).filter_by(form_id=form_id, kind="raw").one()
    assert hashlib.sha256(get_storage().get(d.storage_key)).hexdigest() == d.sha256
    try:
        get_storage().put_immutable(d.storage_key, b"overwrite")
        assert False, "overwrite should fail"
    except FileExistsError:
        pass
    db.close()


def test_filename_and_pdf_validation_goes_to_exception_queue(api, ctx):
    raw, edited, _ = make_pair(2, "clean")
    mgr = api("manager")
    r = upload(mgr, ctx["p1"], "report final.pdf", raw)
    assert "naming convention" in r.json()["results"][0]["error"]
    r = upload(mgr, ctx["p1"], "FORM777_1.pdf", b"not a pdf at all")
    assert r.json()["failed"] == 1
    r = upload(mgr, ctx["p1"], "FORM778_1.pdf", b"%PDF-1.4\ngarbage that cannot be parsed")
    assert r.json()["failed"] == 1
    r = upload(api("editor"), ctx["p1"], "FORM779_2.pdf", edited)                       # editor not assigned / no raw
    assert r.json()["failed"] == 1
    items = mgr.get("/api/repository/exceptions").json()["items"]
    types = {i["issue_type"] for i in items}
    assert {"naming_error", "invalid_pdf"} <= types or {"naming_error", "unreadable"} <= types
    # an explicit Form ID + type may be supplied for a badly named file
    r = upload(mgr, ctx["p1"], "report final.pdf", raw, form_code="form880", kind="raw")
    res = r.json()["results"][0]
    assert res["ok"] and res["logical_name"] == "FORM880_1.pdf" and res["warnings"]


def test_missing_pair_detection(api, ctx):
    form_id, _, _ = intake(api, ctx, code="FORM010", submit=False)
    r = api("manager").get("/api/forms?pair=missing_edited").json()
    assert r["total"] == 1 and r["items"][0]["pair_status"] == "missing_edited"
    assert api("manager").get("/api/forms?pair=complete").json()["total"] == 0
    assert api("manager").get("/api/forms?q=form010").json()["total"] == 1
    assert api("manager").get("/api/forms?q=nothing-here").json()["total"] == 0


def test_scanned_pdf_without_ocr_is_reported_not_faked(api, ctx):
    from app.services.extraction import ocr_status
    from app.sample_docs import make_scanned_pdf
    if ocr_status()["available"]:
        return  # OCR installed: covered by manual verification
    raw, edited, _ = make_pair(3, "clean")
    mgr = api("manager")
    form_id = upload(mgr, ctx["p1"], "FORM020_1.pdf", make_scanned_pdf()).json()["results"][0]["form_id"]
    mgr.post(f"/api/forms/{form_id}/assign", json={"editor_id": ctx["users"]["editor"]})
    ed = api("editor")
    ed.post(f"/api/forms/{form_id}/start")
    upload(ed, ctx["p1"], "FORM020_2.pdf", edited)
    d = mgr.get(f"/api/forms/{form_id}").json()
    assert d["status"] == "manual_review_required"
    run = d["runs"][0]
    assert run["status"] == "failed" and "OCR" in run["error"] or "Manual review" in run["error"]
    raw_doc = [x for x in d["documents"] if x["kind"] == "raw"][0]
    assert raw_doc["extraction_status"] == "ocr_unavailable"
