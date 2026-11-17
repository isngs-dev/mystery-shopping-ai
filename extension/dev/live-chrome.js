/* DEV ONLY: runs the REAL background.js (service worker code) in a normal page, so the panel talks to the real server (same origin proxy). */
(function () {
  const store = {}
  const listeners = []
  try { Object.assign(store, JSON.parse(localStorage.getItem('dev_ext_storage') || '{}')) } catch (e) {}
  store.server = location.origin
  const persist = () => { try { localStorage.setItem('dev_ext_storage', JSON.stringify(store)) } catch (e) {} }
  window.chrome = {
    storage: { local: {
      async get(keys) { const o = {}; for (const k of keys) if (k in store) o[k] = store[k]; return o },
      async set(o) { Object.assign(store, o); persist() },
      async remove(keys) { for (const k of keys) delete store[k]; persist() },
    } },
    runtime: {
      id: 'dev-preview',
      onMessage: { addListener(fn) { listeners.push(fn) } },
      openOptionsPage() { alert('Options page: server address is set to ' + location.origin + ' in this dev preview.') },
      sendMessage(msg) {
        return new Promise((resolve) => { const fn = listeners.find(Boolean); fn(msg, {}, resolve) })
      },
    },
    action: { onClicked: { addListener() {} } },
    tabs: { sendMessage: () => Promise.resolve() },
  }
})()
