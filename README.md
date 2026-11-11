# Mystery Shopping AI — Intelligent Quality Operations

> *Every report checked. Every detail accounted for.*

An enterprise platform for Mystery Shopping operations: upload RAW / edited PDF pairs, run a real
extraction → comparison → rules → (optional) AI pipeline, review **evidence-linked** findings in a
three-panel workspace, record human decisions, manage re-edit cycles, and see the results in the
dashboard and reports.

**Nothing is auto-approved.** Every finding needs a human decision, and approval is blocked while any
finding is unreviewed or confirmed as an error.

## Contents
- [Quick start](#quick-start) · [Demo logins](#demo-logins-sample-data) · [Architecture](#architecture)
- [Folder structure & versioning](#document-repository) · [Roles & access](#roles--access) · [Workflow](#workflow)
- [Pipeline & AI](#processing-pipeline) · [Security](#security--responsible-ai) · [Configuration](#configuration)
- [Tests](#tests) · [Deployment](#deployment) · [Verified features](#verified-features) · [Not done / needs credentials](#integrations-that-still-need-credentials-or-approval)

## Quick start

Requirements: Python 3.12+, Node 20+. (Optional: Tesseract for OCR.)

```bash
# 1. Backend
cd backend
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/python
cp .env.example .env            # edit SECRET_KEY etc.
.venv/Scripts/python -m app.seed --samples     # optional: clearly-labelled SAMPLE data + sample PDFs in ../samples
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# 2. Frontend (new terminal)
cd frontend && npm install && npm run dev      # http://localhost:5173 (proxies /api to :8000)
```

Without the demo seed, set `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` in `backend/.env` and a
Super Admin is created on first start; then create everyone else under **Users & Roles**.

### Demo logins (SAMPLE data)
`python -m app.seed` creates 16 forms that are pushed through the *real* ingestion/pipeline/review code.
Everything is flagged `SAMPLE` in the UI; use **Data → Real only** to hide it. Password for all demo
users: `Demo-Passw0rd!2026` (override with `DEMO_PASSWORD`; the seed refuses to run when `ENV=production`).

**One-click access:** on a local development server the login page shows a *Quick access — demo accounts* panel;
click any SAMPLE account to sign in without a password. It is active only when `ENV=development`, only for requests
from loopback addresses, only for `SAMPLE` users, and can be turned off with `DEMO_LOGIN_ENABLED=false`. It is
unavailable in test/production, and each use is written to the audit trail (`auth.demo_login`).

| Role | Login |
|---|---|
| Super Admin | `superadmin@demo.mysteryshop-ai.com` |
| Admin | `admin@demo.mysteryshop-ai.com` |
| Editor & Manager | `manager@demo.mysteryshop-ai.com` |
| Editors | `editor1@…`, `editor2@…`, `editor3@…` |
| QC Lead / Reviewers | `qc1@…`, `qc2@…` |
| AI Lead · Process Owner · Ops Sponsor · Client POC · Reporting | `ailead@…`, `owner@…`, `sponsor@…`, `client@…`, `reporting@…` |

## Architecture

```
frontend/  React 18 + TypeScript + Tailwind + Framer Motion + Recharts + TanStack Query (Vite)
backend/
  app/main.py            FastAPI app, security headers, SPA hosting
  app/models.py          SQLAlchemy schema (users, clients, projects, forms, documents, pages, rules/versions,
                         qc_runs, findings, review_attempts, assignments, comments, audit, exports, notifications)
  app/permissions.py     Role→permission matrix + data-scope (project access) helpers — enforced server-side
  app/routers/           auth · admin(users/access/clients/projects/audit/system) · forms(repository) · qc · rules · reports
  app/services/
    extraction.py        PDF validation, PyMuPDF text+word boxes, Tesseract OCR for scanned pages
    compare.py           word-level diff with page numbers + bounding boxes
    rules_engine.py      deterministic checks bound to versioned rules
    ai/ + ai_analysis.py configurable provider adapter (Anthropic SDK, OpenAI-compatible) + evidence verification
    pipeline.py          validate → extract → compare → rules → AI → persist (stage status stored on the run)
    workflow.py          state machine, assignment, decision guards (independent review, no auto-approval)
    repository.py        immutable versioned storage, duplicate/naming/pair detection, exception queue
    metrics.py           dashboard + 7 report datasets
  app/storage/           StorageAdapter interface; local adapter implemented; shared-drive/S3 stubs fail loudly
  migrations/            Alembic (0001 initial schema)
  tests/                 pytest (workflow, permissions, AI, rules, reports)
```

### Document repository
`MysteryShop/<Client>/<Project or Editor>/FORM001_1.pdf` (RAW) and `FORM001_2.pdf` (edited).
- RAW is written once (`open(..., 'xb')`, read-only) and can never be replaced; SHA-256 recorded.
- Resubmissions are new files `FORM001_2_r<N>_<UTC timestamp>.pdf` linked to the same Form ID; every version and
  review attempt is preserved.
- Missing pairs, duplicate Form IDs, bad file names, unreadable/encrypted/oversized PDFs go to the **exception queue**
  (rejected files are quarantined, not lost). “Scan storage” registers files placed in the folder tree by other means.
- The same Client → Project → Form hierarchy is kept in the database, so it displays identically even when a
  storage provider cannot represent folders.

### Roles & access
Super Admin (L1) creates accounts and assigns roles/project access · Admin (L2) manages operations within granted
projects · Editor & Manager (L3) assigns work and can edit/upload · Editor (L4) sees only assigned forms.
Also: QC Lead/Reviewer, AI Lead, Process Owner, Ops Sponsor, Client POC, Reporting.
**No role receives all clients automatically** — only the Super Admin is unrestricted; everyone else needs explicit
project grants (Admins can only grant lower-level roles, only within their own projects). QC reviewers cannot upload
or modify PDFs; editors cannot make QC decisions; editors only see *confirmed* findings and feedback.

### AI editor (RAW → AI edit → QC)
Upload `FORMID_1.pdf` (RAW) and, by default, the **AI editor** produces `FORMID_2.pdf` and sends it straight to QC, where the
reviewer compares RAW vs edited. (Untick *“Let the AI editor correct this report”* in the upload dialog, or set
`AI_EDIT_ON_UPLOAD=false`, to have a human editor do it; **Generate AI edit** on a form page runs it on demand, and on a
re-edit it builds on the previous edit.)
- **Scope is spelling, grammar, punctuation, capitalisation and a/an only.** Numbers, times, dates, names, negations and
  facts are never changed; nothing is added or removed.
- **Two engines:** a built-in dictionary/grammar corrector (always on, no external service) and, only if an approved AI
  provider is configured, language-model edits. Each model edit must quote text that exists verbatim in the report and passes
  a safety policy (no changed figures, no added/removed negations, no new names or non-dictionary words, no large rewrites) or it
  is discarded and listed under *“needs a human”*.
- The PDF is re-typeset in place (same font family, size and colour; only the tail of an affected line is rewritten) and then
  **verified** by re-reading it: if the text does not match the plan or any figure differs, nothing is submitted.
- The RAW is never modified. Every correction is logged with page, reason, source and confidence and can be highlighted on both
  PDFs in the QC workspace. The AI editor never approves anything: a human QC reviewer still validates and decides.
- Limits (reported, not hidden): scanned PDFs without a text layer cannot be edited (a human is needed); words the corrector cannot
  fix with confidence (ambiguous typos, multi-line phrases, column gaps) are left unchanged and listed; the *text-layer reading
  order* of a rewritten line may differ in other PDF tools although the page looks identical (this platform reads geometrically).

### QC projects: a whole project for one QC lead
`QC Projects` (Manager, Admin, Super Admin, QC leads) lists every project with its QC progress. The Manager / Admin / Super Admin presses **Assign** and picks a QC lead:
that person becomes reviewer of every form in the project (forms that arrive later follow automatically), the QC analysis is queued on every form that has both PDFs,
and the lead finds them under *QC Projects → Only my projects* and *Review Queue → Assigned to me*. The project page shows each form's stage (to review / ready to decide / decided /
waiting for the edited PDF / QC failed), a **Project report** (PDF summary) and **All reports (ZIP)** (summary + one QC report per analysed form). The lead still accepts or rejects every
finding and takes the decision herself; a lead is never given a form she edited (independent-review rule). API: `/api/qc/projects/*` (see `routers/project_qc.py`).

**Whose project is it?** The Drive folder name says it: `Valley Bank_Geeta` belongs to Geeta, `Orlando_Devansh` to Devansh (a small spelling slip such as *Saradiya* → Saradhya is tolerated,
an unclear match is never guessed). On import the owner becomes the project's QC lead and sees it; other QC leads do not, while Admin / Manager / Super Admin see everything, so every
dashboard, queue and report shows exactly the person's own Drive projects. `python scripts/arrange_by_drive.py [--apply]` applies the same rule to projects that already exist.

**Drive file names understood:** `ID_1.pdf`, `ID-1.pdf`, `ID - 1.pdf`, `ID (1).pdf`, `ID - Admin Shop Review-1.pdf`, and `ID - <text>.pdf` without a number (= the RAW; its edit is `ID - 2.pdf`).
A sync that was cut off by a stopped server is closed at the next start and syncing continues at once; Drive is checked every 2 minutes.

**Removing demo data:** `python scripts/remove_sample_data.py [--apply]` deletes everything flagged SAMPLE (demo clients, projects, forms, files and the demo accounts) after backing the database up.

**Project hand-over flow:** Drive sync creates every project and makes it visible to the Super Admin, Admin, the manager (Geeta) and every *project coordinator* (Rahat: a QC lead account with `is_coordinator`, set by the Super Admin under Users & Roles; coordinators see all projects and may assign them, nothing more). Under **Repository › QC projects** a new project waits in *Awaiting assignment*; a coordinator/manager/admin assigns it to a QC lead (the Drive folder name, e.g. `Orlando_Devansh`, is only suggested — set `AUTO_ASSIGN_BY_FOLDER_NAME=true` to assign automatically), after which it moves to *Assigned QC projects*, QC runs on all its forms and only that QC lead (besides the coordinators/admins) sees it. Reassigning takes access away from the previous lead. QC leads see just their own projects and open one to work through its forms.

**Automatic QC and reports:** the Drive auto-sync (every 2 minutes) imports new/changed PDFs; for every form of a project that has been handed to a QC lead the QC analysis runs by itself (new forms, new edited versions, and all existing forms the moment the project is assigned). Forms of a project that is still *Awaiting assignment* are synced but not analysed (`qc_waiting_assignment` in the sync result). The QC report PDF is built on demand from the latest data, so it is available at once and always current — per form (QC report button) and per project (Project report / All reports ZIP). There is no “Save QC decision” step in the UI: reports are marked *AUTOMATED* and show two scores — the reviewer-confirmed score and the automated check (every finding not rejected counts).

**Project QC report in Excel:** `GET /api/qc/projects/{id}/report.xlsx` (buttons: QC projects → card “Excel” / project page “QC report (Excel)” / Repository → inside a project folder). One row per form of the project in the layout of the team's tracker (`HSB_Multi-Projects`, sheet “October”: Input date … Error Category, Platform, Incomplete Status, Duplicates). QC Member, QC Date, QC Status (the tracker's own words: Accepted / Request Revision / In Process / Query), Feedback (score + errors by category), Error Category (the tracker's list), Editing Type, Platform and Client Name are filled by the platform; Editor, OPS Status, Comments to shopper and dates come from an imported tracker row with the same Shop ID and stay empty otherwise. Extra sheets: *Findings* (every finding with RAW/edited evidence) and *Summary* (the 17-error table counts, average score). The sheet looks like the tracker (blue header, centred cells, dropdowns on Editor / OPS Status / Editing Type / QC Status / Error Category). Editor defaults to “iSN Team” and Editor Name to the project's person unless an imported tracker row says otherwise.

**The Excel files fill themselves:** a background worker (`QC_REPORTS_AUTO`, on by default) keeps `backend/data/qc-reports/<Client>/<Project>.xlsx` current — it rewrites a project's workbook a few seconds after a form is synced from Drive or uploaded, a QC run finishes, a reviewer accepts/rejects a finding or a project is assigned (a burst of changes means one rewrite), and rebuilds all of them at start-up. If a file is open in Excel the write is retried. The download buttons always build a fresh copy.

### The fixed QC error table

QC checks **only** the team's error sheet — nothing else is run, reported or scored (`backend/app/services/error_taxonomy.py`, shown on the Rules page and at `GET /api/qc/error-table`):

| Category | Marks | Errors |
|---|---|---|
| Grammar Mistake | 5 (→ 95) | Punctuation · Extra Space/No space · Spelling · Past tense · Incorrect Terminology · Profanity |
| Missed Instruction | 15 (→ 85) | Negative not answered · Video not checked thoroughly and BOTD or N/A marked · Audio not evaluated correctly · Call Attempt not checked · Incorrect Date and Time |
| Contradiction | 20 | Comment and Marking do not match · Original sentences changed which changed the meaning · Incorrect option selected as per picture |
| Wrong Location / Put on Different category | 25 | Shop was performed incorrectly · Closed location treated as Open · Status Incorrect selected |

* Every rule belongs to one of these errors (`error_subtype`); a rule that does not is never run (existing ones are retired at start-up, new ones are rejected with 422). Checklist items from the Drive documents that are not on the table (contractions, amounts, product question, …) are no longer imported.
* Every finding — rule check, AI or reviewer-added — carries a category and error from the table. The AI is given the table as its only checklist and its answers outside it are discarded.
* Video, audio, call-attempt, picture and “performed incorrectly” checks need media the system cannot see: the reviewer adds them under **Add finding**, where they can be described instead of quoted.
* The QC score of a form is 100 minus the marks of every category with at least one *confirmed* finding (a category counts once). It is shown in the QC workspace and in the QC report PDF.
* **Every QC report is built on this table:** the form QC report (PDF) opens with a *QC scorecard* — all 17 errors with confirmed / not-reviewed / rejected counts, the score and what was deducted — and lists findings grouped by category › error; the project QC report (PDF) and the QC Projects page show the average score, the errors summed over the project and a score per form; the Reports page has *QC scorecard by error* and *QC score per form* (CSV/XLSX export like the others). Scores are provisional while findings are still unreviewed.
* Not errors, so no findings: differing page counts, unreliable OCR and instruction-like text in a document are shown as run warnings.
* Existing database: `python scripts/apply_error_table.py [--apply]` (after `alembic upgrade head`) maps the rules and re-checks the forms still in QC Review.

### Google Drive import, QC rules and QC report
> Drive import and automatic sync can be run by the **Manager** role only (set in `permissions.py`, `integrations.manage`). Super Admin and Admin see the imported data but cannot start imports.

**Drive import** (`Administration → Google Drive Import`) brings a Drive folder laid out as `Client / Project-or-Editor / ID_1.pdf (RAW) · ID_2.pdf (edited)`
into the platform through the normal ingestion path (validation, immutable storage, duplicate/naming checks, exception queue, audit) and then the QC pipeline.
Three sources, one importer:
1. **ZIP** (no credentials): in Drive right-click the folder → *Download*, drop the ZIP in the page. Zip-slip entries are ignored; the ZIP is deleted afterwards.
2. **Google Drive API** (live sync): set `GOOGLE_SERVICE_ACCOUNT_FILE` (or `_JSON`), enable the Drive API, and share the folder with the service account's
   e-mail (Viewer). Read-only scope; nothing is ever written to Drive. *Implemented and tested against a mocked API; not run against live Google here.*
3. **Local folder** inside `IMPORT_LOCAL_ROOT` (e.g. a Drive-for-desktop copy).
Imports are idempotent. `_1` = RAW and `_2` = edited are defined; other suffixes (`_3`, `_4`) are **not guessed**: they are listed in the exception queue until the
meaning is defined. RAWs with no `_2` can be edited by the AI editor. Root-level `.xlsx` trackers are imported read-only (*Production Tracker*); rows that look like
credentials are skipped. Checklists/guidelines are recognised and turned into QC rules (below); **credentials found inside those documents are never copied**.

**Automatic sync (automation)** - `Administration → Google Drive Import → Automatic sync`. Switch it ON and a background scheduler checks the Drive folder
every N minutes (1-60) and imports **only what is new or changed**, with no clicks:
- new RAW → new form (Pending) · new edited file → QC runs automatically · a *revised* edited file → a new, timestamped version (history kept) + a fresh QC run;
- a RAW that is changed in Drive after import is **never** overwritten (immutable): the original is kept and an exception is raised once;
- a RAW that still has no edited file after *N hours* can be AI-edited (optional); if the human editor's file arrives later it becomes the newest version;
- admins get a notification (*"Drive sync: 2 new RAW, 1 edited …"*), problems are shown on the card, Dashboard/Repository refresh themselves.
It is incremental through a sync ledger (`drive_files`: file id + md5/size-mtime fingerprint), so an idle cycle downloads nothing; settings are stored in the
database, so they survive restarts. **Two ways to connect it** - (A) *Google Drive for desktop* (no Google Cloud needed): install it, make the folder available
offline, set `IMPORT_LOCAL_ROOT` to its parent (e.g. `G:\My Drive`) and choose *Synced folder on this server*; (B) a Google *service account* (see above) and choose
*Google Drive API*. Drive is **polled** (a push webhook would need a public HTTPS endpoint). Run one scheduler per deployment (`DRIVE_SCHEDULER_ENABLED=false` on other workers).

**QC rules from the client documents** (`services/rules_pack.py`): APC checklist + Valley Drive-Up instructions (client *APC*) and the CXE checklist + HOU guidelines (client
*CXE*) are distilled into 23 versioned rules - deterministic ones (contractions, `$` amounts, forbidden phrases, "associate", ages, AM/PM, quotation style, …) and
`ai_guideline` ones (need an approved AI provider; otherwise reported as *not evaluated*). They are created as drafts and submitted for approval; **they affect QC only after
an approver (not the importer) publishes them** (*QC Rules → Review & approve*).

**Template awareness** (`services/template_guard.py`): printed survey-platform pages contain headers, shopper contact details, reviewer panels and boilerplate. Lines that
repeat across ≥3 RAWs of a project, plus lines matched by a `template_profile` rule, are *protected*: never flagged, never edited. Comparison still sees them.

**QC report** (`services/qc_report.py`): one PDF per form with pair details, AI-edit log, every finding with quoted evidence and highlighted RAW/edited crops, reviewer
decisions, history and exact rule versions. **FINAL** only when a human decision exists for the analysed submission, otherwise **DRAFT**. It downloads automatically when a
reviewer saves the QC decision (also *QC report* buttons, and a ZIP of reports after an import).

### Workflow
`Pending → In Progress → QC Review → Re-edit Required | Approved → Closed`, plus `Manual Review Required`
and `Blocked`. Re-edit → editor resubmits the same Form ID → new analysis → new review attempt.
Reviewers can accept / reject / edit / mark N/A / escalate each finding, add findings the system missed
(evidence must exist verbatim in the text), and must resolve every finding before approving.

### Processing pipeline
1. Validate PDF pair  2. Extract text (+OCR for scans)  3. Word-level comparison
4. Apply versioned rules: spelling, grammar, PII, changed figures, negation, omissions, unsupported additions,
   required fields, prohibited terms, injection-looking text  5. AI contextual analysis (if configured and approved)
6. Persist findings + exact rule versions used.

Findings carry quoted evidence, real page numbers and highlight boxes, the rule + version, confidence and a
suggested correction. AI findings are **verified**: quotes must exist in the extracted text, rule ids must be ones we
supplied, pages are computed by the server — anything else is discarded and counted.
If AI is not configured, blocked by policy, or fails, the run says so (`completed with warnings`) and approving
requires an explicit acknowledgement.

## Security & responsible AI
- bcrypt (SHA-256 prehash) passwords, 10+ char policy, login lockout, httpOnly SameSite cookies + CSRF header,
  immediate session revocation on deactivation/role change, per-route permission checks and project scoping.
- Upload validation (magic bytes, size, page limits, encrypted PDFs rejected); PDFs are parsed, never executed.
- Confidential PDFs are **never** sent to an external AI provider unless `AI_ALLOW_EXTERNAL=true`.
- Document text is delimited as untrusted data; prompt-injection text is flagged as a security finding.
- Append-only audit trail (accounts, access, assignments, uploads, decisions, rule versions, exports).
- CSV/Excel exports neutralise formula injection. Secrets only via environment.

## Configuration
See `backend/.env.example`. Key settings: `DATABASE_URL`, `SECRET_KEY`, `STORAGE_BACKEND`, `OCR_ENABLED`,
`TESSERACT_CMD`, `AI_PROVIDER` (`none|anthropic|openai_compatible`), `AI_ALLOW_EXTERNAL`, `ANTHROPIC_API_KEY`,
`ANTHROPIC_MODEL` (default `claude-opus-5-5`), `ALLOW_SELF_REVIEW`, `AI_EDIT_ON_UPLOAD`, `DEMO_LOGIN_ENABLED`, `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_DRIVE_FOLDER_ID`, `IMPORT_LOCAL_ROOT`, `DRIVE_AUTO_SYNC`, `DRIVE_SYNC_INTERVAL_MINUTES`, `DRIVE_SCHEDULER_ENABLED`.

## Tests
```bash
cd backend && .venv/Scripts/python -m pytest -q       # 40 tests
cd frontend && npm run typecheck && npm run build
```

## Deployment
```bash
cp .env.example .env     # set SECRET_KEY, POSTGRES_PASSWORD, BOOTSTRAP_ADMIN_*, AI settings
docker compose up --build   # Postgres + app on :8000 (runs `alembic upgrade head` on start)
```
Put it behind HTTPS (`COOKIE_SECURE=true` is set by the compose file). Back up the `pgdata` and `docs` volumes.
Jobs run in-process (thread pool; interrupted runs are marked failed on restart) — swap `services/jobs.py` for
Celery/RQ for multi-node deployments.

## Verified features
Automated (pytest): RAW/edited upload → real pipeline → missed vs new error classification with page evidence;
rule-version recording; no approval with unreviewed/confirmed findings; re-edit → resubmission → approval cycle;
immutability and duplicates; naming/invalid-PDF exception queue; missing-pair detection; scanned PDF without OCR
reported honestly; fabricated AI evidence discarded; AI failure and policy-blocked states; prompt-injection handling;
role gates, project isolation, account hierarchy, lockout, session revocation, independent-review policy;
rule maker-checker approval; reports scoped by role; CSV/XLSX export filters and formula-injection guard; audit events.
Manual (browser): landing, login, dashboard (real data + drill-down), queue, repository, form page, QC workspace with
highlights verified on the page image, persistence after reload, admin pages, mobile layout.

## Integrations that still need credentials or approval
- **AI provider** — set `AI_PROVIDER` + key and (after organisational approval) `AI_ALLOW_EXTERNAL=true`, or point
  `OPENAI_BASE_URL` at an approved in-house model. The Anthropic adapter is implemented but was **not exercised against the
  live API** here (no key); it is covered by a fake-provider test only.
- **OCR** — install Tesseract and set `TESSERACT_CMD`. The OCR code path is implemented but was not executed here
  (Tesseract is not installed on the dev machine); without it scanned pages are reported and routed to manual review.
- **Google Drive live sync** — needs a Google Cloud service account (see above). Use the ZIP import until then.
- **Shared drive / cloud storage** — `shared_drive` and `s3` are explicit not-implemented stubs; implement
  `StorageAdapter` once the provider and credentials are approved.
- **Email/Slack notifications, SSO, survey platform, EP inventory** — not built (see *Automation modules* page,
  which labels every module Live / Foundation / Planned).
- Planned modules from the brief (form validation, follow-up drafting, capacity planning, SOP
  assistant, …) are **not implemented**.
