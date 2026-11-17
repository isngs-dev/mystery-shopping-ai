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
    assert api("poc").post(f"/api/forms/{fid}/ai-edit").status_code == 403                  # the client's contact never edits (QC leads do: they are editors too)
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


def test_rule_editor_fixes_the_grammar_rules_of_the_qc_table():
    """Punctuation (doubled marks), Extra Space/No space (missing / stray space) and Spelling are edited; links and numbers are left alone."""
    from app.services.auto_editor import rule_based_edits
    from app.services.compare import build_tokens
    from app.services.extraction import extract_pdf
    from app.sample_docs import _render
    pdf = _render(["The store was clean.The associate was kind .. I recieved my order,quickly and paid 10.50 at https://example.com/a..b today."])
    ops, _ = rule_based_edits(build_tokens(extract_pdf(pdf).pages))
    out = {o.original: (o.replacement, o.category) for o in ops}
    assert out["clean.The"] == ("clean. The", "spacing")                                   # No space
    assert out["kind .."] == ("kind.", "punctuation")                                        # doubled punctuation (stray space dropped too)
    assert out["recieved"][1] == "spelling"
    assert out["order,quickly"] == ("order, quickly", "spacing")
    assert not any("10.50" in o.original or "example.com" in o.original for o in ops)        # figures and links untouched


def test_raw_only_forms_are_edited_and_qcd_by_the_sync_without_waiting(api, ctx):
    """DRIVE_AI_EDIT_RAW_ONLY (default): a RAW that arrives without an edited file is edited at once and goes to QC."""
    import shutil
    from pathlib import Path

    from sqlalchemy import select

    from app.db import SessionLocal, utcnow
    from app.models import Form, QCRun
    from app.services import drive_sync
    from .test_drive_sync import assign_to_qc, enable, job, review_pdf

    s = get_settings()
    assert s.drive_ai_edit_raw_only is True and s.drive_ai_edit_after_hours == 0
    root = Path(s.import_local_root) / "drive_raw_only"
    shutil.rmtree(root, ignore_errors=True)
    (root / "APC" / "Valley Bank_Geeta").mkdir(parents=True)
    (root / "APC" / "Valley Bank_Geeta" / "9920001_1.pdf").write_bytes(review_pdf("9920001", "The store was clean.The teller recieved me ."))
    assign_to_qc(ctx)
    mgr = api("manager")
    r = mgr.put("/api/integrations/drive/auto", json={"enabled": True, "interval_minutes": 5, "source": "local", "local_path": "drive_raw_only",
                                                       "run_qc": True, "ai_edit_missing": s.drive_ai_edit_raw_only, "ai_edit_after_hours": s.drive_ai_edit_after_hours})
    assert r.status_code == 200, r.text
    j = job(drive_sync.tick(utcnow()))
    assert j["stats"]["raw_imported"] == 1 and j["stats"]["ai_edited"] == 1
    db = SessionLocal()
    f = db.scalars(select(Form).where(Form.form_code == "9920001")).one()
    assert [d.origin for d in f.documents if d.kind == "edited"] == ["ai_assisted"] and f.status == "qc_review"
    assert db.scalars(select(QCRun).where(QCRun.form_id == f.id)).first() is not None             # QC ran on the edited PDF
    db.close()


def test_rule_editor_handles_quotation_marks_brackets_and_symbols():
    """Quotation marks (space inside, full stop outside), brackets, apostrophes and symbols are corrected; facts, links and inches are left alone."""
    from app.services.auto_editor import _resolve, rule_based_edits
    from app.services.compare import build_tokens
    from app.services.extraction import extract_pdf
    from app.sample_docs import _render
    pdf = _render(['The teller said, " I can help you here". I moved to the counter and I dont know the price of 50 % of it ( my first name ).',
                   'She was 5\'3" tall and said "thanks for coming in" then left.The next customer waited at https://example.com/a....b today.'])
    toks = build_tokens(extract_pdf(pdf).pages)
    ops, skipped = rule_based_edits(toks)
    ops, _ = _resolve(ops, toks)
    out = {o.original: (o.replacement, o.category) for o in ops}
    assert out['" I'] == ('"I', "quotation")                                              # no space inside the opening mark
    assert out['here".'] == ('here."', "quotation")                                       # full stop goes inside the closing mark
    assert out["dont"][0] == "don't"                                             # apostrophe (the dictionary or the symbol rule)
    assert out["50 %"] == ("50%", "symbols")
    assert out["( my"] == ("(my", "symbols") and out["name )."][0] == "name)."
    assert not any("5'3" in o.original or "example.com" in o.original or "thanks for coming in" in o.original for o in ops)
    assert not any("not balanced" in s["reason"] for s in skipped)                         # 5'3" is a unit, the other marks are paired


def test_rule_editor_flags_unbalanced_quotes_for_a_human():
    from app.services.auto_editor import rule_based_edits
    from app.services.compare import build_tokens
    from app.services.extraction import extract_pdf
    from app.sample_docs import _render
    toks = build_tokens(extract_pdf(_render(['The teller said "I can help you here and I moved to the counter quickly.'])).pages)
    ops, skipped = rule_based_edits(toks)
    assert any("not balanced" in s["reason"] and "needs a human" in s["reason"] for s in skipped)


def test_ai_editor_sees_the_form_questions_with_the_answers():
    """The model gets every question next to its answer, so it can correct the answer to fit what the question asks."""
    from app.services import auto_editor
    from app.services.compare import build_tokens
    from app.services.extraction import extract_pdf
    from app.sample_docs import _render

    class Spy:
        prompts: list = []

        def analyze(self, system, prompt):
            Spy.prompts.append((system, prompt))
            from types import SimpleNamespace
            return SimpleNamespace(text='{"edits":[]}', model="spy", usage={})

    pdf = _render(["Please describe the circumstances of your visit:", "After the initial rejection, hey tell her spaces down the counter said I can help you here.",
                   "Please detail what was said by the teller:", "Thanks for coming in my first name."])
    extracted = extract_pdf(pdf)
    toks = build_tokens(extracted.pages)

    class P:
        def __init__(self, t, n):
            self.text, self.page_no = t, n
    auto_editor.ai_edits(Spy(), toks, [P(p.text, p.page_no) for p in extracted.pages], None, set(), set())
    system, prompt = Spy.prompts[0]
    assert "<form_questions>" in prompt and "QUESTION: Please describe the circumstances of your visit:" in prompt
    assert "ANSWER: After the initial rejection" in prompt
    assert "QUOTATION MARKS" in system and "FORM QUESTIONS" in system
