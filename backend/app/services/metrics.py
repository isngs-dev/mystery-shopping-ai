"""Dashboard metrics and report datasets, computed from real database rows and scoped to the viewer.

Definitions (also surfaced in the UI):
- Unique forms are counted once per (project, Form ID). Review attempts / resubmissions are counted separately.
- Date range filters forms by intake (created) date.
- First-pass approval = forms whose FIRST review attempt was an approval / forms with at least one review attempt.
- Re-edit rate = forms with at least one re-edit decision / forms with at least one review attempt.
- QC coverage = forms with at least one review attempt / forms with an edited PDF submitted.
- Turnaround = intake to approval, in hours, for approved/closed forms.
- AI precision is only reported from reviewer decisions and only above a minimum sample size.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import utcnow
from ..models import Finding, Form, Project, QCRun, ReviewAttempt, User
from ..permissions import form_scope_clause
from . import error_taxonomy as tx
from .error_taxonomy import category_label

OPEN_STATES = ("pending", "in_progress", "qc_review", "re_edit_required", "manual_review_required", "blocked")
MIN_AI_SAMPLE = 20


@dataclass
class Filters:
    date_from: datetime | None = None
    date_to: datetime | None = None
    project_id: int | None = None
    client_id: int | None = None
    severity: str | None = None
    data: str = "all"  # all | real | sample
    editor_id: int | None = None

    def as_dict(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in self.__dict__.items() if v not in (None, "all")}


def load_forms(db: Session, user: User, f: Filters) -> list[Form]:
    stmt = select(Form).where(form_scope_clause(db, user)).options(
        selectinload(Form.documents), selectinload(Form.project).selectinload(Project.client),
        selectinload(Form.assigned_editor), selectinload(Form.assigned_reviewer))
    if f.project_id:
        stmt = stmt.where(Form.project_id == f.project_id)
    if f.client_id:
        stmt = stmt.where(Form.project_id.in_(select(Project.id).where(Project.client_id == f.client_id)))
    if f.editor_id:
        stmt = stmt.where(Form.assigned_editor_id == f.editor_id)
    if f.date_from:
        stmt = stmt.where(Form.created_at >= f.date_from)
    if f.date_to:
        stmt = stmt.where(Form.created_at <= f.date_to)
    if f.data != "all":
        stmt = stmt.where(Form.is_sample.is_(f.data == "sample"))
    return list(db.execute(stmt).scalars().unique())


def _hours(a: datetime | None, b: datetime | None) -> float | None:
    return round((b - a).total_seconds() / 3600, 1) if a and b else None


def _pct(n: float, d: float) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def _avg(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def _week(dt: datetime) -> str:
    d = (dt - timedelta(days=dt.weekday())).date()
    return d.isoformat()


class Dataset:
    """Everything needed by dashboard + reports, loaded once per request."""

    def __init__(self, db: Session, user: User, f: Filters):
        self.db, self.user, self.f = db, user, f
        self.forms = load_forms(db, user, f)
        ids = [x.id for x in self.forms]
        self.by_id = {x.id: x for x in self.forms}
        self.attempts: list[ReviewAttempt] = []
        self.findings: list[Finding] = []
        self.runs: list[QCRun] = []
        if ids:
            self.attempts = list(db.execute(select(ReviewAttempt).where(ReviewAttempt.form_id.in_(ids)).order_by(ReviewAttempt.id)).scalars())
            fq = select(Finding).where(Finding.form_id.in_(ids))
            if f.severity:
                fq = fq.where(Finding.severity == f.severity)
            self.findings = list(db.execute(fq).scalars())
            self.runs = list(db.execute(select(QCRun).where(QCRun.form_id.in_(ids)).order_by(QCRun.id)).scalars())
        self.users = {u.id: u for u in db.execute(select(User)).scalars()}
        self.attempts_by_form: dict[int, list[ReviewAttempt]] = defaultdict(list)
        for a in self.attempts:
            self.attempts_by_form[a.form_id].append(a)
        self.confirmed = [x for x in self.findings if x.review_status in ("accepted", "edited")]

    # --- per-form helpers
    def has_edited(self, fm: Form) -> bool:
        return any(d.kind == "edited" for d in fm.documents)

    def first_pass(self, fm: Form) -> bool | None:
        at = self.attempts_by_form.get(fm.id)
        return (at[0].decision == "approved") if at else None

    def reedited(self, fm: Form) -> bool:
        return any(a.decision == "re_edit_required" for a in self.attempts_by_form.get(fm.id, []))

    def turnaround(self, fm: Form) -> float | None:
        return _hours(fm.created_at, fm.approved_at) if fm.approved_at else None

    def group_stats(self, forms: list[Form]) -> dict:
        decided = [x for x in forms if self.attempts_by_form.get(x.id)]
        submitted = [x for x in forms if self.has_edited(x)]
        approved = [x for x in forms if x.status in ("approved", "closed")]
        conf = [c for c in self.confirmed if c.form_id in {x.id for x in forms}]
        return {
            "forms": len(forms), "submitted": len(submitted), "decided_forms": len(decided),
            "attempts": sum(len(self.attempts_by_form.get(x.id, [])) for x in forms),
            "approved": len(approved), "open": sum(1 for x in forms if x.status in OPEN_STATES),
            "first_pass_rate": _pct(sum(1 for x in decided if self.first_pass(x)), len(decided)),
            "re_edit_rate": _pct(sum(1 for x in decided if self.reedited(x)), len(decided)),
            "avg_turnaround_h": _avg([self.turnaround(x) for x in approved]),
            "confirmed_findings": len(conf),
            "findings_per_form": round(len(conf) / len(decided), 2) if decided else None,
        }


# ---------------------------------------------------------------------------------- dashboard
def dashboard(db: Session, user: User, f: Filters) -> dict:
    ds = Dataset(db, user, f)
    forms = ds.forms
    st = Counter(x.status for x in forms)
    eligible = [x for x in forms if ds.has_edited(x) and any(d.kind == "raw" for d in x.documents)
                and x.status not in ("approved", "closed", "blocked")]
    decided = [x for x in forms if ds.attempts_by_form.get(x.id)]
    submitted = [x for x in forms if ds.has_edited(x)]
    approved = [x for x in forms if x.status in ("approved", "closed")]
    now = utcnow()
    overdue = sum(1 for x in forms if x.due_at and x.due_at < now and x.status in OPEN_STATES)
    kpis = {
        "total_forms": len(forms), "eligible_for_qc": len(eligible), "pending": st["pending"],
        "backlog": sum(st[s] for s in OPEN_STATES), "under_review": st["qc_review"], "in_progress": st["in_progress"],
        "re_edit_required": st["re_edit_required"], "approved": len(approved), "manual_review": st["manual_review_required"],
        "blocked": st["blocked"], "overdue": overdue,
        "qc_coverage": _pct(len(decided), len(submitted)),
        "first_pass_approval_rate": _pct(sum(1 for x in decided if ds.first_pass(x)), len(decided)),
        "avg_turnaround_h": _avg([ds.turnaround(x) for x in approved]),
        "re_edit_rate": _pct(sum(1 for x in decided if ds.reedited(x)), len(decided)),
        "review_attempts": len(ds.attempts), "forms_reviewed": len(decided),
        "resubmissions": sum(max(0, sum(1 for d in x.documents if d.kind == "edited") - 1) for x in forms),
    }
    # weekly trend of decisions
    trend: dict[str, Counter] = defaultdict(Counter)
    for a in ds.attempts:
        if (f.date_from and a.decided_at < f.date_from) or (f.date_to and a.decided_at > f.date_to):
            continue
        trend[_week(a.decided_at)][a.decision] += 1
    weekly = [{"week": w, "approved": c["approved"], "re_edit": c["re_edit_required"],
               "manual": c["manual_review_required"] + c["blocked"]} for w, c in sorted(trend.items())][-16:]
    # confirmed findings by category / severity
    cat = Counter(category_label(c.category) for c in ds.confirmed)
    sev = Counter(c.severity for c in ds.findings)
    # repeat errors: category confirmed in >= 2 attempts of the same form, per week
    per_form_cat: dict[tuple[int, str], set[int]] = defaultdict(set)
    for c in ds.confirmed:
        per_form_cat[(c.form_id, category_label(c.category))].add(c.run_id)
    repeat_cats = Counter(cat_ for (fid, cat_), runs in per_form_cat.items() if len(runs) >= 2)
    # workload
    def load(group_key, label):
        out: dict = {}
        for x in forms:
            k = group_key(x)
            if k is None:
                continue
            row = out.setdefault(k, {"name": label(x), "open": 0, "done": 0})
            row["open" if x.status in OPEN_STATES else "done"] += 1
        return sorted(out.values(), key=lambda r: -(r["open"] + r["done"]))[:12]

    workload = {
        "project": load(lambda x: x.project_id, lambda x: x.project.name),
        "editor": load(lambda x: x.assigned_editor_id, lambda x: x.assigned_editor.name if x.assigned_editor else "Unassigned"),
        "reviewer": load(lambda x: x.assigned_reviewer_id, lambda x: x.assigned_reviewer.name if x.assigned_reviewer else "Unassigned"),
    }
    sample_forms = sum(1 for x in forms if x.is_sample)
    return {
        "filters": f.as_dict(), "kpis": kpis,
        "status_distribution": [{"status": k, "count": v} for k, v in st.items()],
        "weekly_decisions": weekly,
        "findings_by_category": [{"category": k, "count": v} for k, v in cat.most_common(10)],
        "findings_by_severity": [{"severity": s, "count": sev[s]} for s in ("critical", "major", "minor", "info")],
        "repeat_error_categories": [{"category": k, "forms": v} for k, v in repeat_cats.most_common(8)],
        "workload": workload,
        "sample_data": {"sample_forms": sample_forms, "real_forms": len(forms) - sample_forms,
                        "notice": "Includes clearly-labelled SAMPLE demo forms." if sample_forms else None},
        "definitions": {
            "unique_forms": "Counted once per Form ID. Re-edits and resubmissions are review attempts, not new forms.",
            "date_range": "Date range filters forms by intake date.",
            "first_pass_approval_rate": "Forms whose first review attempt was an approval ÷ forms reviewed at least once.",
            "re_edit_rate": "Forms sent back for re-edit at least once ÷ forms reviewed at least once.",
            "qc_coverage": "Forms with a human QC decision ÷ forms with an edited PDF submitted.",
            "avg_turnaround_h": "Hours from intake to approval for approved/closed forms.",
        },
    }


# ---------------------------------------------------------------------------------- reports
REPORTS = {
    "project_performance": "Project / client performance",
    "editor_quality": "Editor quality and re-edit rates",
    "reviewer_workload": "QC reviewer workload and outcomes",
    "backlog_turnaround": "Operations backlog and turnaround",
    "ai_performance": "AI finding precision and recall",
    "technical_failures": "Technical failures and manual overrides",
    "repeat_errors": "Repeat errors and training opportunities",
    "qc_scorecard": "QC scorecard by error (fixed error table)",
    "qc_form_scores": "QC score per form",
}


def build_report(db: Session, user: User, name: str, f: Filters) -> dict:
    ds = Dataset(db, user, f)
    fn = globals()[f"_r_{name}"]
    result = fn(ds)
    result.update(report=name, title=REPORTS[name], filters=f.as_dict(), generated_at=utcnow().isoformat() + "Z",
                  forms_in_scope=len(ds.forms))
    return result


def _r_project_performance(ds: Dataset) -> dict:
    groups: dict[int, list[Form]] = defaultdict(list)
    for x in ds.forms:
        groups[x.project_id].append(x)
    rows = []
    for pid, fl in groups.items():
        s = ds.group_stats(fl)
        p = fl[0].project
        rows.append({"client": p.client.name, "project": p.name, "unique_forms": s["forms"], "submitted": s["submitted"],
                     "review_attempts": s["attempts"], "approved": s["approved"], "backlog": s["open"],
                     "first_pass_approval_%": s["first_pass_rate"], "re_edit_rate_%": s["re_edit_rate"],
                     "avg_turnaround_h": s["avg_turnaround_h"], "confirmed_findings": s["confirmed_findings"]})
    rows.sort(key=lambda r: (r["client"], r["project"]))
    return {"columns": list(rows[0].keys()) if rows else [], "rows": rows,
            "notes": ["Unique forms and review attempts are counted separately."]}


def _r_editor_quality(ds: Dataset) -> dict:
    groups: dict[int, list[Form]] = defaultdict(list)
    for x in ds.forms:
        if x.assigned_editor_id:
            groups[x.assigned_editor_id].append(x)
    if ds.user.role == "editor":
        groups = {k: v for k, v in groups.items() if k == ds.user.id}
    rows = []
    for uid, fl in groups.items():
        s = ds.group_stats(fl)
        cats = Counter(category_label(c.category) for c in ds.confirmed if c.form_id in {x.id for x in fl})
        rows.append({"editor": ds.users[uid].name, "assigned_forms": s["forms"], "submitted": s["submitted"],
                     "review_attempts": s["attempts"], "approved": s["approved"],
                     "first_pass_approval_%": s["first_pass_rate"], "re_edit_rate_%": s["re_edit_rate"],
                     "confirmed_findings": s["confirmed_findings"], "findings_per_reviewed_form": s["findings_per_form"],
                     "top_error_category": cats.most_common(1)[0][0] if cats else None})
    rows.sort(key=lambda r: r["editor"])
    return {"columns": list(rows[0].keys()) if rows else [], "rows": rows,
            "notes": ["Editors see only their own row." if ds.user.role == "editor" else "Based on the editor currently assigned to each form."]}


def _r_reviewer_workload(ds: Dataset) -> dict:
    rows = []
    reviewers = {a.reviewer_id for a in ds.attempts if a.reviewer_id} | {x.assigned_reviewer_id for x in ds.forms if x.assigned_reviewer_id}
    if ds.user.role == "qc_reviewer":
        reviewers &= {ds.user.id}
    run_end = {r.id: r.finished_at for r in ds.runs}
    for uid in reviewers:
        at = [a for a in ds.attempts if a.reviewer_id == uid]
        fl = [c for c in ds.findings if c.reviewer_id == uid]
        speeds = [_hours(run_end.get(a.run_id), a.decided_at) for a in at]
        rows.append({"reviewer": ds.users[uid].name, "forms_in_queue": sum(1 for x in ds.forms if x.assigned_reviewer_id == uid and x.status == "qc_review"),
                     "decisions": len(at), "approved": sum(1 for a in at if a.decision == "approved"),
                     "sent_for_re_edit": sum(1 for a in at if a.decision == "re_edit_required"),
                     "manual_review_or_blocked": sum(1 for a in at if a.decision in ("manual_review_required", "blocked")),
                     "findings_accepted": sum(1 for c in fl if c.review_status in ("accepted", "edited")),
                     "findings_rejected": sum(1 for c in fl if c.review_status == "rejected"),
                     "findings_not_applicable": sum(1 for c in fl if c.review_status == "not_applicable"),
                     "avg_hours_to_decision": _avg(speeds)})
    rows.sort(key=lambda r: r["reviewer"])
    return {"columns": list(rows[0].keys()) if rows else [], "rows": rows, "notes": []}


def _r_backlog_turnaround(ds: Dataset) -> dict:
    now = utcnow()
    rows = []
    for x in sorted(ds.forms, key=lambda x: x.created_at):
        if x.status not in OPEN_STATES:
            continue
        age = _hours(x.created_at, now)
        rows.append({"form_id": x.form_code, "client": x.project.client.name, "project": x.project.name, "status": x.status,
                     "editor": x.assigned_editor.name if x.assigned_editor else None,
                     "reviewer": x.assigned_reviewer.name if x.assigned_reviewer else None,
                     "age_hours": age, "due_at": x.due_at.isoformat() + "Z" if x.due_at else None,
                     "overdue": bool(x.due_at and x.due_at < now), "pair": "complete" if ds.has_edited(x) and any(d.kind == "raw" for d in x.documents) else "incomplete"})
    approved = [x for x in ds.forms if x.approved_at]
    buckets = Counter()
    for r in rows:
        a = r["age_hours"] or 0
        buckets["<24h" if a < 24 else "1-3d" if a < 72 else "3-7d" if a < 168 else ">7d"] += 1
    summary = {"open_forms": len(rows), "overdue": sum(1 for r in rows if r["overdue"]),
               "avg_turnaround_h_approved": _avg([ds.turnaround(x) for x in approved]), "aging": dict(buckets)}
    return {"columns": list(rows[0].keys()) if rows else [], "rows": rows, "summary": summary, "notes": ["Lists open (non-approved) forms."]}


def _r_ai_performance(ds: Dataset) -> dict:
    notes = ["Precision is measured against REVIEWER decisions only; it is not an independent accuracy benchmark.",
             "Recall is only indicated where reviewers manually added findings the system missed."]
    rows = []
    for source in ("ai", "deterministic"):
        fs = [c for c in ds.findings if c.source == source and c.review_status not in ("pending", "escalated")]
        useful = sum(1 for c in fs if c.review_status in ("accepted", "edited"))
        rejected = sum(1 for c in fs if c.review_status == "rejected")
        n = useful + rejected
        manual = sum(1 for c in ds.findings if c.source == "manual")
        enough = n >= MIN_AI_SAMPLE
        rows.append({"source": source, "validated_findings": n, "confirmed": useful, "rejected": rejected,
                     "not_applicable": sum(1 for c in fs if c.review_status == "not_applicable"),
                     "reviewer_confirmed_precision_%": _pct(useful, n) if enough else None,
                     "indicative_recall_%": _pct(useful, useful + manual) if enough and manual else None,
                     "status": "reported" if enough else f"Insufficient validated data (need ≥{MIN_AI_SAMPLE}, have {n})"})
    by_rule = defaultdict(lambda: [0, 0])
    for c in ds.findings:
        if c.review_status in ("accepted", "edited"):
            by_rule[c.rule_label or "No rule cited"][0] += 1
        elif c.review_status == "rejected":
            by_rule[c.rule_label or "No rule cited"][1] += 1
    breakdown = [{"rule": k, "confirmed": v[0], "rejected": v[1]} for k, v in sorted(by_rule.items(), key=lambda kv: -sum(kv[1]))][:15]
    run_status = Counter(r.ai_status for r in ds.runs)
    return {"columns": list(rows[0].keys()), "rows": rows,
            "summary": {"runs_by_ai_status": dict(run_status), "manual_findings_added": sum(1 for c in ds.findings if c.source == "manual"),
                        "by_rule": breakdown}, "notes": notes}


def _r_technical_failures(ds: Dataset) -> dict:
    rows = []
    for r in ds.runs:
        fm = ds.by_id[r.form_id]
        issues = []
        if r.status == "failed":
            issues.append(("run_failed", r.error))
        if r.ai_status in ("failed", "not_configured", "blocked_by_policy") and r.status != "failed":
            issues.append((f"ai_{r.ai_status}", r.ai_message))
        for w in (r.stats or {}).get("warnings", []):
            if not str(w).startswith("AI analysis"):
                issues.append(("warning", w))
        for kind, msg in issues:
            rows.append({"when": (r.finished_at or r.created_at).isoformat() + "Z", "form_id": fm.form_code, "project": fm.project.name,
                         "run_id": r.id, "type": kind, "detail": msg})
    for x in ds.forms:
        for d in x.documents:
            if d.extraction_status in ("failed", "ocr_unavailable", "partial"):
                rows.append({"when": d.uploaded_at.isoformat() + "Z", "form_id": x.form_code, "project": x.project.name, "run_id": None,
                             "type": f"extraction_{d.extraction_status}", "detail": f"{d.logical_name} v{d.version_no}"})
    overrides = [c for c in ds.findings if c.review_status in ("rejected", "edited")]
    manual = [c for c in ds.findings if c.source == "manual"]
    rows.sort(key=lambda r: r["when"], reverse=True)
    return {"columns": ["when", "form_id", "project", "run_id", "type", "detail"], "rows": rows,
            "summary": {"failed_runs": sum(1 for r in ds.runs if r.status == "failed"),
                        "reviewer_rejected_findings": sum(1 for c in overrides if c.review_status == "rejected"),
                        "reviewer_edited_findings": sum(1 for c in overrides if c.review_status == "edited"),
                        "reviewer_added_findings": len(manual)}, "notes": []}


def _r_repeat_errors(ds: Dataset) -> dict:
    agg: dict[tuple[str, str], dict] = {}
    own = ds.user.role == "editor"
    for c in ds.confirmed:
        fm = ds.by_id.get(c.form_id)
        if not fm or not fm.assigned_editor_id or (own and fm.assigned_editor_id != ds.user.id):
            continue
        key = (ds.users[fm.assigned_editor_id].name, category_label(c.category))
        row = agg.setdefault(key, {"editor": key[0], "category": key[1], "confirmed": 0, "forms": set(), "attempts": set()})
        row["confirmed"] += 1
        row["forms"].add(c.form_id)
        row["attempts"].add(c.run_id)
    rows = []
    for row in agg.values():
        repeated = len(row["forms"]) >= 2 or len(row["attempts"]) >= 2
        rows.append({"editor": row["editor"], "category": row["category"], "confirmed_findings": row["confirmed"],
                     "forms_affected": len(row["forms"]), "review_attempts_affected": len(row["attempts"]),
                     "training_opportunity": "Yes" if repeated and row["confirmed"] >= 3 else ("Watch" if repeated else "No")})
    rows.sort(key=lambda r: (-r["confirmed_findings"], r["editor"]))
    return {"columns": list(rows[0].keys()) if rows else [], "rows": rows,
            "notes": ["Training opportunity = same category confirmed ≥3 times across ≥2 forms or review attempts."]}


def _scoped_findings(ds: Dataset):
    """Findings in scope; an editor only sees the forms assigned to them."""
    own = ds.user.role == "editor"
    return [c for c in ds.findings if c.form_id in ds.by_id and not (own and ds.by_id[c.form_id].assigned_editor_id != ds.user.id)]


def _r_qc_scorecard(ds: Dataset) -> dict:
    """Every error of the fixed QC error table with how often reviewers confirmed it."""
    fs = _scoped_findings(ds)
    rows = []
    for cat in tx.CATEGORIES:
        for sub in (x for x in tx.SUBTYPES if x.category == cat.key):
            mine = [c for c in fs if c.error_subtype == sub.key]
            conf = [c for c in mine if c.review_status in ("accepted", "edited")]
            rows.append({"category": cat.label, "error": sub.label, "marks_per_category": cat.points,
                         "confirmed": len(conf), "forms_with_confirmed_error": len({c.form_id for c in conf}),
                         "waiting_for_review": sum(1 for c in mine if c.review_status == "pending"),
                         "rejected_by_reviewer": sum(1 for c in mine if c.review_status == "rejected"),
                         "checked_by": "Reviewer (video / audio / picture)" if sub.manual_only else "System + reviewer"})
    return {"columns": list(rows[0].keys()), "rows": rows,
            "notes": [f"Only the {len(tx.SUBTYPES)} errors of the QC error table are checked. Counts of confirmed errors are what reviewers accepted; "
                      "pending findings are not scored yet."]}


def _r_qc_form_scores(ds: Dataset) -> dict:
    """Score per form: 100 minus the marks of every category that has a confirmed error."""
    by_form: dict[int, list] = defaultdict(list)
    for c in _scoped_findings(ds):
        by_form[c.form_id].append(c)
    rows = []
    for fm in ds.forms:
        fl = by_form.get(fm.id)
        if fl is None and not ds.attempts_by_form.get(fm.id):
            continue
        fl = fl or []
        conf = [c.error_subtype for c in fl if c.review_status in ("accepted", "edited")]
        sc = tx.score(conf)
        last = (ds.attempts_by_form.get(fm.id) or [None])[-1]
        row = {"form": fm.form_code, "client": fm.project.client.name, "project": fm.project.name,
               "editor": fm.assigned_editor.name if fm.assigned_editor else "", "qc_reviewer": fm.assigned_reviewer.name if fm.assigned_reviewer else "",
               "decision": last.decision.replace("_", " ") if last else "not decided yet", "score": sc["score"]}
        for cat in tx.CATEGORIES:
            row[cat.label] = sum(1 for k in conf if tx.category_of(k) and tx.category_of(k).key == cat.key)
        row["not_reviewed_yet"] = sum(1 for c in fl if c.review_status == "pending")
        rows.append(row)
    rows.sort(key=lambda r: (r["score"], r["form"]))
    scored = [r["score"] for r in rows if r["not_reviewed_yet"] == 0]
    return {"columns": list(rows[0].keys()) if rows else [], "rows": rows,
            "summary": {"forms": len(rows), "fully_reviewed": len(scored), "average_score_reviewed": round(sum(scored) / len(scored), 1) if scored else None},
            "notes": ["Score = 100 minus the marks of each category with at least one confirmed error (Grammar 5, Missed Instruction 15, Contradiction 20, Wrong Location 25). "
                      "Forms with findings still waiting for review show a provisional score."]}
