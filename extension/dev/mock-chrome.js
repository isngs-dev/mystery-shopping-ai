/* DEV ONLY: pretends to be the extension runtime so the panel can be looked at (and clicked through) in a normal web page. */
(function () {
  const page = { page: 1, width: 595, height: 842 }
  const edits = [
    { id: 'a1', page: 1, original: 'The cashier greet me warmly', replacement: 'The cashier greeted me warmly', reason: 'Past tense (company rule)', category: 'tense', confidence: 0.9, source: 'ai', i1: 8, i2: 13,
      raw_boxes: [{ page: 1, x0: 56, y0: 128, x1: 232, y1: 143 }] },
    { id: 'b2', page: 1, original: 'recieved', replacement: 'received', reason: 'Spelling: “recieved” → “received”', category: 'spelling', confidence: 0.9, source: 'rules', i1: 15, i2: 16,
      raw_boxes: [{ page: 1, x0: 55.8, y0: 148.7, x1: 95.5, y1: 163.1 }] },
    { id: 'c3', page: 1, original: 'order,quickly', replacement: 'order, quickly', reason: 'Missing space after punctuation added', category: 'spacing', confidence: 0.85, source: 'rules', i1: 17, i2: 18,
      raw_boxes: [{ page: 1, x0: 115.4, y0: 148.7, x1: 174.9, y1: 163.1 }] },
  ]
  const info = { found: true, form: { id: 80, code: '3353921', status: 'pending', edited_versions: 0 }, company: { client: 'APC', project: 'Valley Bank_Devansh', path: 'APC / Valley Bank_Devansh' },
    identified_by: 'drive folder', is_raw: true, company_rules: 2, pending_approval: [{ key: 'APC-AVOID-PHRASES', name: 'Phrases that must not appear' }],
    rules: [{ key: 'GEN-SPELL', name: 'Spelling', error: 'Spelling', description: 'Words must be spelled correctly.', scope: 'general', company_specific: false },
      { key: 'GEN-PUNCT', name: 'No space before punctuation', error: 'Extra Space/No space', description: 'No space before commas and full stops.', scope: 'general', company_specific: false },
      { key: 'APC-PAST-TENSE', name: 'Comments in the past tense', error: 'Past tense', description: 'All sentences must be in the past tense and complete.', scope: 'client', company_specific: true },
      { key: 'APC-NEGATIVE', name: 'Negative markings need comments', error: 'Negative not answered', description: 'Every NO needs a comment.', scope: 'client', company_specific: true }],
    ai: { usable: true } }
  let round = 0
  let qcPolls = 0
  const ok = (data) => Promise.resolve({ ok: true, status: 200, data })
  window.chrome = {
    runtime: {
      onMessage: { addListener() {} },
      sendMessage(msg) {
        if (msg.type === 'session') return Promise.resolve({ ok: true, server: 'https://mystery-shopping-ai.onrender.com', user: { name: 'Devansh', role: 'qc_reviewer' } })
        if (msg.type === 'options') return Promise.resolve({ ok: true })
        if (msg.type === 'api') {
          if (msg.path === '/api/ext/identify') return ok(info)
          if (msg.path.endsWith('/propose')) {
            round += 1
            const kept = msg.body && msg.body.rejected && msg.body.rejected.length ? edits.filter((e) => !msg.body.rejected.some((r) => r.original === e.original)) : edits
            return ok({ proposal_id: 7, round, edits: kept, skipped: [], pages: [page], raw_document_id: 194, engine: 'ai+rules', ai_status: 'completed', ai_model: 'claude-sonnet', message: null,
              company_check: { company: 'APC', confidence: 0.93, evidence: 'Valley Bank teller report', matches_folder: true }, company: info.company, company_rules: 2, rules_used: 4, pending_rules: 1,
              feedback_history: msg.body && msg.body.feedback ? [{ comment: msg.body.feedback }] : [] })
          }
          if (msg.path.includes('/pages/')) return Promise.resolve({ ok: true, dataUrl: 'sample-page.png' })
          if (msg.path.endsWith('/finalize')) return Promise.resolve({ ok: true, status: 202, data: { document_id: 9, version: 1, applied: msg.body.accepted.length, verification: 'Verified: edited PDF matches the planned edits (100.0%); all figures unchanged.', qc_run_id: 3, skipped: [] } })
          if (msg.path.endsWith('/status')) {
            qcPolls += 1
            return ok({ form: info.form, edited_version: 1, qc: qcPolls < 2 ? { status: 'running' } : { run_id: 3, status: 'completed_with_warnings', findings: 4, score: 75, deductions: [], by_category: { 'Grammar Mistake': 3, Contradiction: 1, 'Missed Instruction': 0 } } })
          }
        }
        return Promise.resolve({ ok: false, error: 'mock: not handled ' + (msg.path || msg.type) })
      },
    },
  }
})()
