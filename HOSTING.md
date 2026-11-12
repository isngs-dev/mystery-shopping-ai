# Hosting the backend (step by step)

Goal: one public HTTPS address that serves the website **and** the API, with a database and a disk that survive restarts and a Drive
auto-sync that keeps running. The repository is ready for this with **Render** (`render.yaml`); the same `Dockerfile` also works on
Railway, Fly.io or any VPS with Docker.

> Status: written and checked by reading the code. It has **not** been run on a real host yet (Docker was not running on the
> machine where this was prepared), so expect to read the first deploy log once.

## Before you start (10 minutes)

1. **Delete the accidental Netlify site** and make a **new Google Drive service-account key** (Google Cloud → IAM → Service accounts →
   Keys: delete the old key, add a new JSON key). Share the Drive folder with the service account's e-mail if it is a new account.
2. Decide the **first Super Admin** e-mail and a strong password (the app creates it on first start only).
3. Keep the new JSON key file closed and private; you will paste its text into Render once.

## Deploy on Render

1. https://dashboard.render.com → **New → Blueprint** → connect GitHub → choose `isngs-dev/mystery-shopping-ai` (branch `main`).
   Render reads `render.yaml` and proposes: web service `mystery-shopping-ai` (Docker, 10 GB disk at `/data`) and database `msai-db`.
2. It asks for the values marked secret:
   | Name | What to enter |
   |---|---|
   | `GOOGLE_SERVICE_ACCOUNT_JSON` | the **whole text** of the new key file |
   | `GOOGLE_DRIVE_FOLDER_ID` | the ID of the shared Drive folder (the last part of its URL) |
   | `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` | the first Super Admin (ISN) |
3. **Apply.** The first build takes several minutes (it builds the website and installs Tesseract OCR). The service is ready when
   `https://mystery-shopping-ai.onrender.com/api/health` answers `ok`.
4. Open the site, sign in as the Super Admin, then **Users & Roles → New user** for the team (Shreyas = Admin, Geeta and Rahat = Manager,
   Renuka, Swapnali, Saradhya, Devansh = QC reviewer). Give each a **new** password.
5. Sign in as Geeta → **Google Drive Import** → enable auto-sync (2 minutes). Projects appear under **Repository › QC projects →
   Awaiting assignment**; Geeta or Rahat assign them to the QC leads and QC starts by itself.
6. Check privacy: `https://YOUR-HOST/backend/data/app.db` and `…/backend/secrets/drive-key.json` must not download anything (the website's own page or a 404 is fine).

Costs: the web service needs a paid plan (always-on + persistent disk) and the database a paid plan too; see Render's current prices.
A free web service sleeps, which would stop the Drive sync and the QC.

## The persistent disk is not optional

The PDFs live on the disk mounted at `/data`; the forms' records live in PostgreSQL. A service **without a disk** loses every PDF each time
it restarts or redeploys, while the database keeps the records — the website then shows the forms but "The page could not be rendered"
and QC fails with `[Errno 2] No such file or directory: '/data/storage/…'`. Add the disk (Render → service → **Disks** → mount path
`/data`) before anything else. If PDFs were already lost, the Drive auto-sync fetches them again by itself (byte-identical files only) and
runs QC on those forms again; "Sync now" does it immediately.

## What is different from running on your PC

* The database is PostgreSQL, not the local SQLite file. **Nothing from the local database is copied**: forms are imported again from
  Drive, and local reviewer decisions, assignments and rule approvals start from zero. (A one-off data migration can be written if
  needed.)
* Excel QC reports are written to `/data/qc-reports` on the disk; download them from the website buttons.
* `ENV=production` turns the one-click demo login off; everyone signs in with e-mail and password.
* Updating: push to `main`; Render rebuilds and runs `alembic upgrade head` before starting.

## Optional: Vercel / Netlify for the website

Not needed when the container serves the website itself. If you still want the website on Vercel/Netlify, add the API rule pointing to
the Render address as described in `DEPLOY.md` (sections 2 and 2b).
