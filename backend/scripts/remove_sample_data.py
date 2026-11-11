"""Remove the seeded SAMPLE data (demo clients/projects/forms/documents and the demo accounts) so only real Drive data is left.

    python scripts/remove_sample_data.py            # dry run: shows what would go
    python scripts/remove_sample_data.py --apply    # backs the database up first, then deletes

Everything flagged is_sample goes: sample clients, projects, forms (with documents, QC runs, findings, comments, notifications, audit
entries), the sample files in storage (moved to a backup folder, not erased) and the demo accounts. Real data that merely points at a
demo account (a document uploaded by one, an audit line written by one) is kept: the account link is cleared and, for the audit log, the
person's name stays in actor_label.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401

KEYED = {"projects", "forms", "clients", "documents", "qc_runs"}     # tables whose rows can be sample rows themselves


def _in(ids: set[int]) -> str:
    return "(" + ",".join(str(i) for i in sorted(ids)) + ")" if ids else "(NULL)"


def collect(db) -> dict[str, set[int]]:
    def ids(sql: str) -> set[int]:
        return {r[0] for r in db.execute(text(sql))}

    s = {"users": ids("select id from users where is_sample = 1"), "clients": ids("select id from clients where is_sample = 1"),
         "projects": ids("select id from projects where is_sample = 1")}
    s["projects"] |= ids(f"select id from projects where client_id in {_in(s['clients'])}")
    s["forms"] = ids(f"select id from forms where is_sample = 1 or project_id in {_in(s['projects'])}")
    s["documents"] = ids(f"select id from documents where is_sample = 1 or form_id in {_in(s['forms'])}")
    s["qc_runs"] = ids(f"select id from qc_runs where form_id in {_in(s['forms'])}")
    return s


def plan(db, s: dict[str, set[int]]):
    """(table, where-clause) deletions in dependency order: children before parents."""
    out = []
    for t in reversed(Base.metadata.sorted_tables):
        if t.name in ("users",):
            continue
        conds = []
        if t.name in KEYED:
            conds.append(f"id in {_in(s[t.name])}")
        for c in t.columns:
            for fk in c.foreign_keys:
                tgt = fk.column.table.name
                if tgt in KEYED and tgt != t.name:
                    conds.append(f'"{c.name}" in {_in(s[tgt])}')
        if conds:
            out.append((t.name, " or ".join(conds)))
    return out


def user_links():
    """Every column that points at users: (table, column, nullable)."""
    rows = []
    for t in Base.metadata.sorted_tables:
        if t.name == "users":
            for c in t.columns:
                if c.name == "created_by":
                    rows.append((t.name, c.name, True))
            continue
        for c in t.columns:
            if any(fk.column.table.name == "users" for fk in c.foreign_keys):
                rows.append((t.name, c.name, bool(c.nullable)))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = SessionLocal()
    s = collect(db)
    print({k: len(v) for k, v in s.items()})
    steps = plan(db, s)
    print("\nRows that go with the sample projects:")
    for name, where in steps:
        n = db.execute(text(f'select count(*) from "{name}" where {where}')).scalar()
        if n:
            print(f"  {name:<20}{n}")
    u = _in(s["users"])
    print("\nLinks from real data to the demo accounts (cleared, not deleted):")
    for t, c, nullable in user_links():
        if t in {n for n, _ in steps} and t != "users" and False:
            continue
        n = db.execute(text(f'select count(*) from "{t}" where "{c}" in {u}')).scalar()
        if n:
            print(f"  {t}.{c:<16}{n}  -> {'cleared' if nullable else 'row removed'}")
    if not args.apply:
        print("\nDry run only. Re-run with --apply to delete.")
        return

    # ---- backup: database (consistent copy) and the sample files
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    data = BACKEND / "data"
    backup_db = data / f"app.backup-before-sample-removal-{stamp}.db"
    src = sqlite3.connect(str(engine.url.database))
    dst = sqlite3.connect(str(backup_db))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    print(f"\nDatabase backup: {backup_db}")
    keep = data / f"removed-sample-files-{stamp}"
    moved = 0
    root = data / "storage"
    for (key,) in db.execute(text(f"select storage_key from documents where id in {_in(s['documents'])}")).all():
        p = root / key
        if p.is_file():
            target = keep / key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(target))
            moved += 1

    # ---- delete
    admin = db.execute(text("select id from users where role = 'super_admin' and is_sample = 0 and is_active = 1 order by id")).scalar()
    for name, where in steps:
        db.execute(text(f'delete from "{name}" where {where}'))
    db.execute(text(f"update audit_events set actor_label = (select name from users where users.id = audit_events.actor_id) "
                    f"where actor_id in {u} and actor_label is null"))
    for t, c, nullable in user_links():
        if nullable:
            if t == "rule_versions" and c == "owner_id" and admin:
                db.execute(text(f'update rule_versions set owner_id = {admin} where owner_id in {u}'))
            else:
                db.execute(text(f'update "{t}" set "{c}" = NULL where "{c}" in {u}'))
        else:
            db.execute(text(f'delete from "{t}" where "{c}" in {u}'))
    db.execute(text(f"delete from users where id in {u}"))
    db.commit()
    print(f"Removed. {moved} sample file(s) moved to {keep}")
    print("Left:", {t: db.execute(text(f"select count(*) from {t}")).scalar() for t in ("clients", "projects", "forms", "documents", "users")})


if __name__ == "__main__":
    main()
