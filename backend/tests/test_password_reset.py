"""Forgot password from the extension: e-mailed one-time code, new password, lock-out lifted; safe against guessing and enumeration."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Notification, User
from app.services import mailer

from .conftest import PASSWORD

NEW = "Brand-New-Pass-9"


class Outbox:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True

    def code(self):
        line = next(ln for ln in self.sent[-1][2].splitlines() if "code is:" in ln)
        return line.split(":")[1].strip()


def _mail(monkeypatch):
    box = Outbox()
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", box.send)
    return box


def _post(path, **body):
    return TestClient(app).post(path, json=body)


def test_a_code_is_e_mailed_and_sets_a_new_password_and_lifts_the_lock_out(api, ctx, monkeypatch):
    box = _mail(monkeypatch)
    for _ in range(5):
        assert _post("/api/ext/login", email="qc@test.local", password="wrong-wrong-1").status_code == 401
    assert _post("/api/ext/login", email="qc@test.local", password=PASSWORD).status_code == 429          # locked out, like the screenshot

    known = _post("/api/ext/forgot", email="QC@test.local ")
    unknown = _post("/api/ext/forgot", email="nobody@test.local")
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()          # no way to tell which e-mails have accounts
    assert len(box.sent) == 1 and box.sent[0][0] == "qc@test.local" and "reset code" in box.sent[0][1]
    code = box.code()
    assert len(code.replace("-", "")) == 8

    assert _post("/api/ext/reset", email="qc@test.local", code="AAAA-BBBB", new_password=NEW).status_code == 400    # wrong code
    weak = _post("/api/ext/reset", email="qc@test.local", code=code, new_password="short")
    assert weak.status_code == 422 and "at least" in weak.json()["detail"]                                         # weak password does not use up the code
    ok = _post("/api/ext/reset", email="qc@test.local", code=code.lower().replace("-", ""), new_password=NEW)
    assert ok.status_code == 200 and "changed" in ok.json()["message"]
    assert _post("/api/ext/reset", email="qc@test.local", code=code, new_password=NEW + "x").status_code == 400    # one use only

    assert _post("/api/ext/login", email="qc@test.local", password=PASSWORD).status_code == 401                    # the old password is gone
    assert _post("/api/ext/login", email="qc@test.local", password=NEW).status_code == 200                         # and the lock-out is lifted


def test_a_code_dies_after_five_wrong_tries_and_codes_are_limited_per_hour(api, ctx, monkeypatch):
    box = _mail(monkeypatch)
    _post("/api/ext/forgot", email="qc@test.local")
    code = box.code()
    for _ in range(5):
        assert _post("/api/ext/reset", email="qc@test.local", code="WRONG-123", new_password=NEW).status_code == 400
    assert _post("/api/ext/reset", email="qc@test.local", code=code, new_password=NEW).status_code == 400          # guessing killed the code
    for _ in range(5):
        _post("/api/ext/forgot", email="qc@test.local")
    assert len(box.sent) == 3                                                                                       # at most 3 e-mails an hour


def test_without_a_mail_server_the_super_admins_are_notified_instead(api, ctx, monkeypatch):
    monkeypatch.setattr(mailer, "configured", lambda: False)
    out = _post("/api/ext/forgot", email="qc@test.local").json()
    assert "not set up" in out["message"] and "Super Admins" in out["message"]
    db = SessionLocal()
    sa = db.execute(select(User).where(User.email == "sa@test.local")).scalar_one()
    notes = db.execute(select(Notification).where(Notification.user_id == sa.id, Notification.kind == "password_reset")).scalars().all()
    assert len(notes) == 1 and "qc@test.local" in notes[0].message
    db.close()
    assert _post("/api/ext/reset", email="qc@test.local", code="ANYTHING", new_password=NEW).status_code == 400    # no code was ever created
    assert not mailer.configured()
