"""QC checks only the fixed error table (Grammar 5 · Missed Instruction 15 · Contradiction 20 · Wrong Location 25)."""
from app.sample_docs import _render

from .helpers import upload


def _pair(api, ctx, code, raw_lines, edited_lines):
    mgr = api("manager")
    form_id = upload(mgr, ctx["p1"], f"{code}_1.pdf", _render(raw_lines)).json()["results"][0]["form_id"]
    assert mgr.post(f"/api/forms/{form_id}/assign", json={"editor_id": ctx["users"]["editor"], "reviewer_id": ctx["users"]["qc"]}).status_code == 200
    ed = api("editor")
    ed.post(f"/api/forms/{form_id}/start")
    assert upload(ed, ctx["p1"], f"{code}_2.pdf", _render(edited_lines)).json()["uploaded"] == 1
    return form_id


def test_error_table_endpoint_is_the_sheet(api, ctx):
    t = api("qc").get("/api/qc/error-table").json()
    assert t["total_score"] == 100
    assert [(c["label"], c["points"]) for c in t["categories"]] == [
        ("Grammar Mistake", 5), ("Missed Instruction", 15), ("Contradiction", 20), ("Wrong Location / Put on Different category", 25)]
    subs = {c["key"]: [s["label"] for s in c["subtypes"]] for c in t["categories"]}
    assert subs["grammar"] == ["Punctuation", "Extra Space/No space", "Spelling", "Past tense", "Incorrect Terminology", "Profanity"]
    assert len(subs["missed_instruction"]) == 5 and len(subs["contradiction"]) == 3 and len(subs["wrong_location"]) == 3
    manual = {s["key"] for c in t["categories"] for s in c["subtypes"] if s["manual_only"]}
    assert manual == {"video_not_checked", "audio_not_evaluated", "call_attempt_not_checked", "incorrect_option_picture", "shop_performed_incorrectly"}


def test_checks_report_only_table_errors_and_classify_date_time(api, ctx):
    raw = ["The visit was at 10:30 AM and the associate was kind.", "She asked how I was doing.", "The store was clean."]
    edited = ["The visit was at 11:45 AM and the associate was kind.", "She asked how I was doing ..", "The store was clean.The associate smiled.",
              "That was a shit experience."]
    form_id = _pair(api, ctx, "FORM040", raw, edited)
    ws = api("qc").get(f"/api/forms/{form_id}/qc").json()
    by_sub = {}
    for f in ws["findings"]:
        by_sub.setdefault(f["error_subtype"], []).append(f)
    assert {"incorrect_date_time", "punctuation", "extra_space", "profanity"} <= set(by_sub)
    assert by_sub["incorrect_date_time"][0]["category"] == "missed_instruction" and by_sub["incorrect_date_time"][0]["points"] == 15
    assert by_sub["profanity"][0]["category"] == "grammar" and by_sub["profanity"][0]["points"] == 5
    assert any("No space after punctuation" == f["title"] for f in by_sub["extra_space"])
    assert all(f["error_label"] and f["category_label"] for f in ws["findings"])
    # rules that are not on the table are listed but flagged as not used
    rules = api("sa").get("/api/rules").json()
    by_key = {r["rule_key"]: r for r in rules}
    assert by_key["GEN-PROFANITY"]["in_qc"] and by_key["GEN-PROFANITY"]["error_subtype"] == "profanity"
    assert all(by_key[k]["in_qc"] is False for k in ("GEN-REPEAT", "GEN-PII", "GEN-ARTICLE") if k in by_key)      # old rules in an existing database
    assert not any(v["rule_key"] in ("GEN-REPEAT", "GEN-PII", "GEN-ARTICLE", "GEN-INJECTION", "GEN-AI-STYLE") for v in ws["run"]["rule_versions"])


def test_reviewer_adds_findings_from_the_table_and_score_follows(api, ctx):
    form_id = _pair(api, ctx, "FORM041", ["The store was clean."], ["The store was clean."])
    qc = api("qc")
    url = f"/api/forms/{form_id}/findings"
    assert qc.post(url, json={"error_subtype": "tone_of_voice", "title": "Rude", "edited_excerpt": "The store was clean."}).status_code == 422
    # a text error needs a verbatim quote ...
    assert qc.post(url, json={"error_subtype": "spelling", "title": "Typo", "description": "x"}).status_code == 422
    # ... but video / audio / picture checks can be described instead
    assert qc.post(url, json={"error_subtype": "video_not_checked", "title": "Video skipped"}).status_code == 422
    r = qc.post(url, json={"error_subtype": "video_not_checked", "title": "Video not watched fully",
                           "description": "BOTD was marked but the video shows the answer at 2:10."})
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["category"] == "missed_instruction" and f["points"] == 15 and f["source"] == "manual" and f["review_status"] == "accepted"
    assert qc.get(f"/api/forms/{form_id}/qc").json()["score"]["score"] == 85
    r = qc.post(url, json={"error_subtype": "closed_treated_open", "title": "Location was closed", "edited_excerpt": "The store was clean."})
    assert r.status_code == 201 and r.json()["category"] == "wrong_location"
    sc = qc.get(f"/api/forms/{form_id}/qc").json()["score"]
    assert sc["score"] == 100 - 15 - 25 and [d["points"] for d in sc["deductions"]] == [25, 15]
    # one category counts once: a second Missed Instruction does not deduct again
    qc.post(url, json={"error_subtype": "audio_not_evaluated", "title": "Audio", "description": "Greeting missed in the recording."})
    assert qc.get(f"/api/forms/{form_id}/qc").json()["score"]["score"] == 60


def test_reports_are_built_on_the_error_table(api, ctx):
    import io

    import pymupdf

    form_id = _pair(api, ctx, "FORM042", ["The store was clean."], ["The store was clean.The associate smiled .. That was a shit day."])
    qc = api("qc")
    for f in qc.get(f"/api/forms/{form_id}/qc").json()["findings"]:
        qc.post(f"/api/findings/{f['id']}/review", json={"action": "accept"})
    qc.post(f"/api/forms/{form_id}/findings", json={"error_subtype": "audio_not_evaluated", "title": "Audio", "description": "Greeting missed."})
    sc = qc.get(f"/api/forms/{form_id}/qc").json()["score"]
    assert sc["score"] == 100 - 5 - 15
    # form report PDF: scorecard of the fixed table, every error listed, score stated
    pdf = qc.get(f"/api/forms/{form_id}/qc-report")
    text = " ".join(" ".join(p.get_text().split()) for p in pymupdf.open(stream=pdf.content, filetype="pdf"))      # table cells wrap
    assert "QC scorecard" in text and f"QC score: {sc['score']} / 100" in text
    for label in ("Extra Space/No space", "Profanity", "Audio not evaluated correctly", "Status Incorrect selected",
                  "Original sentences changed which changed the meaning"):
        assert label in text
    # the two new reports list the table and the per-form score
    card = qc.get("/api/reports/qc_scorecard").json()
    assert len(card["rows"]) == 17
    prof = next(r for r in card["rows"] if r["error"] == "Profanity")
    assert prof["confirmed"] == 1 and prof["category"] == "Grammar Mistake"
    scores = qc.get("/api/reports/qc_form_scores").json()
    row = next(r for r in scores["rows"] if r["form"] == "FORM042")
    assert row["score"] == sc["score"] and row["Grammar Mistake"] >= 2 and row["Missed Instruction"] == 1
    assert api("editor").get("/api/reports/qc_scorecard").status_code == 200
    assert api("poc").get("/api/reports/qc_scorecard").status_code == 403
    # project report: error table + average score + a score per form
    summ = qc.get(f"/api/qc/projects/{ctx['p1']}").json()
    row = next(r for r in summ["forms"] if r["form_code"] == "FORM042")
    assert row["score"] == sc["score"] and row["errors"]["missed_instruction"] == 1
    assert {c["category"] for c in summ["error_table"]} == {c["label"] for c in qc.get("/api/qc/error-table").json()["categories"]}
    assert summ["totals"]["average_score"] == sc["score"]
    rep = qc.get(f"/api/qc/projects/{ctx['p1']}/report")
    assert rep.status_code == 200 and rep.content.startswith(b"%PDF")
    ptext = " ".join(p.get_text() for p in pymupdf.open(stream=rep.content, filetype="pdf"))
    assert "Errors by the QC error table" in ptext and "Average QC score" in ptext and "Profanity" in ptext
