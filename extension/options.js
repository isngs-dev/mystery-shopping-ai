const $ = (id) => document.getElementById(id)
const DEFAULT_SERVER = 'https://mystery-shopping-ai.onrender.com'

async function refresh() {
  const s = await chrome.storage.local.get(['server', 'user'])
  $('server').value = s.server || DEFAULT_SERVER
  $('who').textContent = s.user ? `Signed in as ${s.user.name} (${s.user.role}).` : 'Not signed in: sign in from the panel that opens next to a RAW form.'
  $('logout').hidden = !s.user
}

$('save').addEventListener('click', async () => {
  const msg = $('msg')
  let url
  try { url = new URL($('server').value.trim()) } catch { msg.className = 'err'; msg.textContent = 'Enter a full address, e.g. https://mystery-shopping-ai.onrender.com'; return }
  if (url.protocol !== 'https:' && !['localhost', '127.0.0.1'].includes(url.hostname)) { msg.className = 'err'; msg.textContent = 'Use https:// (plain http is only allowed for localhost).'; return }
  const granted = await chrome.permissions.request({ origins: [url.origin + '/*'] })        // the browser asks you once
  if (!granted) { msg.className = 'err'; msg.textContent = 'Permission for this address was not granted.'; return }
  await chrome.storage.local.set({ server: url.origin })
  await chrome.storage.local.remove(['token', 'user'])                                       // a different server needs a new sign-in
  msg.className = 'ok'; msg.textContent = 'Saved. Open a RAW form in Google Drive and sign in from the panel.'
  refresh()
})

$('logout').addEventListener('click', async () => { await chrome.runtime.sendMessage({ type: 'logout' }); refresh() })
refresh()
