import io

import pymupdf
import pytest

from app.config import get_settings
from app.sample_docs import make_pair, make_scanned_pdf
from app.services.ai import set_provider_override

from .helpers import upload
from .test_ai import FakeProvider


def pdf_text(api_client, doc_id):
    r = api_client.get(f"/api/documents/{doc_id}/file")
    assert r.status_code == 200
    from app.services.extraction import extract_pdf
    return " ".join(p.text for p in extract_pdf(r.content).pages).replace("\n", " ")  # the platform's own reading order


@pytest.fixture
def auto_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_edit_on_upload", True)


def test_raw_upload_is_ai_edited_and_sent_to_qc(api, ctx, auto_on):
    raw, _, _ = make_pair(1, "mixed")
    mgr = api("manager")
    r = upload(mgr, ctx["p1"], "FORM100_1.pdf", raw)
    res = r.json()["results"][0]
    assert res["ok"] and res["ai_edit_queued"]
    fid = res["form_id"]

    form = mgr.get(f"/api/forms/{fid}").json()
    assert form["status"] == "qc_review"                                  # AI edit -> straight to QC, nothing auto-approved
    assert form["assigned_editor"]["name"] == "AI Editor"
    docs = {d["kind"]: d for d in form["documents"]}
    assert docs["raw"]["origin"] == "human" and docs["edited"]["origin"] == "ai_assisted"
    assert docs["edited"]["stored_name"] == "FORM100_2.pdf"

    # the RAW stays byte-identical; the edited PDF contains the corrections and leaves facts alone
    assert pdf_text(mgr, docs["raw"]["id"]).count("recieved") == 1
    edited_text = pdf_text(mgr, docs["edited"]["id"])
    assert "received goods area" in edited_text and "recieved" not in edited_text
    assert "help me today" in edited_text and "$89.99" in edited_text and "10:30 AM" in edited_text

    log = mgr.get(f"/api/forms/{fid}/ai-edits").json()[0]
    assert log["status"] == "completed" and log["engine"] == "rules" and log["result_document_id"] == docs["edited"]["id"]
    pairs = {(e["original"], e["replacement"]) for e in log["edits"]}
    assert ("recieved", "received") in pairs and ("mee", "me") in pairs
    assert log["ai_status"] == "not_configured" and "built-in" in log["message"]       # honest: no LLM was used
    assert log["stats"]["verification"].startswith("Verified")
    # every correction carries real coordinates on BOTH PDFs so QC can see exactly where it changed
    for e in log["edits"]:
        assert e["raw_boxes"] and e["edited_boxes"] and e["raw_boxes"][0]["page"] == e["edited_boxes"][0]["page"] == 1
    me = next(e for e in log["edits"] if e["replacement"] == "me")
    assert abs(me["raw_boxes"][0]["y0"] - me["edited_boxes"][0]["y0"]) < 3 and abs(me["raw_boxes"][0]["x0"] - me["edited_boxes"][0]["x0"]) < 3

    # QC compares RAW vs the AI-edited PDF; fixed typos are NOT reported as missed corrections
    ws = api("qc").get(f"/api/forms/{fid}/qc").json()
    assert ws["run"]["status"] in ("completed", "completed_with_warnings")
    assert ws["run"]["stats"]["changes"] >= 2
    assert not [f for f in ws["findings"] if f["type"] == "missed_correction" and "recieved" in (f["edited_excerpt"] or "")]
    # and it still needs a human decision
    assert api("qc").post(f"/api/forms/{fid}/decision", json={"decision": "approved"}).status_code in (409, 403)
    actions = {e["action"] for e in api("sa").get("/api/audit?limit=300").json()["items"]}
    assert {"ai_edit.queued", "ai_edit.completed", "document.uploaded"} <= actions


def test_manual_ai_edit_and_permissions(api, ctx):
    raw, _, _ = make_pair(1, "mixed")
    mgr = api("manager")
    fid = upload(mgr, ctx["p1"], "FORM101_1.pdf", raw).json()["results"][0]["form_id"]      # auto edit disabled in tests
    assert mgr.get(f"/api/forms/{fid}").json()["edited_versions"] == 0
    mgr.post(f"/api/forms/{fid}/assign", json={"editor_id": ctx["users"]["editor"], "reviewer_id": ctx["users"]["qc"]})
    assert api("qc").post(f"/api/forms/{fid}/ai-edit").status_code == 403                  # QC stays independent of editing
    assert api("viewer").post(f"/api/forms/{fid}/ai-edit").status_code == 403
    assert api("editor2").post(f"/api/forms/{fid}/ai-edit").status_code == 404              # not their form
    r = api("editor").post(f"/api/forms/{fid}/ai-edit")
    assert r.status_code == 202
    d = mgr.get(f"/api/forms/{fid}").json()
    assert d["status"] == "qc_review" and d["edited_versions"] == 1
    assert api("editor").post(f"/api/forms/{fid}/ai-edit").status_code == 409               # already in QC review


def test_nothing_to_fix_submits_raw_unchanged_and_reedit_reports_no_changes(api, ctx):
    _, clean, _ = make_pair(1, "clean")
    mgr = api("manager")
    fid = upload(mgr, ctx["p1"], "FORM102_1.pdf", clean).json()["results"][0]["form_id"]
    mgr.post(f"/api/forms/{fid}/assign", json={"editor_id": ctx["users"]["editor"], "reviewer_id": ctx["users"]["qc"]})
    assert mgr.post(f"/api/forms/{fid}/ai-edit").status_code == 202
    log = mgr.get(f"/api/forms/{fid}/ai-edits").json()[0]
    assert log["status"] == "completed" and log["edits"] == []
    assert mgr.get(f"/api/forms/{fid}").json()["status"] == "qc_review"
    # reviewer sends it back; with nothing left for the AI to fix it says so instead of faking a new version
    qc = api("qc")
    for f in qc.get(f"/api/forms/{fid}/qc").json()["findings"]:
        qc.post(f"/api/findings/{f['id']}/review", json={"action": "reject"})
    assert qc.post(f"/api/forms/{fid}/decision", json={"decision": "re_edit_required", "feedback_to_editor": "Please re-check.", "acknowledge_warnings": True}).status_code == 200
    assert mgr.post(f"/api/forms/{fid}/ai-edit").status_code == 202
    again = mgr.get(f"/api/forms/{fid}/ai-edits").json()[0]
    assert again["status"] == "no_changes"
    assert mgr.get(f"/api/forms/{fid}").json()["edited_versions"] == 1


def test_scanned_pdf_cannot_be_ai_edited(api, ctx, auto_on):
    from app.services.extraction import ocr_status
    if ocr_status()["available"]:
        pytest.skip("OCR installed; scanned path not applicable")
    mgr = api("manager")
    fid = upload(mgr, ctx["p1"], "FORM103_1.pdf", make_scanned_pdf()).json()["results"][0]["form_id"]
    f = mgr.get(f"/api/forms/{fid}").json()
    assert f["status"] == "pending" and f["edited_versions"] == 0
    log = mgr.get(f"/api/forms/{fid}/ai-edits").json()[0]
    assert log["status"] == "failed" and "no readable text layer" in log["message"]


def test_llm_edits_are_verified_and_unsafe_ones_rejected(api, ctx, auto_on):
    fake = FakeProvider({"findings": [], "edits": [
        {"original": "asked how she could help mee today", "replacement": "asked how she could help me today", "reason": "typo", "category": "spelling", "confidence": 0.95},
        {"original": "priced at $89.99 and she demonstrated", "replacement": "priced at $98.99 and she demonstrated", "reason": "x", "category": "grammar", "confidence": 0.99},
        {"original": "the manager was extremely rude", "replacement": "the manager was polite", "reason": "x", "category": "grammar", "confidence": 0.99},
        {"original": "greeted by an associate", "replacement": "greeted by Jennifer the associate", "reason": "x", "category": "grammar", "confidence": 0.9},
        {"original": "The store was clean", "replacement": "The store was not clean", "reason": "x", "category": "grammar", "confidence": 0.9},
    ]})
    set_provider_override(fake)
    raw, _, _ = make_pair(1, "mixed")
    mgr = api("manager")
    fid = upload(mgr, ctx["p1"], "FORM104_1.pdf", raw).json()["results"][0]["form_id"]
    log = mgr.get(f"/api/forms/{fid}/ai-edits").json()[0]
    assert log["status"] == "completed" and log["engine"] == "ai+rules" and log["ai_status"] == "completed"
    assert {e["source"] for e in log["edits"]} == {"ai", "rules"}                        # AI fixed 'mee'; rules fixed 'recieved'
    reasons = " | ".join(s["reason"] for s in log["skipped"])
    assert "number" in reasons and "not found verbatim" in reasons and "proper words" in reasons and "negation" in reasons
    d = mgr.get(f"/api/forms/{fid}").json()
    text = pdf_text(mgr, [x for x in d["documents"] if x["kind"] == "edited"][0]["id"])
    assert "$89.99" in text and "Jennifer" not in text and "not clean" not in text and "help me today" in text
    assert "untrusted DATA" in fake.prompts[0][0] and "MINIMAL" in fake.prompts[0][0]


def test_unsafe_pdf_result_is_never_submitted(api, ctx, auto_on, monkeypatch):
    from app.services import auto_editor
    monkeypatch.setattr(auto_editor, "verify_result", lambda *a, **k: (False, "Safety check failed: simulated."))
    raw, _, _ = make_pair(1, "mixed")
    mgr = api("manager")
    fid = upload(mgr, ctx["p1"], "FORM105_1.pdf", raw).json()["results"][0]["form_id"]
    f = mgr.get(f"/api/forms/{fid}").json()
    assert f["status"] == "pending" and f["edited_versions"] == 0
    log = mgr.get(f"/api/forms/{fid}/ai-edits").json()[0]
    assert log["status"] == "failed" and "Safety check failed" in log["message"] and "human editor is needed" in log["message"]


def test_rule_editor_never_touches_facts():
    from app.services.auto_editor import rule_based_edits
    from app.services.compare import build_tokens
    from app.services.extraction import extract_pdf
    from app.sample_docs import _render
    pdf = _render(["I did not see a manager at 10:30 and paid $45.50 on 12 March at Riverside Mart.", "the cashier said no."])
    toks = build_tokens(extract_pdf(pdf).pages)
    ops, _ = rule_based_edits(toks)
    changed = " ".join(o.replacement for o in ops)
    assert "10:30" not in changed and "$45.50" not in changed and "not" not in changed
    assert any(o.replacement.startswith("The") for o in ops)                              # only capitalisation was fixed
