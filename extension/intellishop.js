/* Grammarly-style editor for the shopper's answers on an Intelli-Shop "Shop Review" page (the HTML website with a text box under every question).
   - The page's own answer boxes are read (question + answer); the platform recognises the client from the page header, applies that client's checklist and
     grammar rules, and returns the edits.
   - Edits are drawn as coloured underlines under the words inside the boxes. While you scroll, a card pops up at the edit in the middle of the screen and
     scrolls with it (hover any underline to see its card). The card says who suggests it (AI or the built-in rules) and offers Accept / Dismiss, your own
     wording, and a comment box for the AI ("Ask AI" re-edits that passage with your comment).
   - Accept writes the corrected wording into that answer's own box on the page (Undo puts the old words back). Nothing reaches the website's database until
     the person presses "Save to site & download PDF": that presses the website's own UPDATE SURVEY button and, when the page has reloaded, downloads the
     form's PDF from the website itself (the same "PDF" button of its Company View), so the file shows the edited answers.
   Everything that comes from the page or the server is inserted with textContent, never as HTML. */
(function () {
  if (window.__msaiShop) return
  window.__msaiShop = true
  let dead = false
  const timers = []
  const alive = () => { try { return !!(chrome.runtime && chrome.runtime.id) } catch (e) { return false } }
  const every = (fn, ms) => { timers.push(setInterval(() => { if (!alive()) return gone(); fn() }, ms)) }
  function gone() {
    if (dead) return
    dead = true
    timers.forEach(clearInterval)
    try { host.remove() } catch (_) { /* page is going away */ }
  }
  const send = async (msg) => {
    if (dead || !alive()) { gone(); return { ok: false, error: 'The extension was updated. Refresh this page (F5).' } }
    try { return await chrome.runtime.sendMessage(msg) } catch (e) { gone(); return { ok: false, error: 'The extension was updated. Refresh this page (F5).' } }
  }
  const api = (method, path, body) => send({ type: 'api', method, path, body })

  const GREEN = '#15a37f'
  const CSS = `
  :host { all: initial; }
  * { box-sizing: border-box; }
  .wrap { position: fixed; top: 0; right: 0; height: 100vh; width: 370px; z-index: 2147483000; display: flex; flex-direction: column;
    font: 13px/1.45 system-ui, 'Segoe UI', Arial, sans-serif; color: #1f2937; background: #fff; border-left: 1px solid #e5e7eb; box-shadow: -6px 0 24px rgba(15,23,42,.12); }
  .wrap.hidden { display: none; }
  header { display: flex; align-items: center; gap: 4px; padding: 12px 10px 10px 12px; border-bottom: 1px solid #eef0f3; }
  .logo { width: 24px; height: 24px; border-radius: 50%; background: ${GREEN}; color: #fff; display: grid; place-items: center; font-weight: 800; font-size: 13px; flex: none; margin-right: 4px; }
  .title { font-weight: 700; flex: 1; min-width: 0; } .title span { display: block; font-weight: 500; font-size: 11px; color: #6b7280; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  button { font: inherit; cursor: pointer; border-radius: 8px; border: 1px solid #d1d5db; background: #fff; color: #374151; padding: 5px 10px; }
  button:hover { background: #f3f4f6; } button:disabled { opacity: .5; cursor: default; }
  button.primary { background: ${GREEN}; border: 0; color: #fff; font-weight: 700; padding: 8px 12px; border-radius: 999px; } button.primary:hover { background: #0f8a69; }
  button.icon { padding: 2px 7px; border-color: transparent; background: transparent; color: #6b7280; font-size: 15px; } button.icon.on { color: ${GREEN}; }
  .body { flex: 1; overflow: auto; padding: 12px; background: #f7f8fa; }
  .foot { padding: 10px 12px; border-top: 1px solid #eef0f3; display: grid; gap: 8px; background: #fff; }
  .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 10px 12px; margin-bottom: 10px; }
  .muted { color: #6b7280; } .small { font-size: 12px; } .errtext { color: #b42318; font-size: 12px; min-height: 1em; }
  .warn { background: #fff8e6; border-color: #f2d27a; } .err { background: #fdecec; border-color: #f2a5a5; } .ok { background: #e9f8f2; border-color: #9edbc4; } .info { background: #eef4ff; border-color: #b9cdf7; }
  .chip { display: inline-block; font-size: 11px; padding: 1px 8px; border-radius: 999px; background: #eef2f7; color: #475569; margin-right: 4px; }
  .chip.ai { background: #ede9fe; color: #5b3fc4; } .chip.rules { background: #e0f2fe; color: #0b5c8a; }
  h3 { margin: 0 0 6px; font-size: 13px; }
  .q { font-size: 11px; font-weight: 700; color: #475569; margin: 10px 0 4px; text-transform: none; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; background: var(--c); }
  .red { --c: #e5484d; } .blue { --c: #2f6fed; } .purple { --c: #8b5cf6; } .green { --c: #15a37f; } .grey { --c: #cbd5e1; }
  .sug { background: #fff; border: 1px solid #e5e7eb; border-left: 4px solid var(--c); border-radius: 10px; padding: 7px 10px; margin-bottom: 6px; cursor: pointer; }
  .sug:hover { box-shadow: 0 2px 10px rgba(15,23,42,.10); } .sug.off { opacity: .6; }
  .del { color: #b42318; text-decoration: line-through; } .add { color: #067a55; font-weight: 700; }
  .acts { display: flex; gap: 6px; margin-top: 8px; align-items: center; flex-wrap: wrap; } .acts button { padding: 3px 12px; border-radius: 999px; font-size: 12px; }
  .state { font-size: 11px; margin-left: auto; color: #6b7280; }
  .ctl { margin-top: 8px; padding-top: 8px; border-top: 1px dashed #e2e8f0; display: grid; gap: 6px; }
  .ctl label { margin: 0; font-size: 11px; font-weight: 600; color: #64748b; } .ctl .row { display: flex; gap: 6px; } .ctl .row input { flex: 1; }
  .said { font-size: 12px; background: #f4f6f8; border-radius: 8px; padding: 5px 8px; }
  textarea, input { width: 100%; font: inherit; color: inherit; background: #fff; border: 1px solid #d1d5db; border-radius: 8px; padding: 6px 8px; }
  textarea { min-height: 44px; resize: vertical; }
  label { display: block; margin: 8px 0 3px; font-weight: 600; }
  .spin { display: inline-block; width: 12px; height: 12px; border: 2px solid ${GREEN}; border-right-color: transparent; border-radius: 50%; animation: s .8s linear infinite; vertical-align: -2px; margin-right: 6px; }
  @keyframes s { to { transform: rotate(360deg); } }
  a { color: #0f8a69; }
  .badge { position: fixed; right: 18px; bottom: 22px; z-index: 2147483001; width: 46px; height: 46px; border-radius: 50%; border: 0; padding: 0; background: ${GREEN}; color: #fff;
    font-weight: 800; font-size: 20px; box-shadow: 0 4px 14px rgba(15,23,42,.35); display: grid; place-items: center; }
  .badge:hover { background: #0f8a69; } .badge.panel-open { right: 386px; }
  .badge .n { position: absolute; top: -4px; right: -4px; min-width: 20px; height: 20px; padding: 0 5px; border-radius: 999px; background: #e5484d; color: #fff; font-size: 11px; font-weight: 700; display: grid; place-items: center; border: 2px solid #fff; }
  .badge .n.zero { background: #15a37f; }
  .layer { position: fixed; inset: 0; pointer-events: none; z-index: 2147482000; }
  .ul { position: fixed; pointer-events: none; border-bottom: 3px solid var(--c); background: color-mix(in srgb, var(--c) 12%, transparent); border-radius: 2px 2px 0 0; }
  .ul.flash { background: color-mix(in srgb, var(--c) 30%, transparent); }
  .pop { position: fixed; z-index: 2147483002; width: 330px; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px 14px;
    box-shadow: 0 10px 30px rgba(15,23,42,.28); color: #1f2937; font: 13px/1.45 system-ui, 'Segoe UI', Arial, sans-serif; }
  .pop .top { display: flex; align-items: center; } .pop .top .x { margin-left: auto; padding: 0 6px; border: 0; background: transparent; color: #6b7280; font-size: 15px; }
  .pop .cat { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; color: #64748b; }
  .pop .change { font-size: 16px; margin: 6px 0 4px; } .pop .reason { font-size: 12px; color: #6b7280; margin-bottom: 6px; }
  `

  const host = document.createElement('div')
  host.id = 'msai-shop-host'
  const root = host.attachShadow({ mode: 'open' })
  const style = document.createElement('style')
  style.textContent = CSS
  root.appendChild(style)

  function el(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag)
    for (const [k, v] of Object.entries(attrs)) { if (v !== undefined && v !== null) n.setAttribute(k, v) }
    for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k.nodeType ? k : document.createTextNode(String(k)))
    return n
  }
  const set = (node, ...kids) => { node.replaceChildren(...kids.flat().filter(Boolean)) }
  const card = (cls, ...kids) => el('div', { class: 'card ' + (cls || '') }, ...kids)

  const wrap = el('div', { class: 'wrap hidden' })
  const bodyEl = el('div', { class: 'body' })
  const footEl = el('div', { class: 'foot' })
  const subtitle = el('span', {}, '')
  const layer = el('div', { class: 'layer' })
  const badgeN = el('span', { class: 'n zero' }, '…')
  const badge = el('button', { class: 'badge', title: 'Mystery Shopping AI' }, 'M', badgeN)
  const autoBtn = el('button', { class: 'icon on', title: 'Pop-up cards while you scroll: on' }, '⚡')
  const gear = el('button', { class: 'icon', title: 'Settings' }, '⚙')
  const minBtn = el('button', { class: 'icon', title: 'Close the panel' }, '⟩')
  wrap.append(el('header', {}, el('div', { class: 'logo' }, 'M'), el('div', { class: 'title' }, 'Mystery Shopping AI', subtitle), autoBtn, gear, minBtn), bodyEl, footEl)
  root.append(layer, wrap, badge)

  const S = { session: null, rows: [], edits: [], info: null, auto: true, seen: new Set(), dismissed: new Set(), busy: false, error: null, checked: false, orig: new Map(), confirm: false, note: null }
  let tip = null, tipHover = null

  function setPanel(openIt) { wrap.classList.toggle('hidden', !openIt); badge.classList.toggle('panel-open', openIt); relayout() }
  badge.addEventListener('click', () => setPanel(wrap.classList.contains('hidden')))
  minBtn.addEventListener('click', () => setPanel(false))
  gear.addEventListener('click', () => send({ type: 'options' }))
  autoBtn.addEventListener('click', () => {
    S.auto = !S.auto
    autoBtn.classList.toggle('on', S.auto)
    autoBtn.title = 'Pop-up cards while you scroll: ' + (S.auto ? 'on' : 'off')
    if (!S.auto && tip && tip.auto) hideTip(); else autoSoon(100)
  })

  // ------------------------------------------------------------------------------------------------ read the page
  const SKIP_QUESTION = /^AI Editor Suggestions/i
  function questionOf(ta) {
    const row = ta.closest('tr')
    const d = row && row.querySelector('div[align="left"]')
    if (!d) return ''
    const first = [...d.childNodes].find((n) => n.nodeType === 3 && n.data.trim())
    return ((first ? first.data : d.textContent.split('Spell Check')[0]) || '').replace(/\s+/g, ' ').trim()
  }
  function answerBoxes() {
    return [...document.querySelectorAll('textarea[name^="Q["]')].filter((t) => t.offsetParent !== null && !t.readOnly && !t.disabled && t.value.trim().split(/\s+/).length >= 2 &&
      !SKIP_QUESTION.test(questionOf(t))).map((ta) => ({ id: (ta.name.match(/Q\[(\d+)\]/) || [])[1] || ta.name, ta, question: questionOf(ta) }))
  }
  function clientHint() {
    const h = document.querySelector('.white16bold')
    // the form's name ("Love's: T-Stop") and the site ("cxeview.com" -> client CXE): the server matches either with the platform's clients
    return ((h ? h.textContent : document.title).replace(/\s+/g, ' ').trim().slice(0, 200) + ' | ' + location.hostname.replace(/^www\./, ''))
  }

  // ------------------------------------------------------------------------------------------------ the client's guidelines on the website
  /** The guideline file the shop page links ("Download Guidelines" -> the client's brief PDF). */
  function guidelineLinks() {
    const seen = new Set()
    const out = []
    for (const a of document.querySelectorAll('a[href]')) {
      const href = a.getAttribute('href') || ''
      if (!href || href.startsWith('#') || /^javascript:/i.test(href)) continue
      const label = (a.textContent || '').replace(/\s+/g, ' ').trim()
      const isFile = /\.(pdf|txt)(\?|#|$)/i.test(href)
      if (!(/guideline|client brief|\bbrief\b|instruction/i.test(label) && (isFile || /guideline/i.test(label)) || (isFile && /guideline|brief/i.test(href)))) continue
      let url
      try { url = new URL(href, location.href) } catch (_) { continue }
      if (!/^https?:$/.test(url.protocol) || url.origin !== location.origin || seen.has(url.href)) continue
      seen.add(url.href)
      out.push({ url: url.href, label: label || 'Guidelines' })
      if (out.length >= 3) break
    }
    return out
  }
  const toB64 = (blob) => new Promise((res, rej) => { const f = new FileReader(); f.onload = () => res(String(f.result).split(',')[1] || ''); f.onerror = rej; f.readAsDataURL(blob) })
  const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')

  /** Reads each guideline file of the page once and gives it to the platform: a file it has not seen is read by the AI and becomes editing rules for the client
      (drafts, used for editing at once); a file it knows costs one small request. A new project of a client is learned the same way, without any set-up. */
  async function learnGuidelines() {
    S.gl = []
    for (const g of guidelineLinks()) {
      const item = { name: g.label, url: g.url, sha: null, status: 'error', rules: 0, learned: [], message: null, newProject: false }
      S.gl.push(item)
      try {
        const r = await fetch(g.url, { credentials: 'include' })
        if (!r.ok) throw new Error('The website returned ' + r.status + ' for the guideline file.')
        const blob = await r.blob()
        if (blob.size > 6_000_000) throw new Error('The guideline file is larger than 6 MB.')
        item.sha = hex(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer()))
        const body = { client_hint: clientHint(), project: projectName(), sha256: item.sha, filename: decodeURIComponent(g.url.split('?')[0].split('/').pop() || 'guidelines.pdf'), source_url: g.url }
        let a = await api('POST', '/api/ext/guidelines/learn', body)
        if (a.ok && a.data.status === 'need_content') {
          set(bodyEl, card('', el('span', { class: 'spin' }), 'New guideline file: the AI is reading it and writing the client’s editing rules … (one time only)'))
          a = await api('POST', '/api/ext/guidelines/learn', { ...body, content_b64: await toB64(blob) })
        }
        if (!a.ok) throw new Error(a.error || 'The platform did not accept the guideline file.')
        Object.assign(item, { status: a.data.status, rules: a.data.rules || 0, learned: a.data.learned || [], message: a.data.message || null, newProject: !!a.data.new_project })
      } catch (e) { item.message = (e && e.message) || String(e) }
    }
    return S.gl
  }
  const guidelineShas = () => (S.gl || []).filter((g) => g.sha).map((g) => g.sha.slice(0, 16))
  function projectName() {
    const h = document.querySelector('.white16bold')
    return ((h ? h.textContent : document.title) || '').replace(/\s+/g, ' ').trim().slice(0, 160)
  }

  // ------------------------------------------------------------------------------------------------ sign in / check
  function signIn(message) {
    const email = el('input', { type: 'email', placeholder: 'you@isngs.com', autocomplete: 'username' })
    const pass = el('input', { type: 'password', placeholder: 'Password', autocomplete: 'current-password' })
    const err = el('div', { class: 'errtext', style: 'margin-top:6px' }, message || '')
    const go = el('button', { class: 'primary' }, 'Sign in')
    const doLogin = async () => {
      err.textContent = ''; go.disabled = true
      const r = await send({ type: 'login', email: email.value.trim(), password: pass.value })
      go.disabled = false
      if (!r.ok) { err.textContent = r.error || 'Sign-in failed.'; return }
      S.session = r.data.user
      check()
    }
    go.addEventListener('click', doLogin)
    pass.addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin() })
    set(bodyEl, card('', el('h3', {}, 'Sign in to Mystery Shopping AI'), el('div', { class: 'small muted' }, 'Use your platform account (Editor, QC Reviewer, Manager or Admin).'),
      el('label', {}, 'Email'), email, el('label', {}, 'Password'), pass, err, el('div', { style: 'margin-top:8px' }, go)))
    set(footEl)
    setBadge(null)
    setPanel(true)
  }

  async function check(note) {
    if (S.busy) return
    S.busy = true
    S.rows = answerBoxes()
    for (const x of S.rows) if (!S.orig.has(x.id)) S.orig.set(x.id, x.ta.value)      // what the answer said when the page was opened (to count what was edited)
    set(bodyEl, card('', el('span', { class: 'spin' }), `Checking ${S.rows.length} answer${S.rows.length === 1 ? '' : 's'}…`))
    setBadge(null)
    const r = await api('POST', '/api/ext/text/propose', {
      client_hint: clientHint(), guidelines: guidelineShas(), rejected: [...S.dismissed].map((k) => { const [o, n] = k.split('\u0001'); return { original: o, replacement: n } }),
      answers: S.rows.map((x) => ({ id: x.id, question: x.question, text: x.ta.value })),
    })
    S.busy = false
    if (r.status === 401) return signIn('Please sign in.')
    if (!r.ok) { S.error = r.error; S.info = null; S.edits = []; renderPanel(); relayout(); setPanel(true); return }
    S.error = null
    S.info = r.data
    S.checked = true
    S.edits = r.data.edits.map((e) => ({ ...e, state: 'open', final: e.replacement, said: [], valid: true }))
    S.seen.clear()
    renderPanel(); relayout(); setBadge(S.edits.length)
    if (!S.edits.length && !S.error) setPanel(true)
  }

  function setBadge(n) { badgeN.textContent = n === null ? '…' : String(n); badgeN.classList.toggle('zero', n === 0) }

  // ------------------------------------------------------------------------------------------------ the underlines
  const GROUP = { spelling: 'red', grammar: 'red', sentence: 'red', profanity: 'red', punctuation: 'blue', quotation: 'blue', symbols: 'blue', spacing: 'blue', capitalization: 'blue', capitalisation: 'blue' }
  const LABEL = { spelling: 'Spelling', grammar: 'Grammar', sentence: 'Sentence', quotation: 'Quotation marks', symbols: 'Symbols', capitalization: 'Capital letters', punctuation: 'Punctuation', tense: 'Past tense', terminology: 'Terminology', profanity: 'Language' }
  const groupOf = (e) => (e.state === 'accepted' ? 'green' : GROUP[(e.category || '').toLowerCase()] || 'purple')
  const labelOf = (e) => LABEL[(e.category || '').toLowerCase()] || (e.category ? e.category[0].toUpperCase() + e.category.slice(1) : 'Edit')
  const rowOf = (qid) => S.rows.find((x) => x.id === qid)

  /** Where the characters of an answer are drawn: the text of the box is laid out in a hidden copy with the same font, width and padding. */
  const mirror = document.createElement('div')
  function rectsFor(ta, start, end) {
    const cs = getComputedStyle(ta)
    const m = mirror
    if (!m.isConnected) document.body.appendChild(m)
    const props = ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'wordSpacing', 'textTransform', 'textIndent', 'lineHeight', 'tabSize', 'overflowWrap', 'wordBreak',
      'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'textAlign', 'direction']
    for (const p of props) m.style[p] = cs[p]
    const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight)
    m.style.cssText += `;position:fixed;left:-99999px;top:0;visibility:hidden;white-space:pre-wrap;box-sizing:content-box;border:0;margin:0;width:${Math.max(10, ta.clientWidth - padX)}px;height:auto;overflow:visible;`
    m.textContent = ta.value
    const tn = m.firstChild
    if (!tn || start >= tn.length) return []
    const rg = document.createRange()
    rg.setStart(tn, start)
    rg.setEnd(tn, Math.min(end, tn.length))
    const mr = m.getBoundingClientRect()
    const tr = ta.getBoundingClientRect()
    const ox = tr.left + ta.clientLeft - ta.scrollLeft - mr.left, oy = tr.top + ta.clientTop - ta.scrollTop - mr.top
    const clip = { l: tr.left + ta.clientLeft, t: tr.top + ta.clientTop, r: tr.left + ta.clientLeft + ta.clientWidth, b: tr.top + ta.clientTop + ta.clientHeight }
    const out = []
    for (const r of rg.getClientRects()) {
      const x0 = Math.max(clip.l, r.left + ox), x1 = Math.min(clip.r, r.right + ox), y0 = Math.max(clip.t, r.top + oy), y1 = Math.min(clip.b, r.bottom + oy)
      if (x1 - x0 > 1 && y1 - y0 > 1) out.push({ left: x0, top: y0, width: x1 - x0, height: y1 - y0 })
    }
    return out
  }

  /** The answer was typed in after the check: keep an edit only while its words are still in the box (move it when text was added before it). */
  function revalidate(e) {
    const row = rowOf(e.qid)
    if (!row) return (e.valid = false)
    const v = row.ta.value
    const now = shown(e)                                                             // the words that are in the box for this edit right now
    if (v.slice(e.start, e.end) === now) return (e.valid = true)
    let best = -1
    for (let i = v.indexOf(now); i !== -1; i = v.indexOf(now, i + 1)) { if (best < 0 || Math.abs(i - e.start) < Math.abs(best - e.start)) best = i }
    if (best < 0) return (e.valid = false)
    e.start = best; e.end = best + now.length
    return (e.valid = true)
  }
  const shown = (e) => (e.applied ? e.final : e.original)

  /** Accept: the corrected wording is written into the page's own box (the site sees a normal edit: input + change events). */
  function setBox(ta, value) {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
    setter.call(ta, value)
    ta.dispatchEvent(new Event('input', { bubbles: true }))
    ta.dispatchEvent(new Event('change', { bubbles: true }))
  }
  /** Replace [e.start, e.end) of the box by `text`; the other edits of the same box that sit after it move by the change in length. */
  function splice(e, text) {
    const row = rowOf(e.qid)
    if (!row || !revalidate(e)) return false
    const v = row.ta.value
    const delta = text.length - (e.end - e.start)
    const end0 = e.end
    setBox(row.ta, v.slice(0, e.start) + text + v.slice(e.end))
    for (const o of S.edits) { if (o !== e && o.qid === e.qid && o.start >= end0) { o.start += delta; o.end += delta } }
    e.end = e.start + text.length
    return true
  }
  function applyEdit(e) {
    if (e.applied) return true
    if (!splice(e, e.final)) return false
    e.applied = true; e.state = 'accepted'
    return true
  }
  function undoEdit(e) {
    if (!e.applied) return true
    if (!splice(e, e.original)) return false
    e.applied = false; e.state = 'open'
    return true
  }

  let flashId = null
  function relayout() {
    if (dead) return
    const kids = []
    for (const e of S.edits) {
      e.rects = []
      if (e.state === 'dismissed' || !revalidate(e)) continue
      const row = rowOf(e.qid)
      const tr = row.ta.getBoundingClientRect()
      if (tr.bottom < -50 || tr.top > innerHeight + 50) continue
      e.rects = rectsFor(row.ta, e.start, e.end)
      for (const r of e.rects) {
        const u = el('div', { class: 'ul ' + groupOf(e) + (flashId === e.id ? ' flash' : '') })
        u.style.cssText = `left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px`
        kids.push(u)
      }
    }
    layer.replaceChildren(...kids)
    trackCard()
  }
  const visible = (e) => e.rects && e.rects.some((r) => r.top < innerHeight && r.top + r.height > 0 && r.left < innerWidth)

  // ------------------------------------------------------------------------------------------------ the card
  function hideTip() { if (tip) { tip.remove(); tip = null } }
  function showCard(e, auto) {
    if (tip && tip.edit === e && tip.auto === auto) return trackCard()
    hideTip()
    const own = el('input', { type: 'text', value: e.final })
    const comment = el('textarea', { placeholder: 'Tell the AI what to change (e.g. “keep past tense”) …' })
    const who = e.source === 'ai' ? el('span', { class: 'chip ai' }, 'AI editor') : el('span', { class: 'chip rules' }, 'Grammar rules')
    const status = el('div', { class: 'small muted' })
    const accept = el('button', { class: 'primary' }, e.applied ? '✓ Applied' : 'Accept')
    if (e.applied) accept.disabled = true
    const dismiss = el('button', {}, e.applied ? 'Undo' : 'Dismiss')
    const copy = el('button', {}, 'Copy fix')
    const ask = el('button', {}, 'Ask AI')
    const note = e.applied ? el('div', { class: 'small ok card', style: 'margin:6px 0' }, 'Written into the answer box. It is saved on the website only when you press “Save to site & download PDF”. Undo puts the old words back.') : null
    accept.addEventListener('click', () => {
      e.final = own.value.trim() || e.replacement
      if (!applyEdit(e)) { status.textContent = 'The answer changed there; press “Check again”.'; return }
      decided(); showCard0(e, auto)
    })
    dismiss.addEventListener('click', () => {
      if (e.applied) { if (!undoEdit(e)) { status.textContent = 'The answer changed there; press “Check again”.'; return } decided(); showCard0(e, auto); return }
      e.state = 'dismissed'; S.dismissed.add(e.original + '\u0001' + e.replacement); hideTip(); decided()
    })
    copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(own.value.trim() || e.replacement); copy.textContent = 'Copied' } catch (_) { own.select(); copy.textContent = 'Select + Ctrl+C' } })
    ask.addEventListener('click', async () => {
      if (comment.value.trim().length < 2) { status.textContent = 'Write a short comment first.'; return }
      ask.disabled = true; status.textContent = ''; status.append(el('span', { class: 'spin' }), 'The AI is re-editing…')
      await reedit(e, comment.value.trim())
    })
    const p = el('div', { class: 'pop' },
      el('div', { class: 'top' }, who, el('span', { class: 'cat' }, labelOf(e)), el('button', { class: 'x', title: 'Close' }, '✕')),
      el('div', { class: 'change' }, el('span', { class: 'del' }, e.original), ' → ', el('span', { class: 'add' }, e.replacement)),
      el('div', { class: 'reason' }, e.reason || ''), ...e.said.map((s) => el('div', { class: 'said' }, 'You: ' + s)), note,
      el('div', { class: 'ctl' }, el('label', {}, 'Your own wording'), el('div', { class: 'row' }, own, copy)),
      el('div', { class: 'acts' }, accept, dismiss, status),
      el('div', { class: 'ctl' }, el('label', {}, 'Comment for the AI'), comment, el('div', { class: 'row' }, ask)))
    p.querySelector('.x').addEventListener('click', () => hideTip())
    p.addEventListener('mouseenter', () => { tipHover = true })
    p.addEventListener('mouseleave', () => { tipHover = false })
    if (e.applied) own.disabled = true
    p.edit = e; p.auto = auto
    tip = p
    root.append(p)
    placeCard(e)
  }
  const showCard0 = (e, auto) => { const keep = tip && tip.style.cssText; hideTip(); showCard(e, auto); if (tip && keep) tip.style.cssText = keep }

  /** The card sits under its underline and moves with it while the page scrolls (the layout runs on every scroll). */
  function placeCard(e) {
    if (!tip) return
    const r = (e.rects || [])[0]
    if (!r) { hideTip(); return }
    const w = 330, h = tip.offsetHeight
    const limit = innerWidth - (wrap.classList.contains('hidden') ? 0 : 380) - 12
    const below = r.top + r.height + 8 + h < innerHeight
    tip.style.left = Math.max(8, Math.min(limit - w, r.left)) + 'px'
    tip.style.top = Math.max(8, below ? r.top + r.height + 8 : r.top - 8 - h) + 'px'
  }
  function trackCard() {
    if (!tip) return
    const e = tip.edit
    if (!S.edits.includes(e) || e.state === 'dismissed' || !e.valid) return hideTip()
    if (!visible(e)) { if (tip.auto) hideTip(); return }
    placeCard(e)
  }

  /** "Ask AI": only that answer is sent again, with the person's comment; its new edits replace the open ones of that box. */
  async function reedit(e, text) {
    const row = rowOf(e.qid)
    const r = await api('POST', '/api/ext/text/propose', {
      client_hint: clientHint(), guidelines: guidelineShas(), client_id: S.info && S.info.company ? S.info.company.client_id : null,
      item_feedback: [{ id: e.id, original: e.original, replacement: e.replacement, comment: text }], rejected: [...S.dismissed].map((k) => { const [o, n] = k.split('\u0001'); return { original: o, replacement: n } }),
      answers: [{ id: row.id, question: row.question, text: row.ta.value }],
    })
    if (!r.ok) { if (tip) { const s = tip.querySelector('.ctl + .acts .small, .acts .small'); if (s) s.textContent = r.error } return }
    const keep = S.edits.filter((x) => x.qid !== e.qid || x.state !== 'open')
    const fresh = r.data.edits.map((n) => ({ ...n, state: 'open', final: n.replacement, said: (n.start < e.end && n.end > e.start) ? [text] : [], valid: true }))
    S.edits = [...keep, ...fresh.filter((n) => !keep.some((k) => k.qid === n.qid && k.start < n.end && k.end > n.start))].sort((a, b) => (+a.qid - +b.qid) || a.start - b.start)
    hideTip(); relayout(); renderPanel(); setBadge(S.edits.filter((x) => x.state === 'open').length)
    const again = fresh.find((n) => n.said.length) || fresh[0]
    const cur = again && S.edits.find((x) => x.id === again.id)
    if (cur && visible(cur)) showCard(cur, false)
  }
  function decided() { renderPanel(); relayout(); setBadge(S.edits.filter((x) => x.state === 'open').length) }

  // hover: a card for the underline under the mouse
  let mv = null, hideAt = 0
  addEventListener('mousemove', (ev) => {
    if (mv) return
    mv = requestAnimationFrame(() => {
      mv = null
      const x = ev.clientX, y = ev.clientY
      const hit = S.edits.find((e) => e.state !== 'dismissed' && (e.rects || []).some((r) => x >= r.left - 1 && x <= r.left + r.width + 1 && y >= r.top && y <= r.top + r.height + 3))
      if (hit) { hideAt = 0; if (!tip || tip.edit !== hit) showCard(hit, false); return }
      if (tip && !tip.auto && !tipHover) { hideAt = hideAt || Date.now() + 500; if (Date.now() > hideAt) { hideTip(); hideAt = 0 } } else hideAt = 0
    })
  }, true)

  // scroll: the edit in the middle of the screen gets its card, automatically
  let autoTimer = null
  const autoSoon = (ms) => { clearTimeout(autoTimer); autoTimer = setTimeout(autoCheck, ms || 260) }
  function autoCheck() {
    if (!S.auto || dead || (tip && !tip.auto && tipHover)) return
    const mid = innerHeight * 0.5
    let best = null, bestD = 1e9
    for (const e of S.edits) {
      if (e.state !== 'open' || S.seen.has(e.id) || !visible(e)) continue
      const r = e.rects[0]
      const d = Math.abs(r.top + r.height / 2 - mid)
      if (r.top > 70 && r.top + r.height < innerHeight - 30 && d < bestD) { best = e; bestD = d }
    }
    if (best) { S.seen.add(best.id); showCard(best, true) }
  }
  addEventListener('scroll', () => { relayout(); autoSoon() }, true)
  addEventListener('resize', relayout)
  addEventListener('input', (ev) => { if (ev.target && ev.target.name && /^Q\[/.test(ev.target.name)) relayout() }, true)
  every(relayout, 400)

  // ------------------------------------------------------------------------------------------------ the side panel
  function focusEdit(e) {
    const row = rowOf(e.qid)
    if (!row) return
    row.ta.scrollIntoView({ block: 'center', behavior: 'instant' })
    flashId = e.id
    setTimeout(() => { flashId = null; relayout() }, 1400)
    setTimeout(() => { relayout(); const cur = S.edits.find((x) => x.id === e.id); if (cur && visible(cur)) { S.seen.add(cur.id); showCard(cur, false) } }, 120)
  }
  function renderPanel() {
    const i = S.info
    subtitle.textContent = S.session ? `${S.session.name || ''}` : ''
    const parts = []
    if (S.error) parts.push(card('err', el('b', {}, 'Could not check this page. '), S.error))
    if (i) {
      parts.push(card(i.company ? 'info' : 'warn', el('h3', {}, i.company ? `Client: ${i.company.client}` : 'Client not recognised'),
        el('div', { class: 'small' }, i.company ? `Recognised from the page header. ${i.rules_used} grammar rule${i.rules_used === 1 ? '' : 's'} apply (${i.company_rules} of this client).`
          : `Only the general grammar rules were used (${i.rules_used}). The page header names no client that is set up in the platform.`),
        i.pending_rules ? el('div', { class: 'small', style: 'margin-top:4px' }, `${i.pending_rules} checklist rule${i.pending_rules === 1 ? ' is' : 's are'} still waiting for the Super Admin's approval; they are used for editing, not for QC.`) : null))
      if (i.message) parts.push(card('warn', i.message))
      parts.push(...guidelineCards())
      const open = S.edits.filter((e) => e.state !== 'dismissed')
      parts.push(el('div', { class: 'small muted', style: 'margin-bottom:6px' }, `${S.rows.length} answers checked · ${open.length} edit${open.length === 1 ? '' : 's'} · ${i.engine === 'ai+rules' ? 'AI + rules' : 'rules only'}. Accept writes the fix into the box; nothing is saved on the website until you save.`))
      let last = null
      for (const e of S.edits) {
        const row = rowOf(e.qid)
        if (e.qid !== last) { last = e.qid; parts.push(el('div', { class: 'q' }, (row && row.question) || 'Answer')) }
        const s = el('div', { class: 'sug ' + groupOf(e) + (e.state === 'dismissed' ? ' off' : '') },
          el('div', {}, el('span', { class: 'cat chip ' + (e.source === 'ai' ? 'ai' : 'rules') }, e.source === 'ai' ? 'AI' : 'Rules'), el('b', {}, labelOf(e))),
          el('div', {}, el('span', { class: 'del' }, e.original), ' → ', el('span', { class: 'add' }, e.state === 'accepted' ? e.final : e.replacement)),
          el('div', { class: 'small muted' }, (e.state === 'accepted' ? '✓ accepted · ' : e.state === 'dismissed' ? 'dismissed · ' : '') + (e.reason || '')))
        s.addEventListener('click', () => focusEdit(e))
        parts.push(s)
      }
      if (!S.edits.length) parts.push(card('ok', el('b', {}, 'No edits needed.'), el('div', { class: 'small' }, 'Nothing to fix in the answers on this page.')))
    }
    set(bodyEl, noteCard(), ...parts)
    renderFoot()
  }

  /** What the extension did with the guideline files of this page, in plain words. */
  function guidelineCards() {
    if (!S.gl) return []
    if (!S.gl.length) return [card('', el('div', { class: 'small muted' }, 'No guideline file is linked on this page, so only the client’s existing rules apply.'))]
    return S.gl.map((g) => {
      const fine = g.status === 'learned' ? el('div', {}, el('b', {}, `Learned ${g.rules} editing rule${g.rules === 1 ? '' : 's'} from “${g.name}”. `),
        el('span', { class: 'small' }, g.newProject ? 'This project was new: ' : '', 'they are used for editing now and count in QC after approval.'),
        el('ul', { class: 'small', style: 'margin:4px 0 0 16px;padding:0' }, ...g.learned.map((r) => el('li', {}, r.name))))
        : g.status === 'known' ? el('div', { class: 'small' }, `Guidelines “${g.name}”: already known to the platform (${g.rules} rule${g.rules === 1 ? '' : 's'}).`, g.newProject ? ' This project was added.' : '')
        : g.status === 'no_rules' ? el('div', { class: 'small' }, `Guidelines “${g.name}” were read: no editing rules in them.`)
        : null
      if (fine) return card(g.status === 'learned' ? 'ok' : 'info', fine)
      return card('warn', el('div', { class: 'small' }, `Guidelines “${g.name}”: `, g.message || (g.status === 'no_client' ? 'this client is not set up in the platform.' : 'could not be learned.')))
    })
  }

  // ------------------------------------------------------------------------------------------------ save on the website + download its PDF
  const FLAG = 'msai_download'
  const editedRows = () => S.rows.filter((x) => document.contains(x.ta) && x.ta.value !== S.orig.get(x.id))
  const noteCard = () => (S.note ? card(S.note.kind, el('div', { class: 'small' }, S.note.busy ? el('span', { class: 'spin' }) : null, S.note.text)) : null)
  function jobInfo() {
    const btn = [...document.querySelectorAll('input[type=button]')].find((b) => /companyView\(/.test(b.getAttribute('onclick') || ''))
    const m = btn && (btn.getAttribute('onclick') || '').match(/companyView\(\s*(\d+)\s*,\s*['"]([^'"]+)['"]/)
    if (!m) return null
    const h = document.querySelector('.white16bold')
    return { job: m[1], access: m[2], name: ((h ? h.textContent : '') || document.title).replace(/\s+/g, ' ').trim() }
  }
  /** The website's own PDF of the form (the "PDF" button of its Company View); it shows what is saved on the website. */
  async function downloadPdf(j) {
    const url = `${location.origin}/clients/ClientShopView.php?JobID=${encodeURIComponent(j.job)}&access=${encodeURIComponent(j.access)}&genpdf=1&mode=print&qset=`
    const r = await fetch(url, { credentials: 'include' })
    if (!r.ok || !/pdf/i.test(r.headers.get('content-type') || '')) throw new Error(`The website did not return a PDF (status ${r.status}).`)
    const blob = await r.blob()
    const name = ((j.name || 'Shop form').replace(/[\\/:*?"<>|]+/g, '').replace(/\s+/g, ' ').trim().slice(0, 80) || 'Shop form') + ' - ' + j.job + '.pdf'
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob); a.download = name
    document.body.append(a); a.click(); a.remove()
    setTimeout(() => URL.revokeObjectURL(a.href), 30000)
    return name
  }
  function saveAndDownload() {
    const j = jobInfo()
    const form = document.getElementById('JobSurveyForm') || document.querySelector('form[name="SurveyForm"]')
    const btn = form && [...form.querySelectorAll('input[type=submit],button[type=submit]')].find((b) => b.name === 'submitButtonName' || /update survey/i.test(b.value || b.textContent || ''))
    if (!j || !btn) { S.note = { kind: 'err', text: 'This page has no UPDATE SURVEY button or no Company View link, so it cannot be saved from here.' }; S.confirm = false; return renderPanel() }
    try { sessionStorage.setItem(FLAG, JSON.stringify({ ...j, ts: Date.now() })) } catch (_) { S.note = { kind: 'err', text: 'The browser blocked temporary storage, so the download cannot follow the save. Press UPDATE SURVEY yourself, then “Download PDF”.' }; S.confirm = false; return renderPanel() }
    S.confirm = false
    S.note = { kind: 'info', busy: true, text: 'Pressing the website’s UPDATE SURVEY … the PDF downloads when the page has reloaded.' }
    renderPanel()
    setTimeout(() => {                                                               // still here: the page did not reload (the website refused or asked something)
      if (dead) return
      try { sessionStorage.removeItem(FLAG) } catch (_) { /* nothing to remove */ }
      S.note = { kind: 'warn', text: 'The website did not reload after UPDATE SURVEY, so nothing was downloaded. Check the page for a message (a required answer, a warning) and try again.' }
      renderPanel()
    }, 20000)
    btn.click()
  }
  async function downloadOnly() {
    const j = jobInfo()
    if (!j) { S.note = { kind: 'err', text: 'No Company View link on this page.' }; return renderPanel() }
    S.note = { kind: 'info', busy: true, text: 'Downloading the PDF from the website …' }; renderPanel()
    try { S.note = { kind: 'ok', text: 'Downloaded from the website: ' + await downloadPdf(j) + ' (it shows what is saved on the website).' } } catch (e) { S.note = { kind: 'err', text: e.message || String(e) } }
    renderPanel()
  }
  /** After UPDATE SURVEY the website reloads: this runs on the new page and downloads the PDF of the saved form. */
  async function finishPending() {
    let f = null
    try { f = JSON.parse(sessionStorage.getItem(FLAG) || 'null') } catch (_) { f = null }
    if (!f) return
    if (Date.now() - f.ts > 180000) { try { sessionStorage.removeItem(FLAG) } catch (_) { /* expired */ } return }
    S.note = { kind: 'info', busy: true, text: 'Saved on the website. Downloading its PDF …' }
    setPanel(true); renderFoot(); set(bodyEl, noteCard())
    await new Promise((r) => setTimeout(r, 1200))
    try {
      const name = await downloadPdf(f)
      try { sessionStorage.removeItem(FLAG) } catch (_) { /* fine */ }
      S.note = { kind: 'ok', text: 'The website reloaded after UPDATE SURVEY and its PDF was downloaded: ' + name }
    } catch (e) { S.note = { kind: 'err', text: (e.message || String(e)) + ' Use “Download PDF” in this panel.' } }
    S.checked ? renderPanel() : set(bodyEl, noteCard())
  }
  function renderFoot() {
    const n = editedRows().length
    const again = el('button', {}, 'Check again')
    again.addEventListener('click', () => { S.dismissed.clear(); S.confirm = false; check() })
    const dl = el('button', {}, 'Download PDF')
    dl.title = 'The website’s own PDF of this form, as it is saved on the website'
    dl.addEventListener('click', downloadOnly)
    const parts = []
    if (n && S.confirm) {
      const yes = el('button', { class: 'primary' }, 'Yes, save & download')
      const no = el('button', {}, 'Cancel')
      yes.addEventListener('click', saveAndDownload)
      no.addEventListener('click', () => { S.confirm = false; renderFoot() })
      parts.push(card('warn', el('b', {}, `Save ${n} edited answer${n === 1 ? '' : 's'} on the website?`),
        el('div', { class: 'small' }, 'This presses the website’s own UPDATE SURVEY button, so the answers are saved in this shop’s record. Then its PDF is downloaded.'), el('div', { class: 'acts' }, yes, no)))
    } else if (n) {
      const save = el('button', { class: 'primary' }, `Save to site & download PDF (${n} edited)`)
      save.addEventListener('click', () => { S.confirm = true; renderFoot() })
      parts.push(save)
    }
    set(footEl, el('div', { class: 'small muted' }, 'Answers are sent to your platform server (and its AI model) to be checked. Accept writes into the boxes here; the website changes only when you save. Version ' + version + '.'),
      ...parts, el('div', { class: 'acts', style: 'margin:0' }, again, dl))
  }
  addEventListener('input', (ev) => { if (ev.target && ev.target.name && /^Q\[/.test(ev.target.name) && !S.confirm) renderFoot() }, true)

  // ------------------------------------------------------------------------------------------------ start
  async function start() {
    const s = await send({ type: 'session' })
    S.session = s && s.user
    if (!S.session) { document.body.append(host); return signIn() }
    document.body.append(host)
    setBadge(null)
    if (!S.gl) {
      set(bodyEl, card('', el('span', { class: 'spin' }), 'Looking for this client’s guidelines on the website …'))
      setPanel(true)
      await learnGuidelines()
      renderPanel()
    }
    check()
  }
  // The badge is always there on Intelli-Shop pages (dimmed when this page has no answer boxes), so it is clear the extension runs; the panel says what is missing.
  const version = (() => { try { return chrome.runtime.getManifest().version } catch (_) { return '?' } })()
  const idleNote = () => {
    set(bodyEl, card('info', el('h3', {}, 'No answer boxes on this page'), el('div', { class: 'small' }, 'The extension is running (version ' + version + ') but found no text boxes under questions here. Open a shop Review page (ShopReview-Right.php?JobID=…) and it checks the answers automatically.')))
    set(footEl, el('div', { class: 'small muted' }, 'Mystery Shopping AI · version ' + version + ' · ' + location.host))
  }
  const mount = () => { if (!host.isConnected && document.body) document.body.append(host) }
  mount(); badge.style.opacity = '.55'; setBadge(0); badgeN.textContent = '–'; idleNote()
  let waited = 0
  const wait = setInterval(() => {
    if (!alive()) return clearInterval(wait)
    mount()
    if (answerBoxes().length) { clearInterval(wait); badge.style.opacity = ''; start() } else if (++waited > 40) clearInterval(wait)        // wait for the answer boxes (about a minute)
  }, 1500)
  chrome.runtime.onMessage.addListener((msg) => { if (msg && msg.type === 'open-panel') setPanel(true) })
  finishPending()
})()
