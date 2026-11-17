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
