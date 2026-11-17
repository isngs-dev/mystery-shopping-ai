/* Service worker: the only place that talks to the platform. The page (content script) sends {type:'api'} messages; this worker adds the
   bearer token, calls the server (host permission, so no CORS) and returns JSON or an image as a data URL.
   The token lives in chrome.storage.local; the password is never stored. */
const DEFAULT_SERVER = 'https://mystery-shopping-ai.onrender.com'

async function settings() {
  const s = await chrome.storage.local.get(['server', 'token', 'user'])
  return { server: (s.server || DEFAULT_SERVER).replace(/\/+$/, ''), token: s.token || null, user: s.user || null }
}

async function call({ method = 'GET', path, body, as = 'json' }) {
  const { server, token } = await settings()
  const headers = { 'X-Requested-With': 'XMLHttpRequest' }
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let res
  try {
    res = await fetch(server + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  } catch (e) {
    return { ok: false, status: 0, error: `Cannot reach ${server}. Check the server address in the extension options and your internet connection.` }
  }
  if (res.status === 401) await chrome.storage.local.remove(['token', 'user'])
  if (as === 'image') {
    if (!res.ok) return { ok: false, status: res.status, error: 'Page image not available.' }
    const buf = new Uint8Array(await res.arrayBuffer())
    let bin = ''
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000))
    return { ok: true, status: 200, dataUrl: `data:image/png;base64,${btoa(bin)}` }
  }
  if (as === 'file') {
    if (!res.ok) return { ok: false, status: res.status, error: 'Download failed.' }
    const buf = new Uint8Array(await res.arrayBuffer())
    let bin = ''
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000))
    return { ok: true, status: 200, dataUrl: `data:${res.headers.get('content-type') || 'application/octet-stream'};base64,${btoa(bin)}` }
  }
  let data = null
  try { data = await res.json() } catch { /* empty body */ }
  if (!res.ok) return { ok: false, status: res.status, error: (data && (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail))) || `Server error ${res.status}` }
  return { ok: true, status: res.status, data }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  ;(async () => {
    if (msg.type === 'api') return call(msg)
    if (msg.type === 'session') { const s = await settings(); return { ok: true, server: s.server, user: s.token ? s.user : null } }
    if (msg.type === 'login') {
      const r = await call({ method: 'POST', path: '/api/ext/login', body: { email: msg.email, password: msg.password } })
      if (r.ok) await chrome.storage.local.set({ token: r.data.token, user: r.data.user })
      return r
    }
    if (msg.type === 'logout') { await chrome.storage.local.remove(['token', 'user']); return { ok: true } }
    if (msg.type === 'options') { chrome.runtime.openOptionsPage(); return { ok: true } }
    return { ok: false, error: 'Unknown message.' }
  })().then(sendResponse)
  return true // answer asynchronously
})

// Toolbar button: ask the page to open the panel for the file that is selected / open right now
chrome.action.onClicked.addListener((tab) => {
  if (tab.id) chrome.tabs.sendMessage(tab.id, { type: 'open-panel' }).catch(() => {})
})
