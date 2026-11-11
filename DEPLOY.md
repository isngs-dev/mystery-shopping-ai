# Deploying Mystery Shopping AI

The app has two parts: a **React website** (`frontend`) and a **FastAPI backend** with a database (`backend`). Netlify can host only the
website. The backend must run somewhere else, and the website talks to it through `/api`.

## 0. Rules that protect your data

* **Never drop or upload the project folder.** `backend/data` (database, PDFs), `backend/secrets` (Drive key, logins) and `backend/.env`
  are private. A static host publishes every file you give it.
* Upload **only** `frontend/dist` (5 files: `index.html`, `_redirects`, `favicon.svg`, `assets/…`). Run `npm run build` in `frontend` first.
* Secrets go into the host's *environment variables / secret store*, never into files in the upload.
* After every deploy, open `https://YOUR-SITE/backend/data/app.db` and `https://YOUR-SITE/backend/secrets/drive-key.json`: neither may download a file. A 404, or the website's own page (single-page apps answer unknown paths with `index.html`), is fine; a JSON/database download is not.

## 1. Recommended: one container for website + API

The repository's `Dockerfile` builds the website and serves it from the same FastAPI process, so there is no CORS or cookie problem and
no Netlify is needed. Works on Render, Railway, Fly.io, an Azure/AWS container service or any VPS with Docker.
`.dockerignore` keeps the database, secrets and `.env` out of the image.

Required settings (environment variables on the host):

| Variable | Value |
|---|---|
| `ENV` | `production` (also turns the one-click demo login off) |
| `SECRET_KEY` | a long random string (32+ characters) |
| `DATABASE_URL` | `postgresql+psycopg://user:password@host:5432/msai` (use the host's managed Postgres; do not use SQLite there) |
| `COOKIE_SECURE` | `true` (the site must be served over HTTPS) |
| `STORAGE_ROOT` | a path on a **persistent disk** (e.g. `/data/storage`) |
| `QC_REPORTS_DIR` | a path on the same persistent disk (e.g. `/data/qc-reports`) |
| `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` | the first Super Admin (only used while the users table is empty) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the **new** Drive service-account key, pasted as a secret |
| `GOOGLE_DRIVE_FOLDER_ID` | the ID of the shared Drive folder (from its URL) |

Then create the 8 team accounts (Users & Roles page) with **new** passwords and let the Drive sync import again.

Honest limits: the local SQLite database is not migrated to Postgres automatically — on a new host the forms are re-imported from Drive,
while reviewer decisions, assignments and rule approvals made locally are not carried over (tell me if you need a migration).
Scanned PDFs need Tesseract; the Dockerfile installs it.

## 2. Optional: Netlify for the website, backend elsewhere

1. Host the backend as in section 1 (a public HTTPS address such as `https://msai-api.example.com`).
2. In `frontend/public/_redirects` put the API rule **above** the existing one, then rebuild:
   ```
   /api/*   https://msai-api.example.com/api/:splat   200
   /*       /index.html                               200
   ```
   The browser keeps talking to the Netlify address, so login cookies stay same-origin.
3. Netlify → *Add new site* → *Deploy manually* → drop the **`frontend\dist` folder** (the folder that directly contains `index.html`).
4. Check: the home page opens, `/app/rules` opens after login (no 404), and the two private URLs in section 0 return 404.

Without step 1–2 the site shows the pages but nothing can log in, because there is no backend behind `/api`.

## 2b. Optional: Vercel for the website, backend elsewhere

Same idea as Netlify: Vercel hosts only the website (`frontend`). The FastAPI backend cannot run on Vercel (serverless functions have no
persistent disk, no background threads for the Drive sync and QC, and short time limits) — host it as in section 1.

1. Vercel dashboard → *Add New… → Project* → import the GitHub repository `isngs-dev/mystery-shopping-ai`.
2. **Root Directory: `frontend`** (important — this keeps `backend/data` and `backend/secrets` out of the build). Framework: Vite.
   Build command and output are already set in `frontend/vercel.json`.
3. Before the first real use, add the API rule **above** the existing one in `frontend/vercel.json`, commit and push (Vercel redeploys):
   ```json
   { "source": "/api/:path*", "destination": "https://YOUR-BACKEND-HOST/api/:path*" }
   ```
4. Check as in section 0: the two private URLs must not download anything, and `/app/rules` must open (not 404) after login.

CLI alternative (from the `frontend` folder only): `npx vercel login`, then `npx vercel --prod`.

## 3. After the accidental upload

* Delete that Netlify site, create a new Drive service-account key (delete the old one in Google Cloud → IAM → Service accounts → Keys),
  change every team password. Treat the old database copy as exposed.
