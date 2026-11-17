# Mystery Shopping AI — Editor (Chrome extension)

Opens a panel on the **right side** of a RAW form in Google Drive, identifies the company, edits the form with **that company's grammar
rules**, lets a human cross-check, and creates the edited PDF with QC.

## How it works
1. Open a RAW PDF in Google Drive (`3353921_1.pdf`, `131227-1.pdf`, `3353722 - Admin Shop Review.pdf` …). It works like Grammarly: a round
   **badge** appears bottom-right with the number of suggestions, and **coloured underlines** are drawn on the page where an edit is proposed
   (red = correctness, blue = punctuation & spacing, purple = company rules & style). Hover an underline for a card with **Dismiss / Accept**;
   click the badge for the side panel with the full list. (If you only select a file in a folder, click the extension's toolbar button.)
2. **Company** — the server finds the form from the Drive file (or form ID) and reads the company from its Drive folder
   (`Client / Project`). If a model is connected it also reads the report text and warns when it thinks the report belongs to a different company.
3. **Grammar rules** — the rules of the QC error table's *Grammar Mistake* group that apply to this company: general rules + the client's / project's
   own rules. Rules still waiting for approval are shown, but not used.
4. **Editing starts at once**: the built-in corrector (spelling, doubled punctuation, missing / stray spaces, capitals, a/an, **quotation marks**
   — no space inside the marks, the full stop inside the closing mark —, brackets, apostrophes, `%` / `$`, run-together sentences) plus, when a model is
   configured, the AI editor with the company's guidelines (past tense, terminology …). Numbers, names, negations and links are never changed.
5. **Cross-check in the panel**: every edit shows before → after, the reason, whether it came from the rules or the AI, and a 🔍 to see the exact place
   on the page. Untick what you do not want, write a comment, press **Re-edit with AI** — the AI gets your comment and the rejected edits and answers again.
6. **Create edited PDF + QC**: only the ticked edits are typeset into a new edited PDF (next version `_2`), verified against the plan, stored in the
   platform, and QC runs on it. The panel shows the QC score and lets you download the QC report. The RAW is never changed.

## Install (developer mode)
1. Chrome → `chrome://extensions` → switch **Developer mode** on → **Load unpacked** → choose this `extension` folder.
2. Extension **Details → Extension options**: enter the server address (`https://mystery-shopping-ai.onrender.com` or `http://localhost:8000`) and
   press *Save* — Chrome asks once to allow that address.
3. Open a RAW form in Google Drive and sign in in the panel with your platform account (e-mail + password). The token is kept in the browser; the
   password is not stored.

Allowed roles: Admin, Manager (Editor & QC Manager), QC Lead (Editor & QC Lead), Editor. Only Manager / Admin can confirm; only a Manager can add rules.

## Server side (what must be set up)
* `AI_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=…` and `AI_ALLOW_EXTERNAL=true` (the PDFs' text is sent to the model — approve that first). Put these in the
  server's environment (Render → Environment), never in this extension. Without them the panel still works with the built-in rules and says so;
  comments cannot be turned into new edits.
* Company rules must be **approved** under *QC Rules* (the Drive checklists are imported as drafts) — by the Super Admin.
* The RAW must already be in the platform (the Drive auto-sync brings it within ~2 minutes). Otherwise the panel says so and you check again.

## Project, checklist and the human / leader cross-check
* **Which project?** The Drive folder names the client / project; the AI also reads the report text and the clients' checklists and says which client it
  thinks the report belongs to (e.g. CXE or APC). If that differs from the folder the panel offers **"Use CXE's checklist and rules"**.
* **Checklist rules for editing:** the client's checklist rules imported from Drive are used for editing even while they wait for the Super Admin's approval
  (marked *draft*); QC counts them only after approval.
* **Pop-up cards while you scroll** (⚡ switches them off): the edit in the middle of the screen gets a card with Accept / Dismiss, *Your wording* and a
  *Comment for the AI* box. **Ask AI** re-edits just that passage using your comment; **Add an edit the AI missed** adds your own edit.
* **Editors** (QC leads, editors) cross-check and press **Send to the leader**. The leader (Manager role) sees every final edit with the editors' comments,
  can still change them, and either **Confirms** (creates the edited PDF and starts QC for that form) or **Sends it back** with a note. The ☰ button lists
  the forms waiting for the leader.
* **The leader can add grammar rules** for the client or project (*+ Add a grammar rule*): used for editing at once, counted in QC after approval.
* Keep `DRIVE_AI_EDIT_RAW_ONLY=false` on the server, otherwise the Drive sync edits a RAW-only form by itself before a human has looked at it.

## Questions and answers
The report mixes the form's questions (fixed wording, never edited) with the shopper's answers. The server pairs every answer with the question in front
of it and gives the model both (`<form_questions>`), so "Please describe the circumstances" is corrected as a past-tense narrative and "what was said by the
teller" keeps the spoken words in quotation marks. The model fixes sentences too (agreement, tense, a missing word, two sentences run together).
Things the editor sees but must not change alone (unbalanced quotation marks, stray symbols, unknown words) are listed under *Needs your attention*.
Without a model only the built-in rules above run: they cannot rewrite a sentence.

## Screenshots (pictures of pages)
Text that exists only as a picture is read with OCR on the server (Tesseract is installed in the Docker image used on Render) and the corrected line is
typeset over a patch of the surrounding colour. Without OCR on the server such a PDF cannot be edited automatically and the panel says that. This part
could not be exercised on the development PC (no Tesseract there): check a real screenshot form after deploying.

## Develop
* `node --test extension/test/detect.test.js` — file-name / diff logic.
* UI preview without Chrome: `python -m http.server 5180` inside `extension/`, open `http://127.0.0.1:5180/dev/preview.html` (fake server).
