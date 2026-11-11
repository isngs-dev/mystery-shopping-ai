import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import { ArrowLeft, BadgeAlert, Check, ChevronDown, Download, FileText, Flag, Pencil, Plus, RotateCcw, ScanSearch, X } from 'lucide-react'
import clsx from 'clsx'
import { api, download } from '../lib/api'
import { fmtDate, REVIEW, SEVERITY, TYPE_LABEL } from '../lib/format'
import { Alert, Badge, Field, Modal, ReviewBadge, SeverityBadge, Skeleton, StatusBadge, Tabs, useToast } from '../components/ui'
import { PdfViewer } from '../components/PdfPage'
import { RunProgress } from './FormDetail'
import { useCopilotHint } from '../lib/copilot'
import { useErrorTable } from '../lib/errorTable'
import { useAuth } from '../lib/auth'
import type { AutoEdit, Finding } from '../lib/types'
import { AiEditPanel } from '../components/AiEditPanel'

type Filter = 'all' | 'pending' | 'accepted' | 'rejected'

function TextPanel({ docId, page }: { docId: number; page: number }) {
  const q = useQuery({ queryKey: ['doctext', docId], queryFn: () => api.get<any>(`/api/documents/${docId}/text`) })
  const p = q.data?.pages.find((x: any) => x.page_no === page)
  if (q.isLoading) return <Skeleton className="h-24" />
  return (
    <div className="max-h-44 overflow-auto rounded-xl border bg-surface2/50 p-3 text-xs">
      <p className="mb-1 flex items-center justify-between font-semibold text-muted"><span>Extracted text · page {page}</span>{p && <Badge t={p.method === 'ocr' ? 'warn' : 'muted'}>{p.method === 'ocr' ? `OCR ${p.ocr_confidence ? Math.round(p.ocr_confidence * 100) + '%' : ''}` : p.method === 'none' ? 'no text' : 'text layer'}</Badge>}</p>
      {p?.note && <p className="mb-1 text-warn">{p.note}</p>}
      <pre className="whitespace-pre-wrap font-mono leading-relaxed text-ink/85">{p?.text || '(no extractable text on this page)'}</pre>
    </div>
  )
}

function FindingCard({ f, active, onSelect, canReview, onAction }: { f: Finding; active: boolean; onSelect: () => void; canReview: boolean; onAction: (a: string, f: Finding) => void }) {
  const decided = f.review_status !== 'pending'
  return (
    <motion.div layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} onClick={onSelect}
      className={clsx('cursor-pointer rounded-xl border p-3 transition', active ? 'border-brand bg-brand/10 shadow-glow' : 'bg-surface2/40 hover:border-brand/40', decided && !active && 'opacity-80')}>
      <div className="flex flex-wrap items-center gap-1.5">
        <SeverityBadge s={f.severity} /><ReviewBadge s={f.review_status} />
        <Badge t={f.source === 'ai' ? 'violet' : f.source === 'manual' ? 'cyan' : 'muted'}>{f.source === 'ai' ? 'AI' : f.source === 'manual' ? 'Reviewer-added' : 'Rule check'}</Badge>
        {f.needs_human_attention && <Badge t="warn"><BadgeAlert size={11} /> Verify</Badge>}
      </div>
      <h4 className="mt-1.5 text-sm font-bold leading-snug">{f.title}</h4>
      <p className="text-xs text-muted"><b className="text-ink/80">{f.category_label || TYPE_LABEL[f.type] || f.type}</b>{f.error_label ? ` › ${f.error_label}` : ''}{f.points ? ` · −${f.points}` : ''}{(f.edited_page || f.raw_page) ? ` · page ${f.edited_page || f.raw_page}` : ''}{f.type === 'missed_correction' ? ' · left by editor' : f.type === 'new_error' ? ' · added by editor' : ''}</p>
      <AnimatePresence initial={false}>
        {active && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="space-y-2 pt-2 text-xs" onClick={(e) => e.stopPropagation()}>
              <p className="text-sm text-ink/90">{f.description}</p>
              {f.raw_excerpt && <div><p className="font-semibold text-cyan">RAW evidence · p.{f.raw_page}</p><p className="rounded-lg border border-cyan/30 bg-cyan/5 p-2 font-mono">“{f.raw_excerpt}”</p></div>}
              {f.edited_excerpt && <div><p className="font-semibold text-warn">Edited evidence · p.{f.edited_page}</p><p className="rounded-lg border border-warn/30 bg-warn/5 p-2 font-mono">“{f.edited_excerpt}”</p></div>}
              {f.suggested_correction && <div><p className="font-semibold text-ok">Recommended correction</p><p className="rounded-lg border border-ok/30 bg-ok/5 p-2">{f.suggested_correction}</p></div>}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted">
                <span>Rule: <b className="text-ink/80">{f.rule_label || 'none cited — verify manually'}</b></span>
                {f.confidence !== null && <span className="inline-flex items-center gap-1.5">Confidence <span className="inline-block h-1.5 w-16 overflow-hidden rounded bg-muted/30"><span className="block h-full bg-gradient-to-r from-warn to-ok" style={{ width: `${f.confidence * 100}%` }} /></span>{Math.round(f.confidence * 100)}%</span>}
              </div>
              {f.reviewer_comment && <p className="rounded-lg bg-surface2 p-2 text-muted">Reviewer: {f.reviewer_comment}</p>}
              {canReview && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  <button className="btn-ok !px-2.5 !py-1 text-xs" onClick={() => onAction('accept', f)}><Check size={13} /> Accept</button>
                  <button className="btn-danger !px-2.5 !py-1 text-xs" onClick={() => onAction('reject', f)}><X size={13} /> Reject</button>
                  <button className="btn-ghost !px-2.5 !py-1 text-xs" onClick={() => onAction('edit', f)}><Pencil size={13} /> Edit</button>
                  <button className="btn-ghost !px-2.5 !py-1 text-xs" onClick={() => onAction('not_applicable', f)}>N/A</button>
                  <button className="btn-ghost !px-2.5 !py-1 text-xs" onClick={() => onAction('escalate', f)}><Flag size={13} /> Escalate</button>
                  {decided && <button className="btn-ghost !px-2.5 !py-1 text-xs" onClick={() => onAction('reset', f)}><RotateCcw size={13} /> Undo</button>}
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

/** The QC workspace (RAW | findings | edited). `embedded` shows it inside the form page instead of as its own page. */
export default function QCWorkspace({ formId, embedded = false }: { formId?: string; embedded?: boolean } = {}) {
  const params = useParams()
  const id = formId ?? params.id
  const qc = useQueryClient()
  const toast = useToast()
  const { can } = useAuth()
  const [runId, setRunId] = useState<number | undefined>()
  const [sel, setSel] = useState<number | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [sev, setSev] = useState('')
  const [rawPage, setRawPage] = useState(1)
  const [edPage, setEdPage] = useState(1)
  const [dialog, setDialog] = useState<{ action: string; f: Finding } | null>(null)
  const [manual, setManual] = useState<false | string>(false)
  const [aiSel, setAiSel] = useState<number | 'all' | null>(null)
  const [mobileTab, setMobileTab] = useState<'raw' | 'findings' | 'edited'>('findings')

  const q = useQuery({
    queryKey: ['qc', id, runId], queryFn: () => api.get<any>(`/api/forms/${id}/qc${runId ? `?run_id=${runId}` : ''}`),
    refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.run?.status) ? 1500 : false,
  })
  const w = q.data
  const aiq = useQuery({ queryKey: ['ai-edits', id], queryFn: () => api.get<AutoEdit[]>(`/api/forms/${id}/ai-edits`) })
  const aiForDoc = aiq.data?.find((a) => a.result_document_id && a.result_document_id === w?.edited_document?.id)
  useEffect(() => { if (aiForDoc?.edits.length) setAiSel('all') }, [aiForDoc?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const aiPicked = !aiForDoc ? [] : aiSel === 'all' ? aiForDoc.edits : typeof aiSel === 'number' && aiForDoc.edits[aiSel] ? [aiForDoc.edits[aiSel]] : []
  const findings: Finding[] = w?.findings || []
  const shown = useMemo(() => findings.filter((f) => (filter === 'all' || (filter === 'accepted' ? ['accepted', 'edited'].includes(f.review_status) : f.review_status === filter)) && (!sev || f.severity === sev)), [findings, filter, sev])
  const active = findings.find((f) => f.id === sel) || null
  const counts = useMemo(() => ({ pending: findings.filter((f) => f.review_status === 'pending').length, accepted: findings.filter((f) => ['accepted', 'edited'].includes(f.review_status)).length, rejected: findings.filter((f) => f.review_status === 'rejected').length, escalated: findings.filter((f) => f.review_status === 'escalated').length }), [findings])
  const run = w?.run
  const running = run && ['queued', 'running'].includes(run.status)
  const refresh = () => { qc.invalidateQueries({ queryKey: ['qc', id] }); qc.invalidateQueries({ queryKey: ['form', id] }); qc.invalidateQueries({ queryKey: ['queue'] }) }

  useEffect(() => { if (active) { if (active.raw_page) setRawPage(active.raw_page); if (active.edited_page) setEdPage(active.edited_page) } }, [sel]) // eslint-disable-line
  useCopilotHint(running ? 'scanning' : q.isLoading ? 'thinking' : run?.status === 'failed' || findings.some((f) => f.severity === 'critical' && f.review_status === 'pending') ? 'alert' : w?.form.status === 'approved' ? 'celebrate' : counts.pending === 0 && findings.length > 0 ? 'celebrate' : 'idle',
    running ? 'Scanning both PDFs and comparing them word by word…' : run?.status === 'failed' ? 'I could not complete this analysis. See the error below — nothing has been approved.' : findings.some((f) => f.severity === 'critical' && f.review_status === 'pending') ? 'Critical findings are waiting for your decision. I only point to evidence — you decide.' : counts.pending === 0 && findings.length > 0 ? 'Every finding has a decision. You can save the final QC decision now.' : 'Select a finding to see the evidence highlighted on both documents.')

  const review = useMutation({
    mutationFn: (v: { id: number; body: any }) => api.post(`/api/findings/${v.id}/review`, v.body),
    onSuccess: refresh, onError: (e: any) => toast('error', e.message),
  })
  const act = (action: string, f: Finding) => {
    setSel(f.id)
    if (['edit', 'escalate', 'reject', 'not_applicable'].includes(action)) setDialog({ action, f })
    else review.mutate({ id: f.id, body: { action } })
  }
  const rerun = useMutation({ mutationFn: () => api.post(`/api/forms/${id}/qc/run`), onSuccess: () => { setRunId(undefined); toast('ok', 'Analysis started'); refresh() }, onError: (e: any) => toast('error', e.message) })

  if (q.isLoading) return <div className="grid gap-3 lg:grid-cols-3"><Skeleton className="h-[70vh]" /><Skeleton className="h-[70vh]" /><Skeleton className="h-[70vh]" /></div>
  if (q.isError) return <Alert kind="error" title="QC workspace unavailable">{(q.error as Error).message}</Alert>
  const canReview = w.can_review && !w.policy.self_review_blocked
  const raw = w.raw_document, edited = w.edited_document
  const rawBoxes = active ? active.raw_boxes || [] : aiPicked.flatMap((e) => e.raw_boxes || [])
  const edBoxes = active ? active.edited_boxes || [] : aiPicked.flatMap((e) => e.edited_boxes || [])

  const rawPanel = raw ? (
    <section className="flex min-h-0 flex-col" aria-label="RAW document">
      <h2 className="mb-2 flex items-center gap-2 text-sm font-bold"><FileText size={15} className="text-cyan" /> RAW · {raw.logical_name}</h2>
      <div className="min-h-[420px] flex-1"><PdfViewer docId={raw.id} pageCount={raw.page_count || 1} page={rawPage} onPage={setRawPage} boxes={rawBoxes} kind="raw" /></div>
      <div className="mt-2"><TextPanel docId={raw.id} page={rawPage} /></div>
    </section>
  ) : <Alert kind="warn">RAW document missing.</Alert>
  const edPanel = edited ? (
    <section className="flex min-h-0 flex-col" aria-label="Edited document">
      <h2 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-bold"><FileText size={15} className="text-violet" /> Edited · v{edited.version_no}
        {w.edited_versions.length > 1 && <select className="input !w-auto !py-0.5 text-xs" value={run?.id ?? ''} onChange={(e) => setRunId(+e.target.value)} aria-label="Analysis run">{w.runs.map((r: any) => <option key={r.id} value={r.id}>Attempt {r.attempt_no} · run #{r.id}</option>)}</select>}</h2>
      <div className="min-h-[420px] flex-1"><PdfViewer docId={edited.id} pageCount={edited.page_count || 1} page={edPage} onPage={setEdPage} boxes={edBoxes} kind="edited" /></div>
      <div className="mt-2"><TextPanel docId={edited.id} page={edPage} /></div>
    </section>
  ) : <Alert kind="warn" title="No edited PDF yet">Waiting for the editor to submit FORMID_2.pdf.</Alert>

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        {embedded ? <h2 className="text-lg font-extrabold">QC workspace <span className="text-sm font-normal text-muted">· attempt {run?.attempt_no ?? '—'}</span></h2> : <>
          <Link to={`/app/forms/${id}`} className="btn-ghost !px-2.5" aria-label="Back to form"><ArrowLeft size={16} /></Link>
          <h1 className="text-xl font-extrabold">{w.form.form_code}</h1><StatusBadge status={w.form.status} /><span className="text-sm text-muted">{w.form.path}</span></>}
        <div className="ml-auto flex flex-wrap gap-2">
          {can('qc.report') && edited && <button className="btn-ghost" onClick={() => download(`/api/forms/${id}/qc-report`, `QC_Report_${w.form.form_code}.pdf`).then(() => toast('ok', 'QC report downloaded')).catch((e) => toast('error', e.message))}><Download size={15} /> QC report (PDF)</button>}
          {!running && edited && <button className="btn-ghost" onClick={() => rerun.mutate()} disabled={rerun.isPending}><ScanSearch size={15} /> Re-run analysis</button>}
          {canReview && <button className="btn-ghost" onClick={() => setManual('')}><Plus size={15} /> Add finding</button>}
        </div>
      </div>
      {w.policy.self_review_blocked && <div className="mb-3"><Alert kind="warn" title="Independent review policy">You edited or submitted this form, so you can view it but cannot review its findings.</Alert></div>}
      {!w.is_current_run && run && <div className="mb-3"><Alert kind="info" title="Viewing an earlier analysis">This is a historical run — it is read-only. Decisions apply to the latest submission only.</Alert></div>}
      {w.form.status !== 'qc_review' && <div className="mb-3"><Alert kind="info">The form is <b>{w.form.status.replace(/_/g, ' ')}</b>; findings are read-only outside QC Review.</Alert></div>}

      <div className="mb-3 lg:hidden"><Tabs value={mobileTab} onChange={setMobileTab} tabs={[{ key: 'raw', label: 'RAW' }, { key: 'findings', label: 'Findings', count: findings.length }, { key: 'edited', label: 'Edited' }]} /></div>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)_minmax(0,1fr)]">
        <div className={clsx(mobileTab !== 'raw' && 'hidden lg:block')}>{rawPanel}</div>

        <section className={clsx('flex min-h-0 flex-col', mobileTab !== 'findings' && 'hidden lg:flex')} aria-label="AI QC findings">
          <h2 className="mb-2 flex items-center justify-between text-sm font-bold"><span>QC findings <span className="text-muted">({findings.length})</span></span>
            <span className="flex flex-wrap justify-end gap-1 text-[11px] font-semibold">{w.score && <Badge t={w.score.score >= 95 ? 'ok' : w.score.score >= 80 ? 'warn' : 'danger'}>QC score {w.score.score}/{w.score.total}</Badge>}{w.score && w.score.automated < w.score.score && <Badge t="warn">Auto-check {w.score.automated}/{w.score.total}</Badge>}<Badge t="muted">{counts.pending} to review</Badge><Badge t="ok">{counts.accepted} accepted</Badge><Badge t="danger">{counts.rejected} rejected</Badge>{counts.escalated > 0 && <Badge t="warn">{counts.escalated} escalated</Badge>}</span></h2>
          {aiForDoc && <div className="mb-2"><AiEditPanel edit={aiForDoc} defaultOpen selected={active ? null : aiSel} onSelect={(i) => { setSel(null); setAiSel(i); if (typeof i === 'number') { const e = aiForDoc.edits[i]; if (e.page) { setRawPage(e.page); setEdPage(e.page) } } }} /></div>}
          {run && (
            <details className="card mb-2 p-3 text-xs" open={running || run.status === 'failed'}>
              <summary className="flex cursor-pointer items-center justify-between font-semibold"><span>Analysis · {run.status.replace(/_/g, ' ')}</span><ChevronDown size={14} /></summary>
              <div className="mt-3"><RunProgress run={run} /></div>
              {run.error && <div className="mt-2"><Alert kind="error">{run.error}</Alert></div>}
              {run.ai_status !== 'completed' && !running && <div className="mt-2"><Alert kind="warn" title={`AI analysis ${run.ai_status.replace(/_/g, ' ')}`}>{run.ai_message}</Alert></div>}
              {run.stats?.warnings?.filter((x: string) => !x.startsWith('AI analysis')).map((x: string, i: number) => <div key={i} className="mt-2"><Alert kind="warn">{x}</Alert></div>)}
              <p className="mt-2 text-muted">Rules applied ({run.rule_versions.length}): {run.rule_versions.map((r: any) => `${r.rule_key} v${r.version}`).join(' · ') || '—'}</p>
              {run.ai_model && <p className="text-muted">AI model: {run.ai_provider} / {run.ai_model}</p>}
            </details>
          )}
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {(['all', 'pending', 'accepted', 'rejected'] as Filter[]).map((x) => <button key={x} onClick={() => setFilter(x)} className={clsx('rounded-full border px-2.5 py-0.5 text-xs font-semibold capitalize', filter === x ? 'border-brand bg-brand/20' : 'text-muted')}>{x}</button>)}
            <select className="input !w-auto !py-0.5 text-xs" value={sev} onChange={(e) => setSev(e.target.value)} aria-label="Severity filter"><option value="">All severities</option>{Object.entries(SEVERITY).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
          </div>
          <div className="max-h-[calc(100vh-18rem)] min-h-[300px] flex-1 space-y-2 overflow-y-auto pr-1">
            {running && Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20" />)}
            {!running && findings.length === 0 && run && run.status !== 'failed' && <Alert kind="ok" title="No issues detected by the automated checks">Reviewers can still add findings manually. Automated checks are not proof the report is perfect.</Alert>}
            {!running && shown.length === 0 && findings.length > 0 && <p className="p-6 text-center text-sm text-muted">No findings match this filter.</p>}
            {shown.map((f) => <FindingCard key={f.id} f={f} active={sel === f.id} onSelect={() => { setAiSel(null); setSel(sel === f.id ? null : f.id) }} canReview={canReview} onAction={act} />)}
          </div>
          {w.attempts.length > 0 && (
            <details className="card mt-2 p-3 text-xs"><summary className="cursor-pointer font-semibold">Previous review attempts ({w.attempts.length})</summary>
              <ul className="mt-2 space-y-1.5">{w.attempts.map((a: any) => <li key={a.id}><b>#{a.attempt_no}</b> {fmtDate(a.decided_at)} · {a.reviewer?.name} · <Badge t={a.decision === 'approved' ? 'ok' : 'warn'}>{a.decision.replace(/_/g, ' ')}</Badge> · {a.accepted_count} confirmed / {a.rejected_count} rejected{a.feedback_to_editor && <span className="block text-muted">“{a.feedback_to_editor}”</span>}</li>)}</ul></details>
          )}
        </section>

        <div className={clsx(mobileTab !== 'edited' && 'hidden lg:block')}>
          {edPanel}
          {active && canReview && (
            <div className="card mt-3 p-3"><p className="mb-2 text-xs font-semibold text-muted">Reviewer actions for the selected finding</p>
              <div className="flex flex-wrap gap-1.5">
                <button className="btn-ok !px-2.5 !py-1 text-xs" onClick={() => act('accept', active)}><Check size={13} /> Accept</button>
                <button className="btn-danger !px-2.5 !py-1 text-xs" onClick={() => act('reject', active)}><X size={13} /> Reject</button>
                <button className="btn-ghost !px-2.5 !py-1 text-xs" onClick={() => act('edit', active)}><Pencil size={13} /> Edit</button>
                <button className="btn-ghost !px-2.5 !py-1 text-xs" onClick={() => act('not_applicable', active)}>Not applicable</button>
                <button className="btn-ghost !px-2.5 !py-1 text-xs" onClick={() => act('escalate', active)}><Flag size={13} /> Escalate</button></div></div>
          )}
        </div>
      </div>

      {dialog && <ReviewDialog dialog={dialog} onClose={() => setDialog(null)} onSubmit={(body) => review.mutate({ id: dialog.f.id, body: { action: dialog.action, ...body } }, { onSuccess: () => setDialog(null) })} busy={review.isPending} />}
      {manual !== false && <ManualDialog initial={manual} onClose={() => setManual(false)} formId={id!} onDone={() => { setManual(false); refresh() }} />}
    </div>
  )
}

function ReviewDialog({ dialog, onClose, onSubmit, busy }: { dialog: { action: string; f: Finding }; onClose: () => void; onSubmit: (b: any) => void; busy: boolean }) {
  const { action, f } = dialog
  const [comment, setComment] = useState(f.reviewer_comment || '')
  const [title, setTitle] = useState(f.title)
  const [desc, setDesc] = useState(f.description)
  const [sev, setSev] = useState<string>(f.severity)
  const [fix, setFix] = useState(f.suggested_correction || '')
  const titles: Record<string, string> = { edit: 'Edit finding', escalate: 'Escalate finding', reject: 'Reject finding', not_applicable: 'Mark as not applicable' }
  const need = action === 'escalate'
  return (
    <Modal open onClose={onClose} title={titles[action]} wide={action === 'edit'}>
      <div className="space-y-3">
        {action === 'edit' && <>
          <Field label="Title"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={250} /></Field>
          <Field label="Description"><textarea className="input min-h-20" value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
          <div className="grid grid-cols-2 gap-3"><Field label="Severity"><select className="input" value={sev} onChange={(e) => setSev(e.target.value)}>{Object.keys(SEVERITY).map((s) => <option key={s}>{s}</option>)}</select></Field></div>
          <Field label="Recommended correction"><textarea className="input min-h-16" value={fix} onChange={(e) => setFix(e.target.value)} /></Field>
          <p className="text-xs text-muted">The original system text is preserved in the audit record. Evidence (excerpts and pages) cannot be edited.</p></>}
        <Field label={need ? 'Reason (required)' : 'Reviewer comment (optional)'}><textarea className="input min-h-16" value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} placeholder={action === 'reject' ? 'Why is this a false positive?' : ''} /></Field>
        <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || (need && comment.trim().length < 3)} onClick={() => onSubmit(action === 'edit' ? { comment, title, description: desc, severity: sev, suggested_correction: fix } : { comment })}>Save</button></div>
      </div>
    </Modal>
  )
}

function ManualDialog({ onClose, formId, onDone, initial }: { onClose: () => void; formId: string; onDone: () => void; initial?: string }) {
  const toast = useToast()
  const table = useErrorTable()
  const cats = table.data?.categories || []
  const first = cats[0]?.subtypes[0]?.key || 'punctuation'
  const [b, setB] = useState({ error_subtype: initial || '', title: '', description: '', raw_excerpt: '', edited_excerpt: '', suggested_correction: '' })
  const subKey = b.error_subtype || first
  const cat = cats.find((c) => c.subtypes.some((x) => x.key === subKey))
  const sub = cat?.subtypes.find((x) => x.key === subKey)
  const m = useMutation({
    mutationFn: () => api.post(`/api/forms/${formId}/findings`, { ...b, error_subtype: subKey, raw_excerpt: b.raw_excerpt || null, edited_excerpt: b.edited_excerpt || null, suggested_correction: b.suggested_correction || null }),
    onSuccess: () => { toast('ok', 'Finding added'); onDone() }, onError: (e: any) => toast('error', e.message),
  })
  const set = (k: string, v: string) => setB({ ...b, [k]: v })
  const hasQuote = !!(b.raw_excerpt || b.edited_excerpt)
  const ok = b.title.trim().length >= 3 && (hasQuote || (!!sub?.manual_only && b.description.trim().length > 0))
  return (
    <Modal open onClose={onClose} title="Add a finding" wide>
      <div className="space-y-3">
        <Alert kind="info">QC only uses the fixed error table. Quote the evidence exactly as it appears in the extracted text — quotes that cannot be found are rejected. Video, audio, call-attempt and picture checks cannot be quoted: describe what you found instead.</Alert>
        <Field label="Error (from the QC error table)"><select className="input" value={subKey} onChange={(e) => set('error_subtype', e.target.value)}>
          {cats.map((c) => <optgroup key={c.key} label={`${c.label} · −${c.points}`}>{c.subtypes.map((x) => <option key={x.key} value={x.key}>{x.label}{x.manual_only ? ' (video / audio / picture)' : ''}</option>)}</optgroup>)}</select></Field>
        {cat && <p className="-mt-1 text-xs text-muted">{cat.label}: {cat.points} marks deducted (score {cat.score_after}). {sub?.hint}</p>}
        <Field label="Title"><input className="input" value={b.title} onChange={(e) => set('title', e.target.value)} maxLength={250} /></Field>
        <Field label="Description"><textarea className="input min-h-16" value={b.description} onChange={(e) => set('description', e.target.value)} /></Field>
        <div className="grid gap-3 sm:grid-cols-2"><Field label="RAW quote (optional)"><textarea className="input min-h-16 font-mono text-xs" value={b.raw_excerpt} onChange={(e) => set('raw_excerpt', e.target.value)} /></Field>
          <Field label="Edited quote (optional)"><textarea className="input min-h-16 font-mono text-xs" value={b.edited_excerpt} onChange={(e) => set('edited_excerpt', e.target.value)} /></Field></div>
        <Field label="Recommended correction"><input className="input" value={b.suggested_correction} onChange={(e) => set('suggested_correction', e.target.value)} /></Field>
        <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={m.isPending || !ok} onClick={() => m.mutate()}>Add finding</button></div>
      </div>
    </Modal>
  )
}
