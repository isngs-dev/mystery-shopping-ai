/* Pure helpers (no DOM, no chrome.*): which file is open, is it a RAW form, how to show an edit. Tested with `node --test extension/test`.
   The file-name patterns mirror backend/app/services/drive_import.py (PDF_NAME) so the extension and the Drive sync agree on what a RAW is. */
(function (root) {
  const PDF_NAME = [
    { rx: /^([A-Za-z0-9-]+)_(\d+)\.pdf$/i },
    { rx: /^(\d{4,})\s*-\s*.*?-\s*(\d+)\.pdf$/i },
    { rx: /^(\d{4,})\s*-\s*(\d+)\.pdf$/i },
    { rx: /^(\d{4,})\s*\(\s*(\d+)\s*\)\.pdf$/i },
    { rx: /^(\d{4,})\s*-\s*[^\d\W][^.]*\.pdf$/i, unnumbered: true }, // "3353722 - Admin Shop Review.pdf": no number = the RAW
  ]

  function parseName(name) {
    const n = (name || '').trim()
    for (const p of PDF_NAME) {
      const m = n.match(p.rx)
      if (m) return { code: m[1].toUpperCase(), number: p.unnumbered ? 1 : parseInt(m[2], 10), kind: (p.unnumbered || parseInt(m[2], 10) === 1) ? 'raw' : 'edited' }
    }
    return null
  }

  /** Google Drive shows the file name in the tab title: "3353921_1.pdf - Google Drive" */
  function nameFromTitle(title) {
    const t = (title || '').replace(/\s*[-–]\s*Google Drive\s*$/i, '').trim()
    return /\.pdf$/i.test(t) ? t : null
  }

  function driveIdFromUrl(url) {
    const m = (url || '').match(/drive\.google\.com\/(?:file\/d\/|open\?id=|uc\?id=)([A-Za-z0-9_-]{10,})/) || (url || '').match(/[?&]id=([A-Za-z0-9_-]{10,})/)
    return m ? m[1] : null
  }

  /** Word-level difference between the original and the corrected text, for the red / green display. */
  function diffWords(a, b) {
    const x = (a || '').split(/\s+/).filter(Boolean)
    const y = (b || '').split(/\s+/).filter(Boolean)
    const dp = Array.from({ length: x.length + 1 }, () => new Array(y.length + 1).fill(0))
    for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--) dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    const out = []
    let i = 0, j = 0
    while (i < x.length && j < y.length) {
      if (x[i] === y[j]) { out.push({ t: 'same', w: x[i] }); i++; j++ }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: 'del', w: x[i] }); i++ }
      else { out.push({ t: 'add', w: y[j] }); j++ }
    }
    while (i < x.length) out.push({ t: 'del', w: x[i++] })
    while (j < y.length) out.push({ t: 'add', w: y[j++] })
    return out
  }

  /** Highlight rectangle in percent of the page, from a PDF-point box and the page size. */
  function boxToPercent(box, page) {
    if (!box || !page || !page.width || !page.height) return null
    return { left: (box.x0 / page.width) * 100, top: (box.y0 / page.height) * 100, width: ((box.x1 - box.x0) / page.width) * 100, height: ((box.y1 - box.y0) / page.height) * 100 }
  }

  const api = { parseName, nameFromTitle, driveIdFromUrl, diffWords, boxToPercent }
  if (typeof module !== 'undefined' && module.exports) module.exports = api
  root.MSAI_DETECT = api
})(typeof globalThis !== 'undefined' ? globalThis : this)
