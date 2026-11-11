"""Create or update the working team's login accounts. Safe to re-run.

    python scripts/create_team.py     # creates missing users, renames/re-roles known ones, grants project access, writes secrets/team-logins.txt

New passwords are generated here, hashed in the database and written to backend/secrets/team-logins.txt (git-ignored).
Accounts can sign in with the one-click panel (development, this PC) or with the password; change passwords before any shared deployment.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.audit import audit  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Project, User, UserProjectAccess  # noqa: E402
from app.security import generate_temp_password, hash_password  # noqa: E402
from app.services import drive_sync  # noqa: E402

DOMAIN = "isngs.com"
# (login id, name, role, previous login id to take over)
TEAM = [
    ("isn", "ISN", "super_admin", "vikram.sethi"),       # sees everything and what every user is doing; no Drive import
    ("shreyas", "Shreyas", "admin", None),               # overall working updates; no Drive import
    ("rahat", "Rahat", "qc_reviewer", None),
    ("renuka", "Renuka", "qc_reviewer", "priya.nair"),
    ("swapnali", "Swapnali", "qc_reviewer", "arjun.menon"),
    ("saradhya", "Saradhya", "qc_reviewer", "kavya.iyer"),
    ("devansh", "Devansh", "qc_reviewer", "rohan.gupta"),
    ("geeta.k", "Geeta", "manager", None),               # QC lead / reviewer AND manager: assigns jobs, QC rules, workload, the only Drive import / sync user
]
NEEDS_ALL_PROJECTS = {"admin", "manager", "qc_reviewer"}      # editors only see forms assigned to them
out = Path(__file__).resolve().parent.parent / "secrets" / "team-logins.txt"
known = dict(re.findall(r"(\S+@" + re.escape(DOMAIN) + r")\s+(Tmp-\S+)", out.read_text(encoding="utf-8"))) if out.exists() else {}

db = SessionLocal()
sa = db.execute(select(User).where(User.role == "super_admin", User.is_active.is_(True)).order_by(User.id)).scalars().first()
rows, changed = [], 0
for login, name, role, previous in TEAM:
    email = f"{login}@{DOMAIN}"
    u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    old = db.execute(select(User).where(User.email == f"{previous}@{DOMAIN}")).scalar_one_or_none() if previous and not u else None
    pw = None
    if u:
        if (u.name, u.role) != (name, role):
            u.name, u.role, u.token_version = name, role, (u.token_version or 0) + 1
            audit(db, sa, "user.updated", entity_type="user", entity_id=u.id, detail={"email": email, "role": role, "via": "scripts/create_team.py"})
            changed += 1
        pw = known.get(email)
    elif old:       # a placeholder account becomes this person: same record, new login, fresh password, old sessions revoked
        pw = generate_temp_password()
        old.email, old.name, old.role, old.password_hash = email, name, role, hash_password(pw)
        old.token_version, old.must_change_password, old.is_active = (old.token_version or 0) + 1, False, True
        u = old
        audit(db, sa, "user.updated", entity_type="user", entity_id=u.id, detail={"email": email, "role": role, "renamed_from": previous, "via": "scripts/create_team.py"})
        changed += 1
    else:
        pw = generate_temp_password()
        u = User(email=email, name=name, role=role, password_hash=hash_password(pw), must_change_password=False, created_by=sa.id if sa else None)
        db.add(u)
        db.flush()
        audit(db, sa, "user.created", entity_type="user", entity_id=u.id, detail={"email": email, "role": role, "via": "scripts/create_team.py"})
        changed += 1
    rows.append((role, name, email, pw or "(set earlier - unchanged)"))
db.flush()

# anyone at the team domain who is not on the list any more is deactivated, never deleted (audit trail)
listed = {f"{t[0]}@{DOMAIN}" for t in TEAM}
for u in db.execute(select(User).where(User.email.like(f"%@{DOMAIN}"), User.is_active.is_(True))).scalars():
    if u.email not in listed:
        u.is_active, u.token_version = False, (u.token_version or 0) + 1
        audit(db, sa, "user.deactivated", entity_type="user", entity_id=u.id, detail={"email": u.email, "via": "scripts/create_team.py"})
        changed += 1

granted = 0
projects = db.execute(select(Project)).scalars().all()
for u in db.execute(select(User).where(User.email.like(f"%@{DOMAIN}"), User.is_active.is_(True), User.role.in_(NEEDS_ALL_PROJECTS))).scalars():
    for p in projects:
        if not db.get(UserProjectAccess, (u.id, p.id)):
            db.add(UserProjectAccess(user_id=u.id, project_id=p.id, granted_by=sa.id if sa else None))
            granted += 1

# Drive import / sync belongs to the manager: if automatic sync was switched on by someone else, hand it to her
geeta = db.execute(select(User).where(User.email == f"geeta.k@{DOMAIN}")).scalar_one()
cfg = drive_sync.load(db)
if cfg.get("enabled") and cfg.get("enabled_by") != geeta.id:
    drive_sync.save(db, {"enabled_by": geeta.id})
    print(f"Automatic sync is ON; it now runs as {geeta.name}.")
db.commit()

out.parent.mkdir(exist_ok=True)
text = "role         name           login id                     password\n" + "\n".join(f"{r:<12} {n:<14} {e:<28} {p}" for r, n, e, p in rows) + "\n"
out.write_text(text, encoding="utf-8")
print(text)
print(f"{changed} account change(s), {granted} project access grant(s) added. Saved in {out}")
