"""End-to-end AI check against the configured provider, using ONLY fictional sample PDFs.

    python scripts/ai_smoke_test.py            # honours AI_PROVIDER / keys / AI_ALLOW_EXTERNAL from backend/.env
    python scripts/ai_smoke_test.py --allow-external   # allow the external provider for THIS process only (sample data)

Uses a throwaway database and storage directory; your real data is never touched or sent anywhere.
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--allow-external", action="store_true", help="permit an external provider for this run (sample data only)")
args = ap.parse_args()

tmp = tempfile.mkdtemp(prefix="msai-ai-smoke-")
os.environ.update({"ENV": "test", "DATABASE_URL": f"sqlite:///{Path(tmp, 'smoke.db').as_posix()}",
                   "STORAGE_ROOT": str(Path(tmp, "storage")), "JOBS_INLINE": "true"})
if args.allow_external:
    os.environ["AI_ALLOW_EXTERNAL"] = "true"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Client, Project, QCRun, User  # noqa: E402
from app.sample_docs import make_pair  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import pipeline, repository  # noqa: E402
from app.services.ai import resolve_provider  # noqa: E402
from app.services.rules_service import seed_baseline_rules  # noqa: E402
from app.storage import get_storage  # noqa: E402

provider, status, msg = resolve_provider()
print(f"Provider check: {status.value} — {msg}")
if provider is None:
    print("\nAI cannot run with this configuration. The smoke test will still run the deterministic pipeline "
          "and show how the run reports the missing AI honestly.\n")
else:
    print(f"Using {provider.name} / {provider.model} (external={provider.external})\n")

Base.metadata.create_all(engine)
db = SessionLocal()
seed_baseline_rules(db)
c = Client(name="Smoke Client", folder="Smoke_Client")
db.add(c)
db.flush()
p = Project(client_id=c.id, name="Smoke Project", folder="Smoke_Project")
db.add(p)
mgr = User(email="m@x.com", name="Mgr", role="manager", password_hash=hash_password("x"))
ed = User(email="e@x.com", name="Ed", role="editor", password_hash=hash_password("x"))
db.add_all([mgr, ed])
db.commit()

raw, edited, _ = make_pair(1, "mixed")
storage = get_storage()
form = repository.ingest_pdf(db, storage, mgr, project=p, form_code="SMOKE1", kind="raw", original_filename="SMOKE1_1.pdf", data=raw).form
form.assigned_editor_id = ed.id
db.commit()
sub = repository.ingest_pdf(db, storage, ed, project=p, form_code="SMOKE1", kind="edited", original_filename="SMOKE1_2.pdf", data=edited)
run = pipeline.create_run(db, form, sub.document, ed)
pipeline.execute_run(db, run.id)
db.expire_all()
run = db.get(QCRun, run.id)

print(f"Run status: {run.status}   AI status: {run.ai_status}")
print(f"AI message: {run.ai_message}")
for s in run.stages:
    print(f"  [{s['status']:>7}] {s['name']}: {s.get('detail')}")
ai = run.stats.get("ai") or {}
if ai:
    print(f"\nAI model: {ai.get('model')}  tokens: {ai.get('usage')}  request: {ai.get('request_id')}")
    print(f"Returned {ai.get('returned')} · accepted {ai.get('accepted')} · discarded as unverifiable {ai.get('discarded_unverified')}")
print("\nFindings:")
for f in run.findings:
    tag = f.source.upper()
    print(f"  [{tag:13}] {f.severity:8} {f.type:18} p.{f.edited_page or f.raw_page} · {f.title}")
    if f.source == "ai":
        print(f"                 evidence: {f.edited_excerpt or f.raw_excerpt!r}")
        print(f"                 rule: {f.rule_label or '(none cited)'} · confidence: {f.confidence}")
ok = run.ai_status == "completed"
print("\nRESULT:", "AI analysis completed end to end." if ok else "AI analysis did NOT run (see message above); deterministic checks only.")
sys.exit(0 if ok else 2)
