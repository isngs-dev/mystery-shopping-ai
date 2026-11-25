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

Allowed roles: Admin, Manager (Editor & QC Manager), QC Lead (Editor & QC Lead), Editor. Everyone listed can cross-check and forward a form; Manager / Admin also see the forwarded list; only a Manager can add rules.

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
* **The panel opens by itself** as soon as a RAW form is opened in Drive and starts working (client, rules, AI edits). Whoever cross-checks the edits
  (editor, QC lead, manager) forwards the form with **Create edited PDF + QC**: the edited PDF is created from the accepted edits and QC starts.
* **The leader** (Manager) sees what was decided under **☰ Forwarded forms**: who forwarded it, how many edits were accepted, dismissed, reworded by a
  person, added by hand or commented (with the details), and the QC result.
* **The leader can add grammar rules** for the client or project (*+ Add a grammar rule*): used for editing at once, counted in QC after approval.
* Keep `DRIVE_AI_EDIT_RAW_ONLY=false` on the server, otherwise the Drive sync edits a RAW-only form by itself before a human has looked at it.

## Questions and answers
The report mixes the form's questions (fixed wording, never edited) with the shopper's answers. The server pairs every answer with the question in front
of it and gives the model both (`<form_questions>`), so "Please describe the circumstances" is corrected as a past-tense narrative and "what was said by the
teller" keeps the spoken words in quotation marks. The model fixes sentences too (agreement, tense, a missing word, two sentences run together).
Things the editor sees but must not change alone (unbalanced quotation marks, stray symbols, unknown words) are listed under *Needs your attention*.
Without a model only the built-in rules above run: they cannot rewrite a sentence.

## Intelli-Shop shop review pages (HTML website)
On `*.intelli-shop.com` and `*.cxeview.com` (same software) the extension (`intellishop.js`) reads the shop review page (`ShopReview-Right.php?JobID=…`): every question with a text box
(`textarea[name="Q[id]"]`) is an answer. The client comes from the page header (`.white16bold`, for example "Love's: T-Stop") and is matched with the
platform's clients; the answers go to `POST /api/ext/text/propose` (same editor, rules, safety policy and question awareness as for PDFs).
Edits are underlined inside the boxes; a card pops up at the edit while you scroll and moves with it (hover an underline for its card): who suggests it,
Accept / Dismiss, your own wording, a comment box for the AI (*Ask AI*).
**Accept writes the corrected wording into that answer's own box on the website page** (Undo puts the old words back; the other edits of the box move along).
Nothing reaches the website's record until you press **Save to site & download PDF** in the panel (it asks once): that presses the website's own
*UPDATE SURVEY* button and, when the page has reloaded, downloads the form's PDF from the website itself (the "PDF" button of its Company View,
`ClientShopView.php?...&genpdf=1`), so the file shows the edited answers. **Download PDF** alone fetches the PDF as it is saved on the website.
If the website does not reload within 20 seconds (a required answer, a warning) nothing is downloaded and the panel says so.
Sign in once from the round badge. After updating the extension, reload it and refresh the Intelli-Shop tab.

### Guidelines are found and learned by themselves
On every shop page the extension looks for the client's guideline file ("Download Guidelines" -> the client brief PDF), reads it with the signed-in
website session and sends its fingerprint (`POST /api/ext/guidelines/learn`). A file the platform has already seen costs one small request. An **unseen file**
(a new project of a client, or an updated brief) is uploaded once; the platform's AI reads it and drafts the client's **editing rules** (spelling of brand
names, forbidden terms, tense, punctuation, topics that must not appear ...; nothing about what the shopper must do on the visit). The rules are created as
*pending approval* (used for editing at once, counted in QC after the Super Admin approves them), apply only to pages that link that file, and are listed in
the panel ("Learned N editing rules from ..."). The file's text is data for the AI, never instructions. Nothing is set up by hand; a client that is not in the
platform is reported ("client not set up"), never created.

### Rules & guidelines tab, and your own note under every edit
The panel has two tabs. **Edits** lists the suggestions; **Rules & guidelines** shows what happened with the page's guideline file, the rules this client has
(from the guidelines, typed by the team, from the checklist) and a text box **Add rules & regulations**: one rule per line, for this project or the whole client
(start a line with a type such as `spelling:` or `past tense:`). It is meant for when the AI could not read the guidelines, or for something the file does not
say. No AI is needed for it, so it works even when the model is down. Typed rules are drafts: used for editing at once, counted in QC after approval, and the
answers are checked again straight away. Under every suggested edit (in the panel and on the pop-up card) there is a **note box** for the editor's own point of view
(why the edit is right or wrong, a problem met while editing); notes are kept with the page's job and written to the platform's audit trail
(`POST /api/ext/text/note`), and "Ask AI" takes the note into account. The panel opens by itself on a shop form page.

### Sign-in: Show password and Forgot password
The sign-in box has a **Show / Hide** button on the password (to check what was typed) and a **Forgot password?** link (on the Drive panel and on the Intelli-Shop /
cxeVIEW panel). The link e-mails a one-time code (`POST /api/ext/forgot`) to the account's address; the code + a new password (`POST /api/ext/reset`) sets the
password, signs out every old session and lifts a "Too many failed attempts" lock-out. The answer is the same for any e-mail, a code works once for 30 minutes,
dies after 5 wrong tries, and at most 3 codes are sent per account per hour. **The server needs an SMTP mailbox** (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASSWORD`, `SMTP_FROM` in the host's environment; for Google Workspace use smtp.gmail.com:587 and an App Password). Without it nothing is e-mailed: the person is told
so and the Super Admins get an in-app notification and can reset the password in Admin → Users.

### Upload guidelines, checklists & rules for any client
The **Rules & guidelines** tab has an **Upload** section: choose files (PDF, Word `.docx`, Excel `.xlsx`, text; many at once, or a whole folder), type the client
(the page's client is suggested; a list of the platform's clients helps), choose *Whole client* or *This project only* and press **Upload & learn**. The AI reads each file
(a long checklist in pieces) and turns it into editing rules for that client (`POST /api/ext/guidelines/upload`). From then on **every form whose header names that client
follows those rules** (the client is recognised from the page header / site name, exactly as before), whichever project it belongs to. Options: take the client from each
file's name when it names one; create the client when its name is new (Managers). The rules are drafts: used for editing at once, counted in QC after the Super Admin approves
them (Rules page, bulk approval). The tab lists the files of the client with **Remove** (its rules stop being used; the uploader or a Manager). Same file twice = added once.
**Accept all N edits** writes every open suggestion into the answer boxes in one go (each can still be undone); then **Save to site & download PDF**.

### Import a whole Google Drive folder (Managers)
In the Rules tab, **Import from Google Drive** reads a Drive folder with the platform's own Drive access (the same service account as the Drive sync; the folder must be shared
with it, which it is when it sits inside the synced folder). Two quick buttons: the **Checklist** folder (every file is a client's checklist; the client is read from the
file name: `CXE_Checklist` -> CXE, `Alta 360_Checklist` -> Alta 360; an unknown client is created) and **Basic MS Documentation** (general guidance for every client: its rules carry no
client). Or paste another folder's link / name and choose the mode. Google Docs are read as Word files; PDF, Word, Excel and text files are supported; a file already read costs nothing.
It runs in the background (`POST /api/ext/guidelines/drive`, `GET /api/ext/guidelines/drive/{job}`); the card shows each file's result.

## Screenshots (pictures of pages)
Text that exists only as a picture is read with OCR on the server (Tesseract is installed in the Docker image used on Render) and the corrected line is
typeset over a patch of the surrounding colour. Without OCR on the server such a PDF cannot be edited automatically and the panel says that. This part
could not be exercised on the development PC (no Tesseract there): check a real screenshot form after deploying.

## Develop
* `node --test extension/test/detect.test.js` — file-name / diff logic.
* UI preview without Chrome: `python -m http.server 5180` inside `extension/`, open `http://127.0.0.1:5180/dev/preview.html` (fake server).
