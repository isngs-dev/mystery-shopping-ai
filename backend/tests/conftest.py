import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="msai-test-")
os.environ.update({
    "ENV": "test", "DATABASE_URL": f"sqlite:///{Path(_tmp, 'test.db').as_posix()}", "STORAGE_ROOT": str(Path(_tmp, "storage")),
    "JOBS_INLINE": "true", "AI_PROVIDER": "none", "OCR_ENABLED": "true", "SECRET_KEY": "test-secret-key-for-unit-tests-123456",
    "LOGIN_MAX_FAILURES": "5", "AI_EDIT_ON_UPLOAD": "false", "IMPORT_LOCAL_ROOT": str(Path(_tmp, "imports")),
    # tests must never see the developer's real Google / AI credentials from backend/.env
    "DEMO_LOGIN_DOMAINS": "", "DEMO_LOGIN_SHOW_SAMPLES": "true",
    "GOOGLE_SERVICE_ACCOUNT_FILE": "", "GOOGLE_SERVICE_ACCOUNT_JSON": "", "GOOGLE_DRIVE_FOLDER_ID": "", "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app, bootstrap  # noqa: E402
from app.models import Client, Project, User, UserProjectAccess  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.ai import set_provider_override  # noqa: E402
from app.storage import reset_storage  # noqa: E402

PASSWORD = "Test-Passw0rd-123"
HDR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    import shutil
    import stat

    def _force(func, path, _exc):  # stored originals are read-only; make them deletable for test cleanup only
        os.chmod(path, stat.S_IWRITE)
        func(path)

    if Path(_tmp, "storage").exists():
        shutil.rmtree(Path(_tmp, "storage"), onerror=_force)
    reset_storage()
    set_provider_override(None)
    bootstrap()
    db = SessionLocal()
    client = Client(name="Acme Retail", folder="Acme_Retail")
    other = Client(name="Other Co", folder="Other_Co")
    db.add_all([client, other])
    db.flush()
    p1 = Project(client_id=client.id, name="Store Visits", folder="Store_Visits")
    p2 = Project(client_id=other.id, name="Secret Project", folder="Secret_Project")
    db.add_all([p1, p2])
    db.flush()
    users = {}
    for key, role in [("sa", "super_admin"), ("admin", "admin"), ("manager", "manager"), ("editor", "editor"),
                      ("editor2", "editor"), ("qc", "qc_reviewer"), ("qc2", "qc_reviewer"), ("lead", "ai_lead"),
                      ("owner", "process_owner"), ("poc", "client_poc"), ("viewer", "reporting")]:
        u = User(email=f"{key}@test.local", name=key.upper(), role=role, password_hash=hash_password(PASSWORD))
        db.add(u)
        db.flush()
        users[key] = u.id
        if role in ("admin", "manager", "qc_reviewer", "client_poc", "reporting"):
            db.add(UserProjectAccess(user_id=u.id, project_id=p1.id))
    db.commit()
    ctx = {"users": users, "p1": p1.id, "p2": p2.id}
    db.close()
    yield ctx


@pytest.fixture
def ctx(fresh_db):
    return fresh_db


class Api:
    def __init__(self):
        self.c = TestClient(app)

    def login(self, key: str):
        self.c = TestClient(app)
        r = self.c.post("/api/auth/login", json={"email": f"{key}@test.local", "password": PASSWORD})
        assert r.status_code == 200, r.text
        return r.json()

    def req(self, method, url, **kw):
        kw.setdefault("headers", {}).update(HDR)
        return self.c.request(method, url, **kw)

    def get(self, url, **kw):
        return self.req("GET", url, **kw)

    def post(self, url, **kw):
        return self.req("POST", url, **kw)

    def put(self, url, **kw):
        return self.req("PUT", url, **kw)

    def patch(self, url, **kw):
        return self.req("PATCH", url, **kw)


@pytest.fixture
def api():
    return lambda key: _as(key)


def _as(key: str) -> Api:
    a = Api()
    a.login(key)
    return a
