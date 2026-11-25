"""Production creates tables ONLY through Alembic (create_all is skipped when ENV=production). A model without a migration works in every test and fails on the
host with "relation does not exist": this test builds a fresh database with `alembic upgrade head` and demands every model table in it."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app import models  # noqa: F401  (registers every table)
from app.db import Base

BACKEND = Path(__file__).resolve().parent.parent


def test_every_model_table_is_created_by_the_migrations(tmp_path):
    db = tmp_path / "migrated.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db.as_posix()}", "ENV": "production", "SECRET_KEY": "migration-test-secret-key-0123456789abcdef"}
    run = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=240)
    assert run.returncode == 0, run.stderr[-2000:]
    created = {r[0] for r in sqlite3.connect(db).execute("select name from sqlite_master where type='table'")}
    missing = sorted(set(Base.metadata.tables) - created)
    assert not missing, f"These tables exist in the models but no Alembic migration creates them (production would fail): {missing}"
