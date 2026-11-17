/* Grammarly-style editor for RAW forms in Google Drive.
   - Coloured underlines are drawn on the page where the editor wants a change. While you scroll, a card pops up for the edit in the middle of the
     screen (hover or click any underline for its card): Accept / Dismiss, your own wording, and a comment box for the AI.
   - A round badge (bottom-right) shows how many suggestions are open; it opens the side panel: the project the AI recognised, the checklist and
     grammar rules in use (a leader can add rules), every suggestion, edits the AI missed, and the hand-over.
   - Editors cross-check and send the result to the leader; the leader confirms, which creates the edited PDF and starts QC (or sends it back).
   Everything that comes from a PDF is inserted with textContent, never as HTML. */
(function () {
  if (window.__msaiPanel) return
  window.__msaiPanel = true
  const D = window.MSAI_DETECT
  let dead = false
  const timers = []
  const alive = () => { try { return !!(chrome.runtime && chrome.runtime.id) } catch (e) { return false } }
  /** Every timer of this copy runs through here: once the extension was reloaded the old copy stops quietly instead of throwing errors. */
  const every = (fn, ms) => { timers.push(setInterval(() => { if (!alive()) return gone(); fn() }, ms)) }
  /** After the extension was reloaded, this tab's old copy cannot reach it any more: stop, and say so once with the one thing to do. */
  function gone() {
    if (dead) return
    dead = true
    timers.forEach(clearInterval)
    try { set(bodyEl, card('warn', el('h3', {}, 'Extension updated'), el('div', { class: 'small' }, 'This tab still has the old version. Refresh this Drive tab (F5) and the panel comes back.'))); set(footEl); setPanel(true) } catch (_) { /* page is going away */ }
  }
  const send = async (msg) => {
    if (dead || !alive()) { gone(); return { ok: false, error: 'The extension was updated. Refresh this Drive tab (F5).' } }
    try { return await chrome.runtime.sendMessage(msg) } catch (e) {
      if (/context invalidated|receiving end does not exist/i.test(String((e && e.message) || e))) gone()
      return { ok: false, error: 'The extension was updated. Refresh this Drive tab (F5).' }
    }
  }
  const api = (method, path, body, as) => send({ type: 'api', method, path, body, as })

  const GREEN = '#15a37f'
  const CSS = `
  :host { all: initial; }
  * { box-sizing: border-box; }
  .wrap { position: fixed; top: 0; right: 0; height: 100vh; width: 380px; z-index: 2147483000; display: flex; flex-direction: column;
    font: 13px/1.45 system-ui, 'Segoe UI', Arial, sans-serif; color: #1f2937; background: #fff; border-left: 1px solid #e5e7eb; box-shadow: -6px 0 24px rgba(15,23,42,.12); }
  .wrap.hidden { display: none; }
  header { display: flex; align-items: center; gap: 4px; padding: 12px 10px 10px 12px; border-bottom: 1px solid #eef0f3; }
  .logo { width: 24px; height: 24px; border-radius: 50%; background: ${GREEN}; color: #fff; display: grid; place-items: center; font-weight: 800; font-size: 13px; flex: none; margin-right: 4px; }
  .title { font-weight: 700; flex: 1; min-width: 0; } .title span { display: block; font-weight: 500; font-size: 11px; color: #6b7280; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  button { font: inherit; cursor: pointer; border-radius: 8px; border: 1px solid #d1d5db; background: #fff; color: #374151; padding: 6px 10px; }
  button:hover { background: #f3f4f6; } button:disabled { opacity: .5; cursor: default; }
  button.primary { background: ${GREEN}; border: 0; color: #fff; font-weight: 700; padding: 9px 12px; border-radius: 999px; }
  button.primary:hover { background: #0f8a69; }
  button.icon { padding: 2px 7px; border-color: transparent; background: transparent; color: #6b7280; font-size: 15px; }
  button.icon:hover { background: #f3f4f6; } button.icon.on { color: ${GREEN}; }
  .body { flex: 1; overflow: auto; padding: 12px; background: #f7f8fa; }
  .foot { padding: 10px 12px; border-top: 1px solid #eef0f3; display: grid; gap: 8px; background: #fff; }
  .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 10px 12px; margin-bottom: 10px; }
  .muted { color: #6b7280; } .small { font-size: 12px; } .errtext { color: #b42318; font-size: 12px; min-height: 1em; }
  .warn { background: #fff8e6; border-color: #f2d27a; } .err { background: #fdecec; border-color: #f2a5a5; } .ok { background: #e9f8f2; border-color: #9edbc4; } .info { background: #eef4ff; border-color: #b9cdf7; }
  .chip { display: inline-block; font-size: 11px; padding: 1px 8px; border-radius: 999px; background: #eef2f7; color: #475569; margin-right: 4px; }
  .chip.ai { background: #ede9fe; color: #5b3fc4; } .chip.draft { background: #fff1d6; color: #8a5a00; } .chip.human { background: #dff3ea; color: #0b6b4e; }
  h3 { margin: 0 0 6px; font-size: 13px; }
  details summary { cursor: pointer; color: #475569; }
  .summary { display: flex; align-items: center; gap: 12px; margin: 4px 0 10px; }
  .ring { width: 54px; height: 54px; border-radius: 50%; display: grid; place-items: center; font-size: 20px; font-weight: 800; color: #1f2937; border: 5px solid ${GREEN}; background: #fff; flex: none; }
  .ring.zero { border-color: #9edbc4; color: ${GREEN}; }
  .tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; }
  .tab { border-radius: 999px; padding: 3px 10px; font-size: 12px; background: #fff; }
  .tab.on { background: #1f2937; color: #fff; border-color: #1f2937; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: 0; }
  .dot.red, .ul.red { --c: #e5484d; } .dot.blue, .ul.blue { --c: #2f6fed; } .dot.purple, .ul.purple { --c: #8b5cf6; }
  .dot { background: var(--c); }
  .sug { background: #fff; border: 1px solid #e5e7eb; border-left: 4px solid var(--c); border-radius: 10px; padding: 9px 11px; margin-bottom: 8px; cursor: pointer; }
  .sug.red { --c: #e5484d; } .sug.blue { --c: #2f6fed; } .sug.purple { --c: #8b5cf6; }
  .sug:hover { box-shadow: 0 2px 10px rgba(15,23,42,.10); }
  .sug.off { --c: #cbd5e1; opacity: .75; }
  .sug.flash { box-shadow: 0 0 0 2px var(--c); }
  .sug .cat, .pop .cat { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; color: #64748b; }
  .sug .change { font-size: 14px; margin: 3px 0; }
  .del { color: #b42318; text-decoration: line-through; } .add { color: #067a55; font-weight: 700; }
  .sug .reason { font-size: 12px; color: #6b7280; }
  .acts { display: flex; gap: 6px; margin-top: 7px; align-items: center; flex-wrap: wrap; }
  .acts button { padding: 3px 12px; border-radius: 999px; font-size: 12px; }
  .state { font-size: 11px; margin-left: auto; color: #6b7280; }
  .ctl { margin-top: 8px; padding-top: 8px; border-top: 1px dashed #e2e8f0; display: grid; gap: 6px; }
  .ctl label { margin: 0; font-size: 11px; font-weight: 600; color: #64748b; }
  .ctl .row { display: flex; gap: 6px; } .ctl .row input { flex: 1; }
  .ctl textarea { min-height: 46px; }
  .said { font-size: 12px; background: #f4f6f8; border-radius: 8px; padding: 5px 8px; }
  .preview { position: relative; margin-top: 8px; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; background: #fff; }
  .preview img { display: block; width: 100%; }
  .hl { position: absolute; border: 2px solid #e5484d; background: rgba(229,72,77,.18); border-radius: 3px; }
  textarea, input, select { width: 100%; font: inherit; color: inherit; background: #fff; border: 1px solid #d1d5db; border-radius: 8px; padding: 7px 8px; }
  input[type=checkbox] { width: auto; padding: 0; flex: none; }
  textarea { min-height: 62px; resize: vertical; }
  label { display: block; margin: 8px 0 3px; font-weight: 600; }
  .spin { display: inline-block; width: 12px; height: 12px; border: 2px solid ${GREEN}; border-right-color: transparent; border-radius: 50%; animation: s .8s linear infinite; vertical-align: -2px; margin-right: 6px; }
  @keyframes s { to { transform: rotate(360deg); } }
  ul { margin: 4px 0 0 16px; padding: 0; }
  a { color: #0f8a69; }
  .rule { padding: 5px 0; border-bottom: 1px solid #f0f2f5; } .rule:last-child { border-bottom: 0; }
  .badge { position: fixed; right: 18px; bottom: 22px; z-index: 2147483001; width: 46px; height: 46px; border-radius: 50%; border: 0; padding: 0; background: ${GREEN}; color: #fff;
    font-weight: 800; font-size: 20px; box-shadow: 0 4px 14px rgba(15,23,42,.35); display: grid; place-items: center; }
  .badge:hover { background: #0f8a69; } .badge.dim { opacity: .55; } .badge.dim:hover { opacity: 1; } .badge.panel-open { right: 396px; }
  .badge .n { position: absolute; top: -4px; right: -4px; min-width: 20px; height: 20px; padding: 0 5px; border-radius: 999px; background: #e5484d; color: #fff; font-size: 11px; font-weight: 700; display: grid; place-items: center; border: 2px solid #fff; }
  .badge .n.zero { background: #15a37f; }
  .layer { position: fixed; inset: 0; pointer-events: none; z-index: 2147482000; }
  .ul { position: fixed; pointer-events: auto; cursor: pointer; border-bottom: 3px solid var(--c); background: color-mix(in srgb, var(--c) 10%, transparent); border-radius: 2px 2px 0 0; }
  .ul:hover, .ul.flash { background: color-mix(in srgb, var(--c) 28%, transparent); }
  .ul.off { --c: #cbd5e1; border-bottom-style: dashed; background: transparent; }
  .pop { position: fixed; z-index: 2147483002; width: 330px; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px 14px;
    box-shadow: 0 10px 30px rgba(15,23,42,.28); color: #1f2937; font: 13px/1.45 system-ui, 'Segoe UI', Arial, sans-serif; }
  .pop .top { display: flex; align-items: center; } .pop .top .x { margin-left: auto; padding: 0 6px; border: 0; background: transparent; color: #6b7280; font-size: 15px; }
  .pop .change { font-size: 16px; margin: 6px 0 4px; }
  .pop .reason { font-size: 12px; color: #6b7280; margin-bottom: 6px; }
  `

  const host = document.createElement('div')
  host.id = 'msai-host'
  const root = host.attachShadow({ mode: 'open' })
  const style = document.createElement('style')
  style.textContent = CSS
  root.appendChild(style)
  const wrap = el('div', { class: 'wrap' })
  const bodyEl = el('div', { class: 'body' })
  const footEl = el('div', { class: 'foot' })
  const subtitle = el('span', {}, '')
  const layer = el('div', { class: 'layer' })
  const badgeN = el('span', { class: 'n zero' }, '0')
  const badge = el('button', { class: 'badge', title: 'Mystery Shopping AI' }, 'M', badgeN)
  let tip = null, tipTimer = null

  function setPanel(openIt) {
    wrap.classList.toggle('hidden', !openIt)
    badge.classList.toggle('panel-open', openIt)
  }
  badge.addEventListener('click', () => {
    if (S.hideMarks) { S.hideMarks = false; badge.classList.remove('dim'); setPanel(true); layout(); return }   // brought back after ✕
    setPanel(wrap.classList.contains('hidden'))
  })
  const queueBtn = el('button', { class: 'icon', title: 'Leader queue: forms waiting for your confirmation', style: 'display:none' }, '☰')
  queueBtn.addEventListener('click', () => showQueue())
  const diagBtn = el('button', { class: 'icon', title: 'Check: what the extension finds on this Drive page' }, '🔧')
  diagBtn.addEventListener('click', () => showDiagnostics())
  const autoBtn = el('button', { class: 'icon on', title: 'Pop-up cards while you scroll: on' }, '⚡')
  autoBtn.addEventListener('click', () => {
    S.auto = !S.auto
    autoBtn.classList.toggle('on', S.auto)
    autoBtn.title = 'Pop-up cards while you scroll: ' + (S.auto ? 'on' : 'off')
    if (!S.auto) hideTip(); else autoSoon(100)
  })
  const minBtn = el('button', { class: 'icon', title: 'Collapse to the badge' }, '⟩')
  minBtn.addEventListener('click', () => setPanel(false))
  const closeBtn = el('button', { class: 'icon', title: 'Hide (click the round badge to bring it back)' }, '✕')
  closeBtn.addEventListener('click', () => { hideTip(); S.hideMarks = true; setPanel(false); badge.classList.add('dim'); layout() })
  const gear = el('button', { class: 'icon', title: 'Settings' }, '⚙')
  gear.addEventListener('click', () => send({ type: 'options' }))
  wrap.append(el('header', {}, el('div', { class: 'logo' }, 'M'), el('div', { class: 'title' }, 'Mystery Shopping AI', subtitle), queueBtn, diagBtn, autoBtn, gear, minBtn, closeBtn), bodyEl, footEl)
  root.append(layer, wrap, badge)

  function el(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag)
    for (const [k, v] of Object.entries(attrs)) { if (v !== undefined && v !== null) n.setAttribute(k, v) }
    for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k.nodeType ? k : document.createTextNode(String(k)))
    return n
  }
  const set = (node, ...kids) => { node.replaceChildren(...kids.flat().filter(Boolean)) }

  // ------------------------------------------------------------------------------------------------ state
  const S = {
    file: null, info: null, prop: null, edits: [], feedback: '', rounds: 0, result: null, previews: {}, open: null, session: null, caps: {},
    marks: false, hideMarks: false, filter: 'all', flash: null, auto: true, seen: new Set(), comments: {}, drafts: {}, expanded: new Set(), reviewMode: false,
    note: '', newRule: null,
  }

  const GROUP = { spelling: 'red', grammar: 'red', sentence: 'red', profanity: 'red', punctuation: 'blue', quotation: 'blue', symbols: 'blue', spacing: 'blue', capitalisation: 'blue', capitalization: 'blue', capital: 'blue' }
  const LABEL = { spelling: 'Spelling', grammar: 'Grammar', sentence: 'Sentence', quotation: 'Quotation marks', symbols: 'Symbols', capitalization: 'Capital letters', spacing: 'Spacing', punctuation: 'Punctuation', tense: 'Past tense', terminology: 'Terminology', profanity: 'Language', capitalisation: 'Capital letters' }
  const groupOf = (e) => GROUP[(e.category || '').toLowerCase()] || 'purple'
  const labelOf = (e) => LABEL[(e.category || '').toLowerCase()] || (e.category ? e.category[0].toUpperCase() + e.category.slice(1) : 'Edit')
  const GROUP_NAME = { red: 'Correctness', blue: 'Punctuation & spacing', purple: 'Company rules & style' }
  const RULE_TYPES = [['terminology', 'Terminology / words'], ['punctuation', 'Punctuation'], ['past_tense', 'Past tense'], ['spelling', 'Spelling'], ['extra_space', 'Extra space / no space'], ['profanity', 'Language']]
  const canAct = () => !S.reviewMode || S.caps.can_confirm            // an editor cannot change what is waiting for the leader

  function card(cls, ...kids) { return el('div', { class: 'card ' + (cls || '') }, ...kids) }
  function busy(text) { S.marks = false; layout(); setBadge(); set(bodyEl, card('', el('span', { class: 'spin' }), text)); set(footEl) }
  function fail(text, retry) {
    S.marks = false; layout(); setPanel(true)
    set(bodyEl, card('err', text), retry ? el('button', {}, 'Try again') : null)
    if (retry) bodyEl.lastChild.addEventListener('click', retry)
    set(footEl)
  }
  function setBadge() {
    const n = S.marks ? S.edits.filter((e) => e.accepted).length : null
    badgeN.textContent = n === null ? '…' : String(n)
    badgeN.classList.toggle('zero', n === 0)
  }
  const expired = () => signIn('Your session expired. Please sign in again.')

  // ------------------------------------------------------------------------------------------------ sign in
  function signIn(message) {
    S.marks = false; layout(); setPanel(true)
    const email = el('input', { type: 'email', placeholder: 'you@isngs.com', autocomplete: 'username' })
    const pass = el('input', { type: 'password', placeholder: 'Password', autocomplete: 'current-password' })
    const err = el('div', { class: 'errtext', style: 'margin-top:6px' }, message || '')
    const go = el('button', { class: 'primary' }, 'Sign in')
    const doLogin = async () => {
      go.disabled = true
      const r = await send({ type: 'login', email: email.value.trim(), password: pass.value })
      if (r.ok) { S.session = r.data.user; start() } else { err.textContent = r.error || 'Sign-in failed.'; go.disabled = false }
    }
    go.addEventListener('click', doLogin)
    pass.addEventListener('keydown', (e) => e.key === 'Enter' && doLogin())
    set(bodyEl, card('', el('h3', {}, 'Sign in'), el('div', { class: 'small muted' }, 'Use your Mystery Shopping AI account (the same e-mail and password as the website).'),
      el('label', {}, 'E-mail'), email, el('label', {}, 'Password'), pass, err, el('div', { style: 'margin-top:8px' }, go)))
    set(footEl)
  }

  // ------------------------------------------------------------------------------------------------ main flow
  async function start() {
    const f = S.file
    subtitle.textContent = f.name || ''
    busy('Identifying the company of this form…')
    const sess = await send({ type: 'session' })
    if (!sess.user) return signIn()
    S.session = sess.user
    const r = await api('POST', '/api/ext/identify', { drive_id: f.driveId || null, filename: f.name })
    if (r.status === 401) return expired()
    if (!r.ok) return fail(r.error, start)
    S.info = r.data
    S.caps = { can_confirm: !!r.data.can_confirm, can_add_rules: !!r.data.can_add_rules }
    queueBtn.style.display = S.caps.can_confirm ? '' : 'none'
    if (!S.info.found) return notFound(S.info)
    if (!S.info.is_raw) return setPanel(true), set(bodyEl, card('warn', el('h3', {}, 'This is an edited file (_2)'), el('div', { class: 'small' }, 'The extension edits RAW forms (_1). Open the RAW file of this form.')))
    if (S.info.review) {                                                      // edits already cross-checked and handed over (or sent back): show them
      const o = await api('GET', `/api/ext/forms/${S.info.form.id}/proposal`)
      if (o.ok && o.data.proposal) return loadProposal(o.data.proposal)
    }
    await propose()
  }

  function notFound(info) {
    const kids = [el('h3', {}, info.ambiguous ? 'Form exists in several projects' : 'Form not in the platform yet'), el('div', { class: 'small' }, info.message || '')]
    if (info.company_guess) kids.push(el('div', { class: 'small muted', style: 'margin-top:6px' }, `Drive folder: ${info.company_guess.client || '?'} / ${info.company_guess.project}`))
    if (info.candidates) kids.push(el('ul', {}, info.candidates.map((c) => el('li', {}, c.path))))
    const again = el('button', { style: 'margin-top:8px' }, 'Check again')
    again.addEventListener('click', start)
    setPanel(true)
    set(bodyEl, card('warn', ...kids, again))
    set(footEl)
  }

  /** Turn a server proposal into the panel state. `old` keeps the human's decisions when the AI ran again. */
  function adopt(p, old) {
    const was = new Map((old || []).map((e) => [e.id, e]))
    const rv = p.review || {}
    const accepted = rv.accepted ? new Set(rv.accepted) : null
    S.prop = p
    S.rounds = p.round
    S.comments = p.comments || {}
    S.edits = p.edits.map((e) => {
      const o = was.get(e.id)
      const changed = rv.changes && rv.changes[e.id]
      return { ...e, orig: e.replacement, replacement: changed || e.replacement, accepted: o ? o.accepted : accepted ? accepted.has(e.id) : true }
    })
    S.previews = {}
    S.open = null
    S.filter = 'all'
    S.seen = new Set()
    S.reviewMode = p.status === 'awaiting_leader'
    S.marks = true
  }

  function loadProposal(p) {
    adopt(p)
    render()
    autoSoon(900)
  }

  async function propose(feedback, rejected, clientId) {
    busy(S.rounds ? 'AI is re-editing…' : `Applying the checklist and grammar rules…`)
    const r = await api('POST', `/api/ext/forms/${S.info.form.id}/propose`, {
      feedback: feedback || null, rejected: rejected || [], proposal_id: S.prop ? S.prop.proposal_id : null, client_id: clientId || (S.prop ? S.prop.client_id : null),
      item_feedback: [] })
    if (r.status === 401) return expired()
    if (!r.ok) return fail(r.error, () => propose(feedback, rejected, clientId))
    adopt(r.data, S.edits)
    render()
    autoSoon(900)
  }

  // ------------------------------------------------------------------------------------------------ the underlines on the page
  /** The page images / canvases of the Drive viewer, top to bottom: page n of the PDF is the n-th big one. */
  function pageEls() {
    const pages = [...document.querySelectorAll('img,canvas')].filter((n) => {
      if (host.contains(n)) return false
      const r = n.getBoundingClientRect()
      return r.width >= 300 && r.height >= 300
    })
    pages.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top || a.getBoundingClientRect().left - b.getBoundingClientRect().left)
    return pages
  }

  const marks = new Map() // key -> element (element._e = the edit)
  function layout() {
    if (!S.marks || !S.prop || S.hideMarks) { layer.replaceChildren(); marks.clear(); return }
    const pages = pageEls()
    const total = (S.prop.pages || []).length
    const usable = pages.length >= 1 && pages.length <= Math.max(total, 1) + 2   // do not guess on a page that shows something else
    const seen = new Set()
    if (usable) {
      for (const e of S.edits) {
        if (S.filter !== 'all' && groupOf(e) !== S.filter) continue
        const pg = (S.prop.pages || []).find((p) => p.page === e.page)
        const pe = pages[e.page - 1]
        if (!pg || !pe) continue
        const pr = pe.getBoundingClientRect()
        if (pr.bottom < 0 || pr.top > innerHeight) continue
        ;(e.raw_boxes || []).forEach((b, i) => {
          if (b.page && b.page !== e.page) return
          const p = D.boxToPercent(b, pg)
          if (!p) return
          const key = e.id + ':' + i
          seen.add(key)
          let n = marks.get(key)
          if (!n) {
            n = el('div', { class: 'ul' })
            n.addEventListener('mouseenter', () => showCard(n._e, n, false))
            n.addEventListener('mouseleave', () => hideTipSoon())
            n.addEventListener('click', () => showCard(n._e, n, false))
            marks.set(key, n); layer.append(n)
          }
          n._e = e
          n.className = `ul ${groupOf(e)}${e.accepted ? '' : ' off'}${S.flash === e.id ? ' flash' : ''}`
          n.style.left = `${pr.left + (p.left / 100) * pr.width}px`
          n.style.top = `${pr.top + (p.top / 100) * pr.height}px`
          n.style.width = `${(p.width / 100) * pr.width}px`
          n.style.height = `${(p.height / 100) * pr.height}px`
        })
      }
    }
    for (const [k, n] of marks) if (!seen.has(k)) { n.remove(); marks.delete(k) }
  }
  every(layout, 350)
  let autoTimer = null
  const autoSoon = (ms) => { clearTimeout(autoTimer); autoTimer = setTimeout(autoCheck, ms || 280) }
  addEventListener('scroll', () => { layout(); autoSoon() }, true)
  addEventListener('resize', layout)

  // ------------------------------------------------------------------------------------------------ cards on the page (hover / scroll)
  function hideTip() { clearTimeout(tipTimer); if (tip) { tip.remove(); tip = null } }
  function hideTipSoon() {
    clearTimeout(tipTimer)
    tipTimer = setTimeout(() => { if (tip && tip.dataset.auto !== '1' && tip.dataset.pinned !== '1' && !tip.matches(':hover')) hideTip() }, 450)
  }

  /** While scrolling: the edit nearest to the middle of the screen gets its card (once), so the page is edited top to bottom. */
  /** The page number Drive's own pager shows ("Page [3] / 5"), for when the page images cannot be found. */
  function pagerPage() {
    const total = S.prop && S.prop.pages ? S.prop.pages.length : 0
    if (!total) return null
    const rx = new RegExp('/\\s*' + total + '\\b')
    for (const inp of document.querySelectorAll('input')) {
      if (host.contains(inp)) continue
      const v = parseInt(inp.value, 10)
      if (!(v >= 1 && v <= total)) continue
      const box = inp.parentElement && inp.parentElement.parentElement
      if (box && rx.test(box.textContent || '')) return v
    }
    return null
  }
  const fixedAnchor = { getBoundingClientRect: () => ({ left: 56, top: 150, right: 66, bottom: 160, width: 10, height: 10 }) }
  let lastPager = null, lastFallback = 0
  every(() => { const p = S.marks && !marks.size ? pagerPage() : null; if (p && p !== lastPager) { lastPager = p; autoSoon(200) } }, 700)

  /** No page images found (Drive draws the viewer differently): follow the pager instead - one card for the next edit on the page you are on. */
  function autoFallback() {
    const pg = pagerPage()
    if (!pg || Date.now() - lastFallback < 2500) return
    const e = S.edits.find((x) => x.page === pg && !S.seen.has(x.id))
    if (!e) return
    lastFallback = Date.now()
    S.seen.add(e.id)
    showCard(e, fixedAnchor, true)
  }

  function autoCheck() {
    if (!S.auto || !S.marks || S.hideMarks || !canAct()) return
    if (tip && tip.dataset.pinned === '1') return                           // the human is typing in a card
    if (!marks.size) return autoFallback()
    let best = null, bestD = 1e9
    for (const n of marks.values()) {
      const r = n.getBoundingClientRect()
      const cy = r.top + r.height / 2
      if (cy < innerHeight * 0.2 || cy > innerHeight * 0.75 || S.seen.has(n._e.id)) continue
      const d = Math.abs(cy - innerHeight * 0.45)
      if (d < bestD) { best = n; bestD = d }
    }
    if (best) { S.seen.add(best._e.id); showCard(best._e, best, true) }
    else if (tip && tip.dataset.auto === '1' && tip._anchor) {                // scrolled away from the card's edit
      const r = tip._anchor.getBoundingClientRect()
      if (r.bottom < 0 || r.top > innerHeight) hideTip()
    }
  }

  /** Accept/Dismiss, own wording and the comment box for the AI. Used by the page card and by the side-panel list. */
  function controls(e, onChange, fromCard) {
    const box = el('div', { class: 'ctl' })
    const msg = el('div', { class: 'errtext' })
    const wording = el('input', { type: 'text', placeholder: 'Your own wording for this place' })
    wording.value = e.replacement
    const use = el('button', {}, 'Use my wording')
    use.addEventListener('click', () => {
      const v = wording.value.trim()
      if (!v) return
      e.replacement = v; e.source = 'human'; e.reason = 'Reworded by you'; e.accepted = true
      onChange()
    })
    const ta = el('textarea', { placeholder: 'Comment for the AI about this edit, e.g. “keep it in the past tense”' })
    ta.value = S.drafts[e.id] || ''
    ta.addEventListener('input', () => { S.drafts[e.id] = ta.value })
    const ask = el('button', { class: 'primary', style: 'padding:5px 14px;font-size:12px' }, 'Ask AI')
    ask.addEventListener('click', async () => {
      const c = ta.value.trim()
      if (c.length < 2) { msg.textContent = 'Write a comment for the AI first.'; return }
      ask.disabled = true; msg.style.color = '#6b7280'; msg.textContent = 'The AI is looking at this edit again…'
      const r = await api('POST', `/api/ext/forms/${S.info.form.id}/proposals/${S.prop.proposal_id}/ask`, { edit_id: e.id, comment: c })
      ask.disabled = false
      if (r.status === 401) return expired()
      if (!r.ok) { msg.style.color = '#b42318'; msg.textContent = r.error; return }
      Object.assign(e, r.data.edit, { accepted: e.accepted, orig: r.data.edit.replacement })
      S.comments[e.id] = r.data.comments
      S.drafts[e.id] = ''
      onChange()
    })
    const said = (S.comments[e.id] || []).map((c) => el('div', { class: 'said' }, el('b', {}, c.by + ': '), c.text))
    box.append(el('label', {}, 'Your wording'), el('div', { class: 'row' }, wording, use), el('label', {}, 'Comment for the AI'), ta, el('div', { class: 'acts', style: 'margin:0' }, ask), msg, ...said)
    if (fromCard) setTimeout(() => {}, 0)
    return box
  }

  function diffNodes(e) {
    return D.diffWords(e.original, e.replacement).map((w) => (w.t === 'same' ? document.createTextNode(w.w + ' ') : el('span', { class: w.t }, w.w + ' ')))
  }

  function showCard(e, anchor, auto) {
    clearTimeout(tipTimer)
    if (!canAct() && auto) return
    if (tip && tip.dataset.id === e.id) { if (!auto) tip.dataset.auto = tip.dataset.auto; return }
    if (tip && tip.dataset.pinned === '1') return
    hideTip()
    const redraw = () => { hideTip(); render(true); layout(); showCard(e, anchor, auto) }
    const toggle = el('button', { class: e.accepted ? '' : 'primary' }, e.accepted ? 'Dismiss' : 'Accept')
    toggle.addEventListener('click', () => { e.accepted = !e.accepted; hideTip(); render(true); layout() })
    const jump = el('button', {}, 'In list')
    jump.addEventListener('click', () => { hideTip(); setPanel(true); S.expanded.add(e.id); render(true); focusSuggestion(e) })
    const x = el('button', { class: 'x', title: 'Close' }, '✕')
    x.addEventListener('click', hideTip)
    tip = el('div', { class: 'pop' }, el('div', { class: 'top' }, el('span', { class: 'cat' }, el('span', { class: `dot ${groupOf(e)}` }), labelOf(e)), x),
      el('div', { class: 'change' }, diffNodes(e)),
      el('div', { class: 'reason' }, e.reason, e.source === 'ai' ? '  · AI' : e.source === 'human' ? '  · you' : '  · rules'),
      el('div', { class: 'acts', style: 'margin-top:0' }, toggle, jump, el('span', { class: 'state' }, e.accepted ? 'Will be applied' : 'Dismissed')),
      controls(e, redraw, true))
    tip.dataset.id = e.id
    tip.dataset.auto = auto ? '1' : '0'
    tip._anchor = anchor
    tip.addEventListener('mouseenter', () => clearTimeout(tipTimer))
    tip.addEventListener('mouseleave', hideTipSoon)
    tip.addEventListener('focusin', () => { tip.dataset.pinned = '1' })
    root.append(tip)
    const r = anchor.getBoundingClientRect()
    const w = 330
    const limit = innerWidth - (wrap.classList.contains('hidden') ? 0 : 380) - 12
    const left = Math.max(8, Math.min(limit - w, r.left))
    const h = tip.offsetHeight
    const below = r.bottom + 8 + h < innerHeight
    tip.style.left = left + 'px'
    tip.style.top = (below ? r.bottom + 8 : Math.max(8, r.top - 8 - h)) + 'px'
  }

  /** Scroll the page to this edit's place and flash it. */
  function focusSuggestion(e) {
    S.flash = e.id
    const pages = pageEls()
    const pe = pages[e.page - 1]
    const pg = (S.prop.pages || []).find((p) => p.page === e.page)
    if (pe && pg && e.raw_boxes && e.raw_boxes[0]) {
      pe.scrollIntoView({ block: 'start' })
      const p = D.boxToPercent(e.raw_boxes[0], pg)
      const sc = scrollParent(pe)
      const dy = pe.getBoundingClientRect().top + (p.top / 100) * pe.getBoundingClientRect().height - innerHeight * 0.35
      if (sc && sc !== document.scrollingElement) sc.scrollTop += dy
      else scrollBy(0, dy)
    } else togglePreview(e, true)
    layout()
    setTimeout(() => { S.flash = null; layout() }, 1600)
  }
  function scrollParent(n) {
    for (let p = n.parentElement; p; p = p.parentElement) { const o = getComputedStyle(p).overflowY; if ((o === 'auto' || o === 'scroll') && p.scrollHeight > p.clientHeight) return p }
    return document.scrollingElement
  }

  // ------------------------------------------------------------------------------------------------ side panel
  function companyName() {
    const cc = S.prop && S.prop.company_check
    if (cc && cc.client_id && S.prop.client_id === cc.client_id) return cc.company
    return S.info.company.client
  }

  function ruleLine(r) {
    return el('div', { class: 'rule' }, el('b', {}, r.name), ' ', el('span', { class: 'muted' }, `(${r.error}${r.company_specific ? ', company' : ''})`), ' ',
      r.status === 'draft' ? el('span', { class: 'chip draft', title: 'Used for editing now; counted in QC after the Super Admin approves it' }, 'draft') : null,
      r.added_by && r.status === 'draft' ? el('span', { class: 'muted small' }, ` by ${r.added_by}`) : null,
      el('div', { class: 'small muted' }, r.description))
  }

  function addRuleForm() {
    if (!S.caps.can_add_rules) return null
    const open = S.newRule
    if (!open) {
      const b = el('button', { style: 'margin-top:8px' }, '+ Add a grammar rule')
      b.addEventListener('click', () => { S.newRule = { name: '', description: '', type: 'terminology', scope: 'client' }; render(true) })
      return b
    }
    const name = el('input', { type: 'text', placeholder: 'Rule name, e.g. Say associate, not employee' })
    name.value = open.name
    name.addEventListener('input', () => { open.name = name.value })
    const desc = el('textarea', { placeholder: 'What must the editor do? e.g. “Write associate instead of employee in every comment.”' })
    desc.value = open.description
    desc.addEventListener('input', () => { open.description = desc.value })
    const type = el('select', {}, RULE_TYPES.map(([v, t]) => el('option', { value: v }, t)))
    type.value = open.type
    type.addEventListener('change', () => { open.type = type.value })
    const scope = el('select', {}, el('option', { value: 'client' }, `All ${companyName()} projects`), el('option', { value: 'project' }, `Only ${S.info.company.project}`))
    scope.value = open.scope
    scope.addEventListener('change', () => { open.scope = scope.value })
    const err = el('div', { class: 'errtext' })
    const save = el('button', { class: 'primary', style: 'padding:6px 14px' }, 'Save rule and re-edit')
    save.addEventListener('click', async () => {
      save.disabled = true
      const r = await api('POST', `/api/ext/forms/${S.info.form.id}/rules`, { name: open.name, description: open.description, error_subtype: open.type, scope: open.scope, client_id: S.prop.client_id })
      if (r.status === 401) return expired()
      if (!r.ok) { err.textContent = r.error; save.disabled = false; return }
      S.newRule = null
      S.prop.rules = [...(S.prop.rules || []), r.data]
      propose()                                                              // the new rule is used for editing at once
    })
    const cancel = el('button', {}, 'Cancel')
    cancel.addEventListener('click', () => { S.newRule = null; render(true) })
    return el('div', { class: 'ctl' }, el('label', {}, 'New grammar rule'), name, desc, type, scope, err,
      el('div', { class: 'small muted' }, 'It is used for editing right away and counted in QC after the Super Admin approves it.'), el('div', { class: 'acts' }, save, cancel))
  }

  function companyCard() {
    const i = S.info, p = S.prop
    const rules = p.rules || i.rules || []
    const cc = p.company_check
    const name = companyName()
    const drafts = rules.filter((r) => r.status === 'draft').length
    const head = el('summary', {}, el('b', {}, name), el('span', { class: 'muted' }, '  ' + (p.client_id === i.company.client_id ? i.company.project : 'switched by you')),
      el('div', { style: 'margin-top:4px' }, el('span', { class: 'chip' }, `${rules.filter((r) => r.company_specific).length} checklist / company rules`), el('span', { class: 'chip' }, `${rules.filter((r) => !r.company_specific).length} general`),
        drafts ? el('span', { class: 'chip draft' }, `${drafts} draft`) : null, el('span', { class: 'chip ' + (p.ai_status === 'completed' ? 'ai' : '') }, p.ai_status === 'completed' ? `AI: ${p.ai_model || 'model'}` : 'built-in rules only')))
    const kids = [el('div', { class: 'small muted', style: 'margin:6px 0' }, `Folder: ${i.company.path} · Form ${i.form.code}`), ...rules.map(ruleLine), addRuleForm()]
    const out = [card('', el('details', {}, head, ...kids))]
    if (cc && cc.company) {
      const sw = cc.suggest_switch && p.client_id !== cc.client_id
      const kidsC = [el('div', { class: 'small' }, `AI read the report as `, el('b', {}, cc.company + (cc.project ? ' / ' + cc.project : '')), ` (${Math.round((cc.confidence || 0) * 100)}% sure)`),
        cc.evidence ? el('div', { class: 'small muted' }, cc.evidence) : null]
      if (sw) {
        const b = el('button', { class: 'primary', style: 'margin-top:8px;padding:6px 14px' }, `Use ${cc.company}'s checklist and rules`)
        b.addEventListener('click', () => propose(null, [], cc.client_id))
        kidsC.push(el('div', { class: 'small', style: 'margin-top:4px' }, `The Drive folder says ${i.company.client}. Check which one is right.`), b)
      } else if (p.client_id !== i.company.client_id) {
        const b = el('button', { style: 'margin-top:8px' }, `Back to ${i.company.client}`)
        b.addEventListener('click', () => propose(null, [], i.company.client_id))
        kidsC.push(b)
      }
      out.push(card(sw ? 'warn' : 'info', ...kidsC))
    }
    if (drafts) out.push(card('warn', el('div', { class: 'small' }, `${drafts} checklist rule(s) still wait for the Super Admin's approval. They are used for editing now; QC counts them after approval.`)))
    if (p.message) out.push(card('warn', el('div', { class: 'small' }, p.message)))
    return out
  }

  /** Things the editor noticed but must not change by itself (unbalanced quotation marks, stray symbols, unknown words). */
  function attentionCard() {
    const items = ((S.prop && S.prop.skipped) || []).filter((x) => /needs a human/i.test(x.reason || '')).slice(0, 12)
    if (!items.length) return null
    return el('details', { class: 'card warn', style: 'margin-top:12px' }, el('summary', {}, `Needs your attention (${items.length})`),
      el('ul', { class: 'small' }, items.map((x) => el('li', {}, el('b', {}, `p.${x.page} “${x.original}”`), ' — ', String(x.reason).replace(/;?\s*needs a human\.?$/i, '')))))
  }

  function sugCard(e, readonly) {
    const toggle = el('button', { class: e.accepted ? '' : 'primary' }, e.accepted ? 'Dismiss' : 'Accept')
    toggle.addEventListener('click', (ev) => { ev.stopPropagation(); e.accepted = !e.accepted; render(true); layout() })
    const show = el('button', {}, 'Show')
    show.addEventListener('click', (ev) => { ev.stopPropagation(); togglePreview(e) })
    const more = el('button', {}, S.expanded.has(e.id) ? 'Hide comment' : 'Comment')
    more.addEventListener('click', (ev) => { ev.stopPropagation(); S.expanded.has(e.id) ? S.expanded.delete(e.id) : S.expanded.add(e.id); render(true) })
    const changed = e.replacement !== e.orig
    const box = el('div', { class: `sug ${groupOf(e)}${e.accepted ? '' : ' off'}${S.flash === e.id ? ' flash' : ''}`, 'data-id': e.id },
      el('div', { class: 'cat' }, labelOf(e), el('span', { class: 'muted', style: 'text-transform:none;font-weight:500' }, `  · page ${e.page}`)),
      el('div', { class: 'change' }, diffNodes(e)),
      el('div', { class: 'reason' }, el('span', { class: 'chip ' + (e.source === 'ai' ? 'ai' : e.source === 'human' || changed ? 'human' : '') }, e.source === 'ai' && !changed ? 'AI' : e.source === 'human' || changed ? 'you' : 'rules'), e.reason),
      readonly ? el('div', { class: 'acts' }, el('span', { class: 'state', style: 'margin-left:0' }, e.accepted ? 'Accepted' : 'Dismissed')) : el('div', { class: 'acts' }, toggle, show, more, el('span', { class: 'state' }, e.accepted ? 'Will be applied' : 'Dismissed')),
      readonly || (S.reviewMode && !S.expanded.has(e.id)) ? commentsOf(e) : null)
    if (!readonly && S.expanded.has(e.id)) box.append(controls(e, () => render(true)))
    box.addEventListener('click', () => focusSuggestion(e))
    if (S.open === e.id) box.append(previewNode(e))
    return box
  }
  function commentsOf(e) { const c = S.comments[e.id] || []; return c.length ? el('div', { style: 'margin-top:6px;display:grid;gap:4px' }, c.map((x) => el('div', { class: 'said' }, el('b', {}, x.by + ': '), x.text))) : null }

  function previewNode(e) {
    const url = S.previews[e.page]
    const wrapP = el('div', { class: 'preview' })
    if (!url) { wrapP.append(el('div', { class: 'small', style: 'padding:10px;color:#333' }, 'Loading the page…')); return wrapP }
    const pg = (S.prop.pages || []).find((p) => p.page === e.page)
    wrapP.append(el('img', { src: url, alt: `Page ${e.page}` }))
    for (const b of e.raw_boxes || []) {
      const r = D.boxToPercent(b, pg)
      if (r) wrapP.append(el('div', { class: 'hl', style: `left:${r.left}%;top:${r.top}%;width:${r.width}%;height:${r.height}%` }))
    }
    return wrapP
  }

  async function togglePreview(e, forceOpen) {
    if (S.open === e.id && !forceOpen) { S.open = null; return render(true) }
    S.open = e.id
    setPanel(true)
    render(true)
    if (!S.previews[e.page]) {
      const r = await api('GET', `/api/documents/${S.prop.raw_document_id}/pages/${e.page}/image?zoom=1.4`, undefined, 'image')
      S.previews[e.page] = r.ok ? r.dataUrl : 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="60"><text x="10" y="35" font-size="14">Page image not available</text></svg>')
      render(true)
    }
  }

  /** An edit the AI missed: the human types the words as written on the page and what they should become. */
  function addMissedCard() {
    const orig = el('input', { type: 'text', placeholder: 'Words as written on the page, e.g. paid at the counter' })
    const repl = el('input', { type: 'text', placeholder: 'What they should say' })
    const err = el('div', { class: 'errtext' })
    const add = el('button', {}, 'Add this edit')
    add.addEventListener('click', async () => {
      add.disabled = true
      const r = await api('POST', `/api/ext/forms/${S.info.form.id}/proposals/${S.prop.proposal_id}/add`, { original: orig.value, replacement: repl.value })
      add.disabled = false
      if (r.status === 401) return expired()
      if (!r.ok) { err.textContent = r.error; return }
      S.edits = [...S.edits, { ...r.data.edit, orig: r.data.edit.replacement, accepted: true }].sort((a, b) => a.i1 - b.i1)
      render(true); layout()
    })
    return el('details', { class: 'card', style: 'margin-top:12px' }, el('summary', {}, 'Add an edit the AI missed'),
      el('div', { class: 'ctl' }, orig, repl, err, el('div', { class: 'acts' }, add)))
  }

  function currentChanges() {
    const o = {}
    S.edits.forEach((e) => { if (e.replacement !== e.orig) o[e.id] = e.replacement })
    return o
  }
  function currentComments() {
    const o = {}
    Object.entries(S.drafts).forEach(([id, t]) => { if (t && t.trim()) o[id] = t.trim() })
    return o
  }

  function filtersAndSummary() {
    const counts = { red: 0, blue: 0, purple: 0 }
    S.edits.forEach((e) => { counts[groupOf(e)]++ })
    const tab = (key, text, n, dot) => {
      const t = el('button', { class: 'tab' + (S.filter === key ? ' on' : '') }, dot ? el('span', { class: `dot ${dot}` }) : null, `${text} ${n}`)
      t.addEventListener('click', () => { S.filter = key; render(true); layout() })
      return t
    }
    const tabs = el('div', { class: 'tabs' }, tab('all', 'All', S.edits.length), ['red', 'blue', 'purple'].filter((g) => counts[g]).map((g) => tab(g, GROUP_NAME[g], counts[g], g)))
    const summary = el('div', { class: 'summary' }, el('div', { class: 'ring' + (S.edits.length ? '' : ' zero') }, String(S.edits.length)),
      el('div', {}, el('div', { style: 'font-weight:700;font-size:14px' }, S.edits.length ? `${S.edits.length} suggestion${S.edits.length === 1 ? '' : 's'}` : 'Nothing to correct'),
        el('div', { class: 'small muted' }, S.edits.length ? 'Underlined on the page; a card pops up as you scroll.' : 'No correction was found. You can still send the RAW on unchanged.'),
        el('div', { class: 'small muted' }, `Round ${S.rounds}`)))
    return [summary, S.edits.length ? tabs : null]
  }

  function render(keepScroll) {
    if (S.reviewMode) return renderReview(keepScroll)
    const top = bodyEl.scrollTop
    const accepted = S.edits.filter((e) => e.accepted)
    const rejected = S.edits.filter((e) => !e.accepted)
    const fb = el('textarea', { placeholder: 'Write what the AI should change or keep for the whole form. Dismissed suggestions are sent as rejected.' })
    fb.value = S.feedback
    fb.addEventListener('input', () => { S.feedback = fb.value })
    const reedit = el('button', {}, '↻ Re-edit the whole form with AI')
    reedit.addEventListener('click', () => {
      const text = S.feedback.trim()
      S.feedback = ''
      propose(text || null, rejected.map((e) => ({ original: e.original, replacement: e.replacement })))
    })
    const shown = S.edits.filter((e) => S.filter === 'all' || groupOf(e) === S.filter)
    const ret = S.prop.returned
    const note = el('textarea', { placeholder: 'Note for the leader (optional)' })
    note.value = S.note
    note.addEventListener('input', () => { S.note = note.value })
    set(bodyEl, ret ? card('warn', el('h3', {}, `Sent back by ${ret.by}`), el('div', { class: 'small' }, ret.note)) : null, ...filtersAndSummary(), shown.map((e) => sugCard(e)),
      addMissedCard(),
      el('details', { class: 'card', style: 'margin-top:12px' }, el('summary', {}, 'Comment for the whole form'), fb, el('div', { style: 'margin-top:8px' }, reedit),
        S.prop.feedback_history && S.prop.feedback_history.length ? el('div', { class: 'small muted', style: 'margin-top:8px' }, 'Your earlier comments:', el('ul', {}, S.prop.feedback_history.map((h) => el('li', {}, h.comment)))) : null),
      S.caps.can_confirm ? null : card('', el('h3', {}, 'For the leader'), note),
      attentionCard(), companyCard())
    if (S.caps.can_confirm) {
      const go = el('button', { class: 'primary' }, `Confirm and create edited PDF + QC  (${accepted.length} edit${accepted.length === 1 ? '' : 's'})`)
      go.addEventListener('click', finalize)
      set(footEl, el('div', { class: 'small muted' }, 'You are the leader: confirming creates the edited PDF (RAW stays untouched) and sends this form to QC.'), go)
    } else {
      const go = el('button', { class: 'primary' }, `Send to the leader  (${accepted.length} of ${S.edits.length} edits)`)
      go.addEventListener('click', submit)
      set(footEl, el('div', { class: 'small muted' }, 'The leader sees your final edits and comments, confirms them, and the form moves on to QC.'), go)
    }
    S.marks = true
    setBadge()
    if (keepScroll) bodyEl.scrollTop = top
    else layout()
  }

  // ------------------------------------------------------------------------------------------------ hand-over and confirmation
  /** The panel belongs to one file; if the viewer moved on to another, nothing is sent for the wrong form. */
  function sameFileOpen() {
    const cf = currentFile()
    if (cf && S.file && cf.name !== S.file.name) {
      alert(`The file open in Drive is now “${cf.name}”, but this panel is for “${S.file.name}”. The panel is reloading for the open file; nothing was sent.`)
      open(cf, false)
      return false
    }
    return true
  }

  async function submit() {
    if (!sameFileOpen()) return
    const ids = S.edits.filter((e) => e.accepted).map((e) => e.id)
    hideTip()
    busy('Sending your final edits to the leader…')
    const r = await api('POST', `/api/ext/forms/${S.info.form.id}/proposals/${S.prop.proposal_id}/submit`, { accepted: ids, changes: currentChanges(), comments: currentComments(), note: S.note || null })
    if (r.status === 401) return expired()
    if (!r.ok) { S.marks = true; render(); alert(r.error); return }
    S.drafts = {}
    const o = await api('GET', `/api/ext/forms/${S.info.form.id}/proposal`)
    if (o.ok && o.data.proposal) return loadProposal(o.data.proposal)
    done(null)
  }

  /** Edits that were cross-checked and handed over: the editor sees what the leader will see; the leader can still change things, then confirms or sends back. */
  function renderReview(keepScroll) {
    const top = bodyEl.scrollTop
    const rv = S.prop.review || {}
    const leader = !!S.caps.can_confirm
    const accepted = S.edits.filter((e) => e.accepted)
    const dismissed = S.edits.filter((e) => !e.accepted)
    const reworded = S.edits.filter((e) => e.replacement !== e.orig)
    const back = el('textarea', { placeholder: 'What should the editor change? (required to send it back)' })
    back.value = S.note
    back.addEventListener('input', () => { S.note = back.value })
    const sendBack = el('button', {}, 'Send back to the editor')
    sendBack.addEventListener('click', async () => {
      if (S.note.trim().length < 2) { back.focus(); return }
      const r = await api('POST', `/api/ext/forms/${S.info.form.id}/proposals/${S.prop.proposal_id}/send-back`, { note: S.note })
      if (!r.ok) return alert(r.error)
      S.note = ''
      loadProposal(r.data)
    })
    const head = card(leader ? 'info' : 'ok', el('h3', {}, leader ? 'Waiting for your confirmation' : 'Sent to the leader'),
      el('div', { class: 'small' }, `${rv.submitted_by || 'An editor'} checked the AI's edits: ${accepted.length} accepted, ${dismissed.length} dismissed, ${reworded.length} reworded by a person.`),
      rv.note ? el('div', { class: 'said', style: 'margin-top:6px' }, el('b', {}, 'Note: '), rv.note) : null,
      leader ? null : el('div', { class: 'small muted', style: 'margin-top:6px' }, 'The leader confirms this; then the edited PDF is created and QC starts.'))
    const list = S.edits.map((e) => sugCard(e, !leader))
    set(bodyEl, head, ...filtersAndSummary().slice(1), list, leader ? card('', el('h3', {}, 'Send back instead'), back, el('div', { style: 'margin-top:8px' }, sendBack)) : null, companyCard())
    if (leader) {
      const go = el('button', { class: 'primary' }, `Confirm and create edited PDF + QC  (${accepted.length} edit${accepted.length === 1 ? '' : 's'})`)
      go.addEventListener('click', finalize)
      set(footEl, el('div', { class: 'small muted' }, 'You can still dismiss or reword edits above before confirming.'), go)
    } else set(footEl)
    S.marks = true
    setBadge()
    if (keepScroll) bodyEl.scrollTop = top
    else layout()
  }

  async function finalize() {
    if (!sameFileOpen()) return
    const ids = S.edits.filter((e) => e.accepted).map((e) => e.id)
    hideTip()
    busy('Creating the edited PDF…')
    const r = await api('POST', `/api/ext/forms/${S.info.form.id}/finalize`, { proposal_id: S.prop.proposal_id, accepted: ids, changes: currentChanges() })
    if (r.status === 401) return expired()
    if (!r.ok) { S.marks = true; render(); alert(r.error); return }
    S.result = r.data
    pollQc()
  }

  async function pollQc() {
    const started = Date.now()
    for (;;) {
      const r = await api('GET', `/api/ext/forms/${S.info.form.id}/status`)
      const qc = r.ok ? r.data.qc : null
      if (qc && ['completed', 'completed_with_warnings', 'failed'].includes(qc.status)) return done(r.data)
      set(bodyEl, card('', el('span', { class: 'spin' }), `Edited PDF v${S.result.version} created (${S.result.applied} correction${S.result.applied === 1 ? '' : 's'}). QC is running…`))
      if (Date.now() - started > 240000) return done(r.ok ? r.data : null)
      await new Promise((res) => setTimeout(res, 2500))
    }
  }

  async function done(st) {
    const qc = st && st.qc
    const base = (await send({ type: 'session' })).server
    const open = el('a', { href: `${base}/app/forms/${S.info.form.id}`, target: '_blank', rel: 'noopener' }, 'Open the form in the platform')
    const dl = el('button', {}, '⬇ Download the QC report (PDF)')
    dl.addEventListener('click', async () => {
      dl.disabled = true
      const f = await api('GET', `/api/forms/${S.info.form.id}/qc-report`, undefined, 'file')
      dl.disabled = false
      if (!f.ok) return alert(f.error)
      const a = document.createElement('a')
      a.href = f.dataUrl
      a.download = `QC_Report_${S.info.form.code}.pdf`
      a.click()
    })
    const kids = [el('h3', {}, `Edited PDF v${S.result.version} saved`), el('div', { class: 'small' }, S.result.verification)]
    if (qc && qc.status !== 'failed') {
      kids.push(el('div', { style: 'margin-top:8px' }, el('b', {}, `QC score ${qc.score}/100`), `  · ${qc.findings} finding${qc.findings === 1 ? '' : 's'}`),
        el('ul', { class: 'small' }, Object.entries(qc.by_category).filter(([, n]) => n).map(([k, n]) => el('li', {}, `${k}: ${n}`))))
    } else kids.push(el('div', { class: 'small', style: 'margin-top:8px' }, qc ? `QC could not finish: ${qc.error || 'error'}` : 'QC is still running; the form page shows the result when it is done.'))
    kids.push(el('div', { style: 'margin-top:8px;display:grid;gap:8px' }, open, dl))
    S.marks = false; layout(); hideTip()
    badgeN.textContent = '✓'; badgeN.classList.add('zero'); setPanel(true)
    set(bodyEl, card(qc && qc.status !== 'failed' ? 'ok' : 'warn', ...kids))
    const next = S.caps.can_confirm ? el('button', {}, '☰ Next form in the queue') : null
    if (next) next.addEventListener('click', showQueue)
    set(footEl, next)
  }

  // ------------------------------------------------------------------------------------------------ what the extension sees on this page
  function diagnostics() {
    const lines = []
    const total = S.prop && S.prop.pages ? S.prop.pages.length : 0
    const found = pageEls()
    lines.push(`file: ${S.file ? S.file.name : '-'} (drive id ${S.file && S.file.driveId ? 'yes' : 'no'})`)
    lines.push(`edits: ${S.edits.length} (pages ${[...new Set(S.edits.map((e) => e.page))].join(',') || '-'}), PDF pages: ${total}`)
    lines.push(`page images found: ${found.length} ${found.length ? '' : '<- none: underlines cannot be drawn'}`)
    lines.push(`underlines drawn: ${marks.size}; pager page: ${pagerPage() || 'not found'}; auto cards: ${S.auto ? 'on' : 'off'}; cards shown so far: ${S.seen.size}`)
    lines.push(`iframes on the page: ${document.querySelectorAll('iframe').length}; scroll containers: ${[...document.querySelectorAll('div')].filter((d) => d.scrollHeight > d.clientHeight + 200 && /auto|scroll/.test(getComputedStyle(d).overflowY)).length}`)
    lines.push('--- largest picture-like elements ---')
    const cands = [...document.querySelectorAll('img,canvas,iframe,embed,object,svg')].filter((n) => !host.contains(n)).map((n) => ({ n, r: n.getBoundingClientRect() }))
      .filter((x) => x.r.width >= 120 && x.r.height >= 120).sort((a, b) => b.r.width * b.r.height - a.r.width * a.r.height).slice(0, 8)
    for (const { n, r } of cands) {
      const src = n.tagName === 'IMG' ? (n.currentSrc || n.src || '').replace(/^(\w+:).*/, '$1') : ''
      lines.push(`${n.tagName.toLowerCase()} ${Math.round(r.width)}x${Math.round(r.height)} at ${Math.round(r.left)},${Math.round(r.top)} ${src} class="${String(n.getAttribute('class') || '').slice(0, 50)}" aria="${String(n.getAttribute('aria-label') || '').slice(0, 30)}"`)
    }
    if (!cands.length) lines.push('(none)')
    return lines.join('\n')
  }
  function showDiagnostics() {
    setPanel(true)
    const text = diagnostics()
    const copy = el('button', {}, 'Copy this')
    copy.addEventListener('click', () => { navigator.clipboard.writeText(text).then(() => { copy.textContent = 'Copied' }) })
    const back = el('button', {}, '← Back')
    back.addEventListener('click', () => { if (S.prop) render(); else if (S.file) start() })
    set(bodyEl, card('', el('h3', {}, 'What the extension sees on this page'), el('pre', { style: 'white-space:pre-wrap;font-size:11px;margin:0' }, text), el('div', { style: 'margin-top:8px' }, copy)))
    set(footEl, back)
  }

  // ------------------------------------------------------------------------------------------------ the leader's queue
  async function showQueue() {
    setPanel(true)
    set(bodyEl, card('', el('span', { class: 'spin' }), 'Loading the forms that wait for you…'))
    const r = await api('GET', '/api/ext/review-queue')
    if (r.status === 401) return expired()
    if (!r.ok) return fail(r.error, showQueue)
    const items = r.data.items
    const rows = items.map((it) => {
      const here = S.info && S.info.form && it.form.id === S.info.form.id
      const open = it.drive_id ? el('a', { href: `https://drive.google.com/file/d/${it.drive_id}/view`, target: '_blank', rel: 'noopener' }, 'Open in Drive') : null
      const review = el('button', { style: 'padding:3px 12px;border-radius:999px;font-size:12px' }, 'Review here')
      review.addEventListener('click', start)
      return card('', el('b', {}, it.form.code), el('span', { class: 'muted' }, '  ' + it.company),
        el('div', { class: 'small' }, `${it.submitted_by || 'Editor'} · ${it.accepted} accepted, ${it.dismissed} dismissed, ${it.reworded} reworded, ${it.commented} with comments`),
        it.note ? el('div', { class: 'said', style: 'margin-top:4px' }, it.note) : null, el('div', { class: 'acts' }, here ? review : null, open))
    })
    S.marks = false; layout(); hideTip()
    set(bodyEl, el('h3', {}, `Waiting for your confirmation (${items.length})`), items.length ? rows : card('ok', el('div', { class: 'small' }, 'Nothing is waiting. Forms the editors have cross-checked show up here.')))
    const back = el('button', {}, '← Back to this form')
    back.addEventListener('click', () => { if (S.prop) { S.marks = true; render() } else if (S.file) start() })
    set(footEl, back)
  }

  // ------------------------------------------------------------------------------------------------ detect the open file
  /** Drive's own words in front of a file name ("PDF 3353783_1.pdf", "Image ...") are not part of the name. */
  function cleanName(t) {
    return (t || '').replace(/\s+/g, ' ').trim().replace(/^(?:PDF|Image|Video|Audio|Google (?:Docs?|Sheets?|Slides?)|Microsoft Word|Text)(?:\s*:\s*|\s+|(?=\d))/i, '').trim()
  }
  /** The file name shown in the preview's header bar (Drive keeps the tab title generic while the preview is open inside a folder). */
  function nameFromPreviewHeader() {
    for (const n of document.querySelectorAll('div,span,h1,h2')) {
      if (host.contains(n) || n.childElementCount) continue
      const t = (n.textContent || '').trim()
      if (t.length < 5 || t.length > 120 || !/\.pdf$/i.test(t)) continue
      const r = n.getBoundingClientRect()
      if (!(r.top >= 0 && r.top < 130 && r.width > 0 && D.parseName(cleanName(t)))) continue
      const hit = document.elementFromPoint(r.left + Math.min(r.width / 2, 40), r.top + r.height / 2)       // the list rows under the preview are covered, the header is not
      if (hit && (n.contains(hit) || hit.contains(n))) return cleanName(t)
    }
    return null
  }

  function currentFile() {
    const name = D.nameFromTitle(document.title)
    const driveId = D.driveIdFromUrl(location.href)
    if (name) return { name, driveId }
    // folder view with the preview overlay / a selected row (toolbar button)
    const sel = document.querySelector('[data-id][aria-selected="true"]')
    const rowId = sel ? sel.getAttribute('data-id') : null
    const header = nameFromPreviewHeader()
    if (header) {
      const rowText = sel ? (sel.getAttribute('aria-label') || sel.textContent || '') : ''
      return { name: header, driveId: rowText.includes(header) ? rowId : null }       // arrows inside the preview change the file but not the selected row
    }
    if (sel) {
      const label = sel.getAttribute('data-tooltip') || sel.getAttribute('aria-label') || sel.textContent || ''
      const m = cleanName(label).match(/(.*?\.pdf)/i)
      if (m) return { name: cleanName(m[1].replace(/^.*?:\s*/, '')), driveId: rowId }
    }
    return null
  }

  function open(file, manual) {
    if (!file) return manual ? show('Open a PDF (or select one) in Google Drive first.') : null
    const parsed = D.parseName(file.name)
    if (!parsed && !manual) return null
    if (parsed && parsed.kind !== 'raw' && !manual) return null            // only RAW forms open the panel by themselves
    if (!host.isConnected) document.documentElement.appendChild(host)
    window.__msaiOpen = true
    if (S.file && S.file.name === file.name && S.file.driveId === file.driveId && S.prop) return
    S.hideMarks = false; badge.classList.remove('dim')
    setPanel(false)                                                          // a Grammarly-like start: the badge + underlines first, the panel on a click
    S.file = file; S.info = null; S.prop = null; S.edits = []; S.rounds = 0; S.result = null; S.reviewMode = false; S.drafts = {}; S.comments = {}; S.expanded = new Set(); S.note = ''
    start()
  }
  function show(text) { if (!host.isConnected) document.documentElement.appendChild(host); setPanel(true); set(bodyEl, card('warn', text)); set(footEl) }

  let last = ''
  every(() => {
    const cf = currentFile()
    const key = location.href + '|' + document.title + '|' + (cf ? cf.name : '')
    if (key === last) return
    last = key
    open(cf, false)
  }, 1000)
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === 'open-panel') {            // toolbar button: always brings the panel back
      S.hideMarks = false; badge.classList.remove('dim')
      open(currentFile(), true); setPanel(true); layout()
    }
  })
})()
