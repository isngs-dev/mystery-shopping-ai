"""Chrome extension API: identify the company of an open RAW form, propose edits with that company's rules, human feedback, finalize + QC."""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Form, QCRun, RuleVersion
from app.sample_docs import _render
from app.services.ai import AIProvider, AIResult, set_provider_override
from app.services.extraction import extract_pdf
from app.services.rules_service import create_rule

from .conftest import PASSWORD
from .helpers import upload

LINES = ["Visit report", "The cashier greet me warmly at the door.", "I recieved my order,quickly and paid at the counter.", "The store was clean."]


class RoutingProvider(AIProvider):
    """Answers the editor prompt and the company-check prompt differently, and remembers what it was asked."""
    name, model, external = "fake", "fake-1", False

    def __init__(self):
        self.prompts = []

    def analyze(self, system, user):
        self.prompts.append((system, user))
        if "identify which client company" in system:
            return AIResult(text=json.dumps({"client": "Acme Retail", "project": "Store Visits", "confidence": 0.93, "evidence": "Store visit report"}), model="fake-1")
        if "re-edit ONE passage" in system:
            return AIResult(text=json.dumps({"replacement": "The cashier greeted me warmly", "reason": "Past tense, as the reviewer asked"}), model="fake-1")
        edits = [{"original": "The cashier greet me warmly", "replacement": "The cashier greeted me warmly", "reason": "Past tense (company rule)",
                  "category": "tense", "confidence": 0.9}]
        if "REJECTED" in user:
            edits = []                                                              # the reviewer said no: the model has nothing else to add
        return AIResult(text=json.dumps({"edits": edits}), model="fake-1")


def _form(api, ctx, code="FORM050"):
    r = upload(api("manager"), ctx["p1"], f"{code}_1.pdf", _render(LINES)).json()["results"][0]
    assert r["ok"], r
    return r["form_id"]


def _token(key="qc"):
    r = TestClient(app).post("/api/ext/login", json={"email": f"{key}@test.local", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(token):
    c = TestClient(app)
    return lambda method, url, **kw: c.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kw)


def test_login_roles_and_identify_by_file_name(api, ctx):
    form_id = _form(api, ctx)
    anon = TestClient(app)
    assert anon.post("/api/ext/login", json={"email": "poc@test.local", "password": PASSWORD}).status_code == 403      # not an editing role
    assert anon.post("/api/ext/login", json={"email": "nobody@test.local", "password": "x" * 12}).status_code == 401
    call = _auth(_token("qc"))
    assert call("GET", "/api/ext/me").json()["user"]["role"] == "qc_reviewer"
    assert _auth("not-a-token")("GET", "/api/ext/me").status_code == 401

    out = call("POST", "/api/ext/identify", json={"filename": "FORM050_1.pdf"}).json()
    assert out["found"] and out["form"]["id"] == form_id and out["company"]["client"] == "Acme Retail" and out["company"]["project"] == "Store Visits"
    assert out["is_raw"] is True and any(r["key"] == "GEN-SPELL" for r in out["rules"]) and out["ai"]["usable"] is False
    assert call("POST", "/api/ext/identify", json={"filename": "PDFFORM050_1.pdf"}).json()["found"] is False       # a real code that merely starts with PDF is left alone
    assert call("POST", "/api/ext/identify", json={"filename": "PDF FORM050_1.pdf"}).json()["form"]["id"] == form_id   # Drive's "PDF " label in front of the name is dropped
    unknown = call("POST", "/api/ext/identify", json={"filename": "9999999_1.pdf"}).json()
    assert unknown["found"] is False and "not in the platform yet" in unknown["message"]


def test_company_rules_reach_the_ai_and_a_human_round_trip_ends_in_an_edited_pdf_with_qc(api, ctx):
    form_id = _form(api, ctx)
    db = SessionLocal()
    client_id = db.get(Form, form_id).project.client_id
    create_rule(db, None, rule_key="ACME-TENSE", published=True, name="Write comments in the past tense", category="grammar", error_subtype="past_tense",
                severity="minor", client_id=client_id, check_type="ai_guideline", config={},
                description="Acme: every sentence about the visit is in the past tense (“greeted”, not “greet”).")
    pend = create_rule(db, None, rule_key="ACME-PENDING", name="Say associate", category="grammar", error_subtype="terminology", severity="minor",
                       client_id=client_id, check_type="forbidden_terms", config={"terms": ["employee"]}, description="x")
    pend.status = "pending_approval"
    db.commit()
    db.close()
    fake = RoutingProvider()
    set_provider_override(fake)
    call = _auth(_token("qc"))
    leader = _auth(_token("manager"))

    info = call("POST", "/api/ext/identify", json={"filename": "FORM050_1.pdf"}).json()
    assert info["company_rules"] == 2 and any(r["key"] == "ACME-TENSE" and r["company_specific"] and r["status"] == "published" for r in info["rules"])
    assert any(r["key"] == "ACME-PENDING" and r["status"] == "draft" for r in info["rules"])   # a checklist rule awaiting approval is used for editing, marked draft
    assert [p["key"] for p in info["pending_approval"]] == ["ACME-PENDING"]

    p1 = call("POST", f"/api/ext/forms/{form_id}/propose", json={})
    assert p1.status_code == 200, p1.text
    prop = p1.json()
    by_orig = {e["original"]: e for e in prop["edits"]}
    assert by_orig["The cashier greet me warmly"]["replacement"] == "The cashier greeted me warmly" and by_orig["The cashier greet me warmly"]["source"] == "ai"
    assert by_orig["recieved"]["replacement"] == "received" and by_orig["order,quickly"]["replacement"] == "order, quickly"       # built-in grammar rules
    assert all("before" in e and "after" in e for e in prop["edits"]) and by_orig["recieved"]["before"].endswith("I") and by_orig["recieved"]["after"].startswith("my")   # the words around an edit
    assert all(e["raw_boxes"] and e["id"] for e in prop["edits"]) and prop["pages"][0]["width"] and prop["company"]["client"] == "Acme Retail"
    assert prop["company_check"]["company"] == "Acme Retail" and prop["company_check"]["matches_folder"] is True
    editor_prompts = [(s, u) for s, u in fake.prompts if "identify which client company" not in s]
    assert "<company_guidelines>" in editor_prompts[0][1] and "past tense" in editor_prompts[0][1]          # the company's own rule text reaches the model
    assert "Say associate" in editor_prompts[0][1]                                                       # ... and so does a draft checklist rule

    # the human rejects the AI's tense edit and says why -> re-edit round
    p2 = call("POST", f"/api/ext/forms/{form_id}/propose", json={"proposal_id": prop["proposal_id"], "feedback": "greet is quoted speech here, keep it",
                                                                 "rejected": [{"original": "The cashier greet me warmly", "replacement": "The cashier greeted me warmly"}]}).json()
    assert p2["proposal_id"] == prop["proposal_id"] and p2["round"] == 2 and p2["feedback_history"][0]["comment"].startswith("greet is quoted")
    assert "The cashier greet me warmly" not in {e["original"] for e in p2["edits"]}
    last_user = [u for s, u in fake.prompts if "identify which client company" not in s][-1]
    assert "REJECTED" in last_user and "quoted speech" in last_user

    # finalize: accept what is left (the built-in grammar edits)
    ids = [e["id"] for e in p2["edits"]]
    assert ids
    fin = call("POST", f"/api/ext/forms/{form_id}/finalize", json={"proposal_id": p2["proposal_id"], "accepted": ids, "note": "checked all"})   # whoever cross-checked forwards it
    assert fin.status_code == 202, fin.text
    out = fin.json()
    assert out["applied"] == len(ids) and out["version"] == 1 and out["qc_run_id"]
    st = call("GET", f"/api/ext/forms/{form_id}/status").json()
    assert st["edited_version"] == 1 and st["qc"]["status"] in ("completed", "completed_with_warnings") and "score" in st["qc"]
    db = SessionLocal()
    f = db.get(Form, form_id)
    edited = next(d for d in f.documents if d.kind == "edited")
    assert edited.origin == "ai_assisted" and f.status == "qc_review" and db.scalars(select(QCRun).where(QCRun.form_id == form_id)).first()
    edited_id = edited.id
    db.close()
    got = " ".join(p.text for p in extract_pdf(api("manager").get(f"/api/documents/{edited_id}/file").content).pages)
    assert "received" in got and "recieved" not in got and "order, quickly" in got and "greet me" in got          # the rejected edit was NOT applied
    # the leader sees what the editor decided, and the QC result so far
    fw = leader("GET", "/api/ext/forwarded").json()["items"]
    assert len(fw) == 1 and fw[0]["form"]["code"] == "FORM050" and fw[0]["by"] == "QC" and fw[0]["accepted"] == len(ids) and fw[0]["note"] == "checked all"
    assert fw[0]["dismissed"] == 0 and fw[0]["total"] == len(ids) and all(i["state"] == "accepted" for i in fw[0]["items"])
    assert fw[0]["qc"]["status"] in ("completed", "completed_with_warnings") and call("GET", "/api/ext/forwarded").status_code == 403   # editors do not get the list
    # the proposal is closed: finalizing it again is refused
    assert leader("POST", f"/api/ext/forms/{form_id}/finalize", json={"proposal_id": p2["proposal_id"], "accepted": ids}).status_code == 409


def test_without_an_ai_model_the_built_in_grammar_rules_still_work_and_access_is_scoped(api, ctx):
    set_provider_override(None)
    form_id = _form(api, ctx, "FORM051")
    call = _auth(_token("qc"))
    prop = call("POST", f"/api/ext/forms/{form_id}/propose", json={"feedback": "please also fix tone"}).json()
    assert prop["engine"] == "rules" and "AI model not used" in prop["message"] and any(e["original"] == "recieved" for e in prop["edits"])
    # nothing accepted: the RAW is submitted unchanged for QC
    fin = _auth(_token("manager"))("POST", f"/api/ext/forms/{form_id}/finalize", json={"proposal_id": prop["proposal_id"], "accepted": []}).json()
    assert fin["applied"] == 0 and "unchanged" in fin["verification"]
    # confirming edits that give exactly the PDF that is already stored does not fail: the stored version is kept and its QC applies
    again = call("POST", f"/api/ext/forms/{form_id}/propose", json={}).json()
    same = _auth(_token("manager"))("POST", f"/api/ext/forms/{form_id}/finalize", json={"proposal_id": again["proposal_id"], "accepted": []})
    assert same.status_code == 202 and same.json()["reused"] is True and same.json()["version"] == fin["version"] and "already stored" in same.json()["verification"]
    # an editor with no access to the project cannot touch the form
    other = _auth(_token("editor2"))
    assert other("POST", f"/api/ext/forms/{form_id}/propose", json={}).status_code in (403, 404)


def test_cross_check_leader_confirmation_and_leader_rules(api, ctx):
    """Per-edit comment -> AI re-edit of that edit, the human's own wording, an edit the AI missed, hand-over to the leader, send back, confirm -> PDF + QC."""
    form_id = _form(api, ctx, "FORM052")
    fake = RoutingProvider()
    set_provider_override(fake)
    editor, leader = _auth(_token("qc")), _auth(_token("manager"))
    assert editor("GET", "/api/ext/me").json()["user"]["can_confirm"] is False and leader("GET", "/api/ext/me").json()["user"]["can_confirm"] is True

    # the leader adds a grammar rule for the client: used for editing at once, awaiting approval for QC; an editor cannot
    body = {"name": "Use associate not employee", "description": "Write associate instead of employee in every comment.", "error_subtype": "terminology", "scope": "client"}
    assert editor("POST", f"/api/ext/forms/{form_id}/rules", json=body).status_code == 403
    made = leader("POST", f"/api/ext/forms/{form_id}/rules", json=body)
    assert made.status_code == 201 and made.json()["status"] == "draft" and made.json()["added_by"] == "MANAGER"
    assert leader("POST", f"/api/ext/forms/{form_id}/rules", json={**body, "error_subtype": "meaning_changed"}).status_code == 422       # only grammar rule types
    prop = editor("POST", f"/api/ext/forms/{form_id}/propose", json={}).json()
    assert any(r["name"] == "Use associate not employee" and r["status"] == "draft" for r in prop["rules"])
    assert "Use associate not employee" in [u for s_, u in fake.prompts if "identify which client company" not in s_ and "re-edit ONE" not in s_][-1]
    pid = prop["proposal_id"]
    tense = next(e for e in prop["edits"] if e["original"] == "The cashier greet me warmly")

    # a comment on ONE edit: the AI looks at that passage again; a human can add an edit the AI missed; unsafe wording is refused
    ask = editor("POST", f"/api/ext/forms/{form_id}/proposals/{pid}/ask", json={"edit_id": tense["id"], "comment": "keep it simple past"})
    assert ask.status_code == 200, ask.text
    assert ask.json()["edit"]["replacement"] == "The cashier greeted me warmly" and ask.json()["comments"][0]["text"] == "keep it simple past"
    ctx_prompt = fake.prompts[-1][1]
    assert "<reviewer_comment>keep it simple past" in ctx_prompt and "<company_guidelines>" in ctx_prompt and "<passage>The cashier greet me warmly</passage>" in ctx_prompt
    missed = editor("POST", f"/api/ext/forms/{form_id}/proposals/{pid}/add", json={"original": "paid at the counter", "replacement": "paid at the till"})
    assert missed.status_code == 200 and missed.json()["edit"]["source"] == "human" and missed.json()["edit"]["raw_boxes"]
    added = missed.json()["edit"]
    assert editor("POST", f"/api/ext/forms/{form_id}/proposals/{pid}/add", json={"original": "not in the report", "replacement": "x"}).status_code == 404
    assert editor("POST", f"/api/ext/forms/{form_id}/proposals/{pid}/add", json={"original": "The store was clean", "replacement": "The store was not clean"}).status_code == 422
    assert editor("GET", f"/api/ext/forms/{form_id}/proposal").json()["proposal"] is None                       # not handed over yet

    # hand the cross-checked edits to the leader (the editor dismisses one and rewords the added one)
    ids = [e["id"] for e in prop["edits"]] + [added["id"]]
    dismissed = ids[0]
    sub = editor("POST", f"/api/ext/forms/{form_id}/proposals/{pid}/submit",
                 json={"accepted": ids[1:], "changes": {added["id"]: "paid at the cash desk"}, "comments": {dismissed: "not needed here"}, "note": "please check the till edit"})
    assert sub.status_code == 200 and sub.json()["status"] == "awaiting_leader"
    assert editor("POST", f"/api/ext/forms/{form_id}/proposals/{pid}/submit", json={"accepted": []}).status_code == 409           # already handed over

    q = leader("GET", "/api/ext/review-queue").json()["items"]
    assert len(q) == 1 and q[0]["form"]["code"] == "FORM052" and q[0]["submitted_by"] == "QC" and q[0]["reworded"] == 1 and q[0]["dismissed"] == 1
    assert editor("GET", "/api/ext/review-queue").status_code == 403
    info = editor("POST", "/api/ext/identify", json={"filename": "FORM052_1.pdf"}).json()
    assert info["review"]["status"] == "awaiting_leader" and info["review"]["note"] == "please check the till edit"
    shown = leader("GET", f"/api/ext/forms/{form_id}/proposal").json()["proposal"]
    assert shown["review"]["changes"][added["id"]] == "paid at the cash desk" and any(c["text"] == "not needed here" for c in shown["comments"][dismissed])

    back = leader("POST", f"/api/ext/forms/{form_id}/proposals/{pid}/send-back", json={"note": "keep the till wording"})
    assert back.status_code == 200 and back.json()["returned"]["note"] == "keep the till wording" and back.json()["status"] == "proposed"
    assert editor("POST", f"/api/ext/forms/{form_id}/proposals/{pid}/submit", json={"accepted": ids[1:], "changes": {added["id"]: "paid at the cash desk"}}).status_code == 200

    fin = leader("POST", f"/api/ext/forms/{form_id}/finalize", json={"proposal_id": pid, "accepted": ids[1:], "changes": {added["id"]: "paid at the cash desk"}})
    assert fin.status_code == 202, fin.text
    assert fin.json()["applied"] == len(ids) - 1 and fin.json()["qc_run_id"]
    db = SessionLocal()
    edited = next(d for d in db.get(Form, form_id).documents if d.kind == "edited")
    edited_id = edited.id
    db.close()
    got = " ".join(p.text for p in extract_pdf(api("manager").get(f"/api/documents/{edited_id}/file").content).pages)
    assert "paid at the cash desk" in got and "paid at the counter" not in got                   # the human's own wording is what was typeset
    assert leader("GET", "/api/ext/review-queue").json()["items"] == []
    fw = leader("GET", "/api/ext/forwarded").json()["items"]
    assert len(fw) == 1 and fw[0]["dismissed"] == 1 and fw[0]["reworded"] == 1 and fw[0]["commented"] >= 1 and fw[0]["added_by_human"] == 1
    reworded = next(i for i in fw[0]["items"] if i["state"] == "reworded")
    assert reworded["ai"] == "paid at the till" and reworded["final"] == "paid at the cash desk"
    assert leader("POST", f"/api/ext/forms/{form_id}/finalize", json={"proposal_id": pid, "accepted": []}).status_code == 409


def test_ai_recognises_another_client_and_the_human_switches_the_rules(api, ctx):
    """The folder says Acme, the report text says Other Co: the panel gets a switch suggestion and the other client's checklist rules apply after the switch."""
    class Other(RoutingProvider):
        def analyze(self, system, user):
            if "identify which client company" in system:
                self.prompts.append((system, user))
                return AIResult(text=json.dumps({"client": "Other Co", "project": "Secret Project", "confidence": 0.9, "evidence": "Other Co branded report"}), model="fake-1")
            return super().analyze(system, user)

    form_id = _form(api, ctx, "FORM053")
    db = SessionLocal()
    from app.models import Client
    other_id = db.scalars(select(Client).where(Client.name == "Other Co")).first().id
    create_rule(db, None, rule_key="OTHER-TERMS", published=True, name="Say customer not guest", category="grammar", error_subtype="terminology", severity="minor",
                client_id=other_id, check_type="ai_guideline", config={}, description="Other Co: write customer, never guest.")
    db.commit()
    db.close()
    fake = Other()
    set_provider_override(fake)
    call = _auth(_token("qc"))
    prop = call("POST", f"/api/ext/forms/{form_id}/propose", json={}).json()
    cc = prop["company_check"]
    assert cc["company"] == "Other Co" and cc["matches_folder"] is False and cc["suggest_switch"] is True and cc["client_id"] == other_id
    assert not any(r["key"] == "OTHER-TERMS" for r in prop["rules"])
    switched = call("POST", f"/api/ext/forms/{form_id}/propose", json={"proposal_id": prop["proposal_id"], "client_id": other_id}).json()
    assert any(r["key"] == "OTHER-TERMS" for r in switched["rules"]) and switched["client_id"] == other_id
    assert "Say customer not guest" in [u for s_, u in fake.prompts if "identify which client company" not in s_][-1]


class LearnProvider(RoutingProvider):
    """Also answers the guideline-learning prompt: two usable rules, one with a type that is not a grammar rule (dropped)."""

    def analyze(self, system, user):
        if "guideline document into rules" in system:
            self.prompts.append((system, user))
            return AIResult(text=json.dumps({"rules": [
                {"name": "Write the brand as Acme Retail", "description": "Always write the brand as “Acme Retail”, never “AcmeRetail”.", "type": "spelling", "examples": "AcmeRetail -> Acme Retail"},
                {"name": "No mention of alcohol", "description": "Do not mention alcohol in the report; say “beverage”.", "type": "terminology"},
                {"name": "Visit within 24 hours", "description": "The shopper must visit within a day.", "type": "scoring"}]}), model="fake-1")
        return super().analyze(system, user)


def test_the_guideline_file_on_the_website_is_learned_once_into_draft_rules_that_apply_to_its_pages(api, ctx):
    import base64
    import hashlib

    prov = LearnProvider()
    set_provider_override(prov)
    try:
        call = _auth(_token("qc"))
        brief = _render(["Acme Retail client brief"] + [f"Rule {i}: write the brand as Acme Retail and never mention alcohol in the report text." for i in range(1, 9)])
        sha = hashlib.sha256(brief).hexdigest()
        body = {"client_hint": "Acme Retail: Store 978 - Carlin NV", "project": "Store 978 - Carlin NV", "sha256": sha, "filename": "brief.pdf", "source_url": "https://x.test/brief.pdf"}
        assert call("POST", "/api/ext/guidelines/learn", json=body).json()["status"] == "need_content"                    # an unseen file: the platform asks for it
        got = call("POST", "/api/ext/guidelines/learn", json={**body, "content_b64": base64.b64encode(brief).decode()}).json()
        assert got["status"] == "learned" and got["rules"] == 2 and [r["name"] for r in got["learned"]][0].startswith("Write the brand")      # the scoring rule is dropped
        assert any("<client_brief" in u and "never mention alcohol" in u for _, u in prov.prompts)
        db = SessionLocal()
        rows = db.execute(select(RuleVersion).where(RuleVersion.change_note.like("Learned by the AI%"))).scalars().all()
        assert len(rows) == 2 and {r.status for r in rows} == {"pending_approval"} and all(r.client_id and r.config["guideline"] == sha[:16] for r in rows)
        db.close()
        again = call("POST", "/api/ext/guidelines/learn", json=body).json()                                                    # the same file again: nothing is read
        assert again["status"] == "known" and again["rules"] == 2 and again["new_project"] is False
        other = call("POST", "/api/ext/guidelines/learn", json={**body, "project": "Store 12 - Reno NV"}).json()               # a new project that links the same file
        assert other["status"] == "known" and other["new_project"] is True
        assert call("POST", "/api/ext/guidelines/learn", json={**body, "client_hint": "Unknown Brand"}).json()["status"] == "no_client"
        assert _auth("bad")("POST", "/api/ext/guidelines/learn", json=body).status_code == 401
        assert call("POST", "/api/ext/guidelines/learn", json={**body, "content_b64": "***"}).status_code == 422

        form = {"client_hint": "Acme Retail: Store 978", "answers": [{"id": "1", "question": "Comments", "text": "The cashier greet me warmly at the door."}]}
        plain = call("POST", "/api/ext/text/propose", json=form).json()
        assert plain["learned_rules"] == 0 and not any("Write the brand" in r["name"] for r in plain["rules"])                  # a page without that file does not get its rules
        mine = call("POST", "/api/ext/text/propose", json={**form, "guidelines": [sha[:16]]}).json()
        assert mine["learned_rules"] == 2 and any(r["name"].startswith("Write the brand") and r["status"] == "draft" for r in mine["rules"])
        assert any("Write the brand as Acme Retail" in u for _, u in prov.prompts if "<company_guidelines>" in u)             # the learned rule text reaches the editor model
    finally:
        set_provider_override(None)


def _docx(text):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:body>" + "".join(f"<w:p><w:r><w:t>{ln}</w:t></w:r></w:p>" for ln in text.split("\n")) + "</w:body></w:document>")
    return buf.getvalue()


def test_uploaded_guideline_files_become_rules_for_every_form_of_the_client_and_can_be_removed(api, ctx):
    import base64

    prov = LearnProvider()
    set_provider_override(prov)
    try:
        b64 = lambda raw: base64.b64encode(raw).decode()                                                      # noqa: E731
        boss, editor = _auth(_token("manager")), _auth(_token("qc"))
        brief = _docx("\n".join(f"Rule {i}: write the brand as Discount Tire and never mention alcohol in any report you edit." for i in range(1, 8)))

        assert editor("GET", "/api/ext/clients").json()["can_create"] is False and boss("GET", "/api/ext/clients").json()["can_create"] is True
        refused = editor("POST", "/api/ext/guidelines/upload", json={"client_name": "Discount Tire", "filename": "dt.docx", "content_b64": b64(brief), "create": True}).json()
        assert refused["status"] == "no_client" and "Manager" in refused["message"]                           # an editor cannot create a client
        out = boss("POST", "/api/ext/guidelines/upload", json={"client_name": "Discount Tire", "filename": "Discount Tire guidelines.docx", "content_b64": b64(brief), "create": True}).json()
        assert out["status"] == "learned" and out["created_client"] is True and out["client"] == "Discount Tire" and out["rules"] == 2
        again = boss("POST", "/api/ext/guidelines/upload", json={"client_name": "discount tire", "filename": "copy.docx", "content_b64": b64(brief), "create": True}).json()
        assert again["status"] == "known" and again["created_client"] is False                                # same file, same client: nothing new

        form = {"client_hint": "Discount Tire: Phone Evaluation 2026 | insite.intelli-shop.com", "answers": [{"id": "1", "question": "Comments", "text": "The cashier greet me warmly."}]}
        for project in ("Phone Evaluation 2026", "Some Other Project"):                                       # a form of the client, any project: its rules trigger
            got = boss("POST", "/api/ext/text/propose", json={**form, "project": project}).json()
            assert got["company"]["client"] == "Discount Tire" and got["manual_rules"] == 2
        assert any("write the brand as Discount Tire" in u.lower() or "Write the brand as Acme Retail" in u for _, u in prov.prompts if "<company_guidelines>" in u)

        cid = next(c["id"] for c in boss("GET", "/api/ext/clients").json()["clients"] if c["name"] == "Discount Tire")
        files = boss("GET", f"/api/ext/guidelines/files?client_id={cid}").json()["files"]
        assert len(files) == 1 and files[0]["rules"] == 2 and files[0]["filename"].startswith("Discount Tire")
        assert editor("DELETE", f"/api/ext/guidelines/files/{files[0]['id']}").status_code == 403              # not the uploader and not a Manager
        assert boss("DELETE", f"/api/ext/guidelines/files/{files[0]['id']}").json()["rules_retired"] == 2
        assert boss("POST", "/api/ext/text/propose", json=form).json()["manual_rules"] == 0                  # the retired rules stop being used
        assert boss("DELETE", f"/api/ext/guidelines/files/{files[0]['id']}").status_code == 404

        # an editor can upload for an existing client; the client may be taken from the file name; a long checklist is read in pieces; old Word files are refused
        long_text = "\n".join(f"Item {i}: always write the brand as Acme Retail and never mention alcohol in the report." for i in range(1, 500))
        n0 = len(prov.prompts)
        auto = editor("POST", "/api/ext/guidelines/upload", json={"client_name": "x", "auto": True, "filename": "Acme Retail checklist.txt", "content_b64": b64(long_text.encode())}).json()
        assert auto["status"] == "learned" and auto["client"] == "Acme Retail" and len(prov.prompts) - n0 >= 2
        old = editor("POST", "/api/ext/guidelines/upload", json={"client_name": "Acme Retail", "filename": "old.doc", "content_b64": b64(b"\xd0\xcf\x11\xe0 not really")}).json()
        assert old["status"] == "unsupported"
        assert editor("POST", "/api/ext/guidelines/upload", json={"client_name": "Acme Retail", "filename": "a.txt", "content_b64": "***"}).status_code == 422
        assert _auth("bad")("GET", "/api/ext/clients").status_code == 401
    finally:
        set_provider_override(None)


class FakeDrive:
    """A tiny Drive: <root>/Checklist/{CXE_Checklist, Alta 360_Checklist, picture.png}, <root>/Basic MS Documentation/Writing for Professionalism.pdf."""

    def __init__(self, tree):
        self.tree = tree

    def root(self):
        from app.services.drive_sources import Node
        return Node("root", "Mystery Shopping_QC", True)

    def children(self, node):
        return self.tree.get(node.id, [])

    def read(self, node):
        return self.tree["bytes"][node.id]

    def close(self):
        pass


def test_a_drive_folder_of_checklists_is_imported_per_client_and_basic_documents_apply_to_all(api, ctx, monkeypatch):
    from types import SimpleNamespace

    from app.services import ext_drive_import
    from app.services.drive_sources import Node

    GDOC = "application/vnd.google-apps.document"
    body = _docx("\n".join(f"Rule {i}: write the brand in full and never mention alcohol in any report you edit for this client." for i in range(1, 8)))
    tree = {"root": [Node("f_check", "Checklist", True), Node("f_basic", "Basic MS Documentation", True)],
            "f_check": [Node("d1", "CXE_Checklist", False, 100, GDOC), Node("d2", "Alta 360_Checklist", False, 100, GDOC), Node("p1", "picture.png", False, 5, "image/png")],
            "f_basic": [Node("b1", "Writing for Professionalism.pdf", False, 100, "application/pdf")],
            "bytes": {"d1": body + b"1", "d2": body + b"2", "b1": _docx("Always write in a professional tone, in the past tense, and never use slang in any report text.\n" * 6)}}
    monkeypatch.setattr(ext_drive_import, "_open_drive", lambda fid: FakeDrive(tree))
    monkeypatch.setattr(ext_drive_import, "get_settings", lambda: SimpleNamespace(google_drive_folder_id="ROOT"))
    set_provider_override(LearnProvider())
    try:
        boss, editor = _auth(_token("manager")), _auth(_token("qc"))
        assert editor("POST", "/api/ext/guidelines/drive", json={"folder": "Checklist"}).status_code == 403            # Managers only

        start = boss("POST", "/api/ext/guidelines/drive", json={"folder": "checklist", "mode": "client"}).json()
        assert start["status"] == "started"
        job = boss("GET", f"/api/ext/guidelines/drive/{start['job']}").json()
        assert job["state"] == "done" and job["total"] == 2 and job["folder"] == "Checklist"                          # the picture is not a document
        by = {f["name"]: f for f in job["files"]}
        assert by["CXE_Checklist"]["status"] == "learned" and by["CXE_Checklist"]["client"] == "CXE" and by["CXE_Checklist"]["created_client"] is True
        assert by["Alta 360_Checklist"]["client"] == "Alta 360" and by["Alta 360_Checklist"]["rules"] == 2
        names = {c["name"] for c in boss("GET", "/api/ext/clients").json()["clients"]}
        assert {"CXE", "Alta 360"} <= names

        form = {"client_hint": "CXE: Master Form | cxeview.com", "answers": [{"id": "1", "question": "Comments", "text": "The cashier greet me warmly."}]}
        assert boss("POST", "/api/ext/text/propose", json=form).json()["manual_rules"] == 2                            # a CXE form follows CXE's file

        gen = boss("POST", "/api/ext/guidelines/drive", json={"folder": "Basic MS Documentation", "mode": "general"}).json()
        job2 = boss("GET", f"/api/ext/guidelines/drive/{gen['job']}").json()
        assert job2["state"] == "done" and job2["files"][0]["status"] == "learned" and job2["files"][0]["rules"] == 2
        assert "General (all clients)" not in {c["name"] for c in boss("GET", "/api/ext/clients").json()["clients"]}        # the placeholder owner stays hidden
        for hint in ("CXE: Master Form", "Acme Retail: Store 978", "Alta 360: Form"):                                  # general rules reach every client
            assert boss("POST", "/api/ext/text/propose", json={**form, "client_hint": hint}).json()["manual_rules"] >= 2
        assert boss("POST", "/api/ext/text/propose", json={**form, "client_hint": "Unknown Brand"}).json()["manual_rules"] == 2   # even a page of no known client

        again = boss("POST", "/api/ext/guidelines/drive", json={"folder": "Checklist"}).json()
        assert {f["status"] for f in boss("GET", f"/api/ext/guidelines/drive/{again['job']}").json()["files"]} == {"known"}   # the same files cost nothing
        missing = boss("POST", "/api/ext/guidelines/drive", json={"folder": "No Such Folder"}).json()
        failed = boss("GET", f"/api/ext/guidelines/drive/{missing['job']}").json()
        assert failed["state"] == "failed" and "No folder named" in failed["message"]
        assert boss("GET", "/api/ext/guidelines/drive/nope").status_code == 404
    finally:
        set_provider_override(None)


class PhotoProvider(LearnProvider):
    """Also reads the picture requirements of a guideline file and looks at pictures."""

    def __init__(self, verdict="ok", risk="low"):
        super().__init__()
        self.verdict, self.risk, self.seen = verdict, risk, []

    def analyze(self, system, user):
        if "guideline document into rules" in system:
            self.prompts.append((system, user))
            return AIResult(text=json.dumps({"rules": [], "photo_requirements": ["Every signage photo must show the whole sign, in focus.", "No customer or staff face may be clearly visible."]}), model="fake-1")
        return super().analyze(system, user)

    def analyze_image(self, system, user, image, media_type="image/jpeg"):
        self.seen.append((user, len(image), media_type))
        return AIResult(text=json.dumps({"shows": "A black sign on a stand in a hallway.", "matches_question": "yes", "match_note": "It is signage.",
                                         "requirements": [{"rule": "Every signage photo must show the whole sign, in focus.", "met": "yes", "note": "Whole sign visible"},
                                                          {"rule": "No customer or staff face may be clearly visible.", "met": "yes", "note": "No faces"}],
                                         "quality": ["slightly dark"], "ai_generated": {"risk": self.risk, "signs": ["clean lighting"]}, "verdict": self.verdict, "summary": "Looks like genuine signage."}), model="fake-1")


def _picture(kind="jpeg", exif=True):
    import io

    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    img = Image.new("RGB", (640, 480), (90, 110, 130))
    buf = io.BytesIO()
    if kind == "png":
        info = PngInfo()
        info.add_text("parameters", "a photo of a sign, Steps: 20, Sampler: Euler a, CFG scale: 7")
        img.save(buf, "PNG", pnginfo=info)
    else:
        ex = Image.Exif()
        if exif:
            ex[271], ex[272], ex[306] = "Apple", "iPhone 15", "2026:11:23 10:15:00"
        img.save(buf, "JPEG", exif=ex.tobytes() if exif else b"")
    return buf.getvalue()


def test_pictures_are_checked_against_the_question_and_the_clients_picture_requirements(api, ctx):
    import base64

    prov = PhotoProvider()
    set_provider_override(prov)
    try:
        b64 = lambda raw: base64.b64encode(raw).decode()                                                       # noqa: E731
        boss, editor = _auth(_token("manager")), _auth(_token("qc"))
        brief = _docx("\n".join(f"Rule {i}: every signage photo must show the whole sign and the photo must be in focus, with no faces." for i in range(1, 8)))
        up = boss("POST", "/api/ext/guidelines/upload", json={"client_name": "Acme Retail", "filename": "photo rules.docx", "content_b64": b64(brief)}).json()
        assert up["status"] == "learned" and up["photo_rules"] == 2                                          # the picture requirements were found in the file

        body = {"client_hint": "Acme Retail: Store 978", "question": "9.2. Please provide pictures for signage.", "image_b64": b64(_picture())}
        got = editor("POST", "/api/ext/images/check", json=body).json()
        assert got["status"] == "ok" and got["verdict"] == "ok" and got["matches_question"] == "yes" and got["requirements_given"] == 2
        assert got["metadata"]["camera"] == "Apple iPhone 15" and got["metadata"]["taken"] and got["metadata"]["ai_marker"] is None
        assert [r["met"] for r in got["requirements"]] == ["yes", "yes"] and got["ai_generated"]["risk"] == "low" and "never proof" in got["ai_generated"]["note"]
        user_prompt, size, mime = prov.seen[-1]
        assert "Please provide pictures for signage" in user_prompt and "whole sign, in focus" in user_prompt and mime == "image/jpeg"   # the model gets the question and the rules

        stripped = editor("POST", "/api/ext/images/check", json={**body, "image_b64": b64(_picture(exif=False))}).json()
        assert stripped["metadata"]["camera"] is None and any("no camera information" in n.lower() for n in stripped["metadata"]["notes"])

        gen = editor("POST", "/api/ext/images/check", json={**body, "image_b64": b64(_picture("png"))}).json()               # an image generator left its prompt in the file
        assert gen["ai_generated"]["risk"] == "high" and gen["verdict"] == "review" and gen["ai_generated"]["signs"][0].startswith("File:")
        assert "parameters" in gen["metadata"]["ai_marker"]

        assert editor("POST", "/api/ext/images/check", json={**body, "image_b64": b64(b"not a picture at all")}).json()["status"] == "unreadable"
        assert editor("POST", "/api/ext/images/check", json={**body, "image_b64": "***"}).status_code == 422
        assert _auth("bad")("POST", "/api/ext/images/check", json=body).status_code == 401

        set_provider_override(PhotoProvider(verdict="ok", risk="high"))
        assert editor("POST", "/api/ext/images/check", json=body).json()["verdict"] == "review"                             # a high AI risk never ends as "ok"
        set_provider_override(RoutingProvider())                                                                             # a provider that cannot see pictures
        assert "cannot look at pictures" in editor("POST", "/api/ext/images/check", json=body).json()["message"]
        set_provider_override(None)
        none = editor("POST", "/api/ext/images/check", json={**body, "image_b64": b64(_picture("png"))}).json()
        assert none["status"] == "no_ai" and none["verdict"] == "problem" and none["metadata"]["ai_marker"]                  # without AI the file's own marker still shows
    finally:
        set_provider_override(None)


def test_spacing_and_full_stop_rules_see_the_white_space_the_word_corrector_cannot(api, ctx):
    call = _auth(_token("qc"))
    a1 = "The cashier  greeted me warmly at the door and  said hello."        # two double spaces
    a2 = "I paid at 5pm,the manager was present and she wrote my name down ,thanks for visiting. Then I left the store with my coffee"   # glued comma, " ,thanks", no final full stop
    a3 = "Boca Raton (5836), Florida Atlantic University in Florida"        # an address: no full stop is asked for
    a4 = "N/A"
    out = call("POST", "/api/ext/text/propose", json={"client_hint": "Acme Retail: Store 978", "answers": [
        {"id": "1", "question": "q1", "text": a1}, {"id": "2", "question": "q2", "text": a2}, {"id": "3", "question": "q3", "text": a3}, {"id": "4", "question": "q4", "text": a4}]}).json()
    by = {(e["qid"], e["reason"]): e for e in out["edits"]}
    spaces = [e for e in out["edits"] if e["qid"] == "1" and e["category"] == "spacing"]
    assert len(spaces) == 2 and all(a1[e["start"]:e["end"]] == e["original"] and "  " in e["original"] and "  " not in e["replacement"] for e in spaces)
    glued = by[("2", "A space is needed after the comma")]
    assert glued["original"] == "5pm,the" and glued["replacement"] == "5pm, the" and a2[glued["start"]:glued["end"]] == "5pm,the"
    before = by[("2", "No space before a comma; a space after it")]
    assert before["original"] == "down ,thanks" and before["replacement"] == "down, thanks"
    stop = by[("2", "The last sentence has no full stop")]
    assert stop["original"] == "coffee" and stop["replacement"] == "coffee." and a2[stop["start"]:stop["end"]] == "coffee"
    assert not [e for e in out["edits"] if e["qid"] in ("3", "4")]                                                       # an address and "N/A" are left alone

    # spaces that are not plain spaces (copied from Word / a PDF): they look like spaces, Grammarly flags them, so must the extension
    odd = "I put my own contraband paper    back into my shoe and the TCA\tsaid goodbye to me in the green　blouse today."
    got = call("POST", "/api/ext/text/propose", json={"client_hint": "Acme Retail: Store 978", "answers": [{"id": "9", "question": "q", "text": odd}]}).json()
    sp = [e for e in got["edits"] if e["category"] == "spacing"]
    assert [e["replacement"] for e in sp] == ["paper back", "TCA said", "green blouse"]
    assert all(odd[e["start"]:e["end"]] == e["original"] and e["original"] != e["replacement"] for e in sp)
    assert sp[0]["reason"] == "Extra space between words" and sp[1]["reason"].startswith("A special space")


def test_an_edit_marks_only_the_words_that_change_and_a_brand_spelling_is_left_alone(api, ctx):
    class Wide(RoutingProvider):
        def analyze(self, system, user):
            self.prompts.append((system, user))
            return AIResult(text=json.dumps({"edits": [
                {"original": "for a oil change", "replacement": "for an oil change", "reason": "Article before a vowel sound", "category": "grammar", "confidence": 0.95},
                {"original": "Correct- See below", "replacement": "Correct - See below", "reason": "Space around the dash", "category": "punctuation", "confidence": 0.95}]}), model="fake-1")

    set_provider_override(Wide())
    try:
        call = _auth(_token("qc"))
        text = "She asked for a oil change today.\nAbsolutely Correct- See below that I am sending it. Fake mail from iSN was read."
        out = call("POST", "/api/ext/text/propose", json={"client_hint": "Acme Retail: Store 978", "answers": [{"id": "1", "question": "q", "text": text}]}).json()
        by = {e["original"]: e for e in out["edits"]}
        assert by["a"]["replacement"] == "an" and text[by["a"]["start"]:by["a"]["end"]] == "a"                     # not the whole phrase
        assert "Correct-" in by and by["Correct-"]["replacement"] == "Correct -"                                        # and the dash fix is no longer refused as a "new proper word"
        assert not any(e["original"] == "iSN" for e in out["edits"])                                                  # a brand written with a capital inside is not a typo
        assert all(e["original"] != e["replacement"] for e in out["edits"])
    finally:
        set_provider_override(None)


def test_rules_typed_in_the_rules_tab_apply_to_their_project_and_a_human_note_is_kept(api, ctx):
    prov = RoutingProvider()
    set_provider_override(prov)
    try:
        call = _auth(_token("qc"))
        hint = "Acme Retail: Store 978 - Carlin NV"
        add = call("POST", "/api/ext/guidelines/manual", json={"client_hint": hint, "project": "Store 978 - Carlin NV", "scope": "project",
                                                                  "text": "- Always write the brand as Acme Retail\nspelling: Use 'lemonade' not 'lemonaid'\nab\n"}).json()
        assert add["status"] == "added" and add["added"] == 2 and add["scope"] == "project" and add["rules"][1]["type"] == "Spelling"
        again = call("POST", "/api/ext/guidelines/manual", json={"client_hint": hint, "project": "Store 978 - Carlin NV", "text": "- Always write the brand as Acme Retail\nspelling: Use 'lemonade' not 'lemonaid'"}).json()
        assert again["status"] == "known" and again["added"] == 0                                                          # the same lines twice make no duplicates
        assert call("POST", "/api/ext/guidelines/manual", json={"client_hint": "Unknown Brand", "text": "Some long rule here"}).json()["status"] == "no_client"
        assert call("POST", "/api/ext/guidelines/manual", json={"client_hint": hint, "text": "   \n a \n"}).json()["status"] == "empty"
        whole = call("POST", "/api/ext/guidelines/manual", json={"client_hint": hint, "project": "Store 978 - Carlin NV", "scope": "client", "text": "Never describe an associate by weight"}).json()
        assert whole["scope"] == "client"

        form = {"client_hint": hint, "answers": [{"id": "1", "question": "Comments", "text": "The cashier greet me warmly at the door."}]}
        same = call("POST", "/api/ext/text/propose", json={**form, "project": "Store 978 - Carlin NV"}).json()
        assert same["manual_rules"] == 3 and all(r["status"] == "draft" for r in same["rules"] if r.get("manual"))
        other = call("POST", "/api/ext/text/propose", json={**form, "project": "Store 12 - Reno NV"}).json()
        assert other["manual_rules"] == 1                                                                                   # only the whole-client rule follows to another project
        assert call("POST", "/api/ext/text/propose", json=form).json()["manual_rules"] == 1
        assert any("Always write the brand as Acme Retail" in u for _, u in prov.prompts if "<company_guidelines>" in u)   # typed rules reach the editor model

        n = call("POST", "/api/ext/text/note", json={"client_hint": hint, "project": "Store 978", "job": "639267", "original": "greet", "replacement": "greeted",
                                                      "decision": "dismissed", "text": "The shopper wrote this in present tense on purpose: quoted speech."})
        assert n.status_code == 200 and n.json()["saved"] is True
        from app.models import AuditEvent
        db = SessionLocal()
        ev = db.execute(select(AuditEvent).where(AuditEvent.action == "ext.edit_note")).scalars().all()
        assert len(ev) == 1 and ev[0].detail["note"].startswith("The shopper wrote") and ev[0].detail["decision"] == "dismissed"
        db.close()
        assert _auth("bad")("POST", "/api/ext/text/note", json={"original": "a", "text": "b"}).status_code == 401
    finally:
        set_provider_override(None)


def test_text_form_edits_for_a_web_page_form_with_character_positions(api, ctx):
    """Intelli-Shop style page: questions + answers in, edits with character ranges inside each answer out; the client comes from the page header."""
    prov = RoutingProvider()
    set_provider_override(prov)
    try:
        call = _auth(_token("qc"))
        a1 = "The cashier greet me warmly at the door.\nI recieved my order,quickly."
        a2 = "The store was clean."
        body = {"client_hint": "Acme Retail: Store 978 - Carlin NV", "answers": [
            {"id": "306378", "question": "Describe the greeting", "text": a1}, {"id": "302694", "question": "Outside comments", "text": a2}]}
        out = call("POST", "/api/ext/text/propose", json=body).json()
        assert out["company"]["client"] == "Acme Retail" and out["company"]["matched_by"] == "page header"
        assert any("QUESTION: Describe the greeting" in u for _, u in prov.prompts)                  # the model sees the form's own question
        by = {e["original"]: e for e in out["edits"]}
        assert "recieved" in by and by["recieved"]["qid"] == "306378" and a1[by["recieved"]["start"]:by["recieved"]["end"]] == "recieved"
        ai = next(e for e in out["edits"] if e["source"] == "ai")
        assert a1[ai["start"]:ai["end"]] == ai["original"] and ai["original"] == "greet" and ai["replacement"] == "greeted"      # only the word that changes is marked
        assert all(e["qid"] in ("306378", "302694") for e in out["edits"])
        by_site = call("POST", "/api/ext/text/propose", json={**body, "client_hint": "Master Form | acme-retail.com"}).json()
        assert by_site["company"]["client"] == "Acme Retail"                                           # the site name also names the client
        none = call("POST", "/api/ext/text/propose", json={**body, "client_hint": "Unknown Brand"}).json()
        assert none["company"] is None
        assert _auth("bad")("POST", "/api/ext/text/propose", json=body).status_code == 401
    finally:
        set_provider_override(None)
