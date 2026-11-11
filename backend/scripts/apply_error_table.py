"""Switch an existing database to the fixed QC error table and re-check the forms waiting for QC.

    python scripts/apply_error_table.py            # dry run: what would change
    python scripts/apply_error_table.py --apply

1. Every rule gets its sub-type from the error table (rules that are not on the table keep none and stop running).
2. Every form still in QC Review gets a fresh analysis, so its findings follow the table (Grammar 5 · Missed Instruction 15 ·
   Contradiction 20 · Wrong Location 25). Earlier runs stay in the history; forms that already have a human decision on the
   current run are left alone.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.enums import FormStatus  # noqa: E402
from app.models import Form, ReviewAttempt, RuleVersion  # noqa: E402
from app.services import error_taxonomy as tx, pipeline, workflow  # noqa: E402
from app.services.rules_service import in_qc, seed_baseline_rules, sync_taxonomy  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = SessionLocal()
    forms = db.execute(select(Form).where(Form.status == FormStatus.QC_REVIEW.value)).scalars().all()
    todo, kept = [], []
    for f in forms:
        run = workflow.current_run(db, f)
        edited = workflow.latest_edited(f)
        reviewed = run is not None and any(x.review_status != "pending" for x in run.findings)
        decided = db.execute(select(ReviewAttempt).where(ReviewAttempt.form_id == f.id, ReviewAttempt.run_id == (run.id if run else -1))).first()
        (kept if (reviewed or decided or not edited) else todo).append(f)
    print(f"{len(forms)} form(s) in QC Review: {len(todo)} to re-check, {len(kept)} left as they are "
          f"(already reviewed, or no edited PDF yet).")
    versions = db.execute(select(RuleVersion)).scalars().all()
    on_table = [v for v in versions if tx.RULE_SUBTYPE.get(v.rule.rule_key) or v.check_type == "template_profile"]
    print(f"{len(versions)} rule version(s): {len(on_table)} belong to the error table, {len(versions) - len(on_table)} will stop running.")
    if not args.apply:
        print("\nDry run only. Re-run with --apply.")
        return
    print(f"new baseline rules: {seed_baseline_rules(db)}, rule versions updated: {sync_taxonomy(db)}")
    run_ids = [pipeline.create_run(db, f, workflow.latest_edited(f), None).id for f in todo]
    db.close()
    for rid in run_ids:
        pipeline.run_job(rid)
    print(f"re-checked {len(run_ids)} form(s).")
    db = SessionLocal()
    active = [v for v in db.execute(select(RuleVersion)).scalars() if in_qc(v) and v.status == "published"]
    print(f"{len(active)} published rule version(s) are active in QC.")


if __name__ == "__main__":
    main()
