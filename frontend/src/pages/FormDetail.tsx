import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import QCWorkspace from './QCWorkspace'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Bot, CheckCircle2, Download, CircleDashed, FileText, Loader2, MessageSquarePlus, Play, ScanSearch, ShieldAlert, Upload, XCircle } from 'lucide-react'
import { api, download } from '../lib/api'
import { ago, bytes, fmtDate, ROLE_LABEL, TYPE_LABEL } from '../lib/format'
import { Alert, Badge, Field, Modal, PageHeader, SeverityBadge, Skeleton, StatusBadge, ReviewBadge, useToast } from '../components/ui'
import { PipelineDiagram } from '../components/Illustrations'
import { AiEditPanel } from '../components/AiEditPanel'
import type { AutoEdit } from '../lib/types'
import { useAuth } from '../lib/auth'
import { useCopilotHint } from '../lib/copilot'

const STAGE_LABEL: Record<string, string> = { validate: 'Validate pair', extract: 'Extract text / OCR', compare: 'Compare RAW vs edited', rules: 'Apply versioned rules', ai: 'AI contextual analysis', persist: 'Save findings' }
const STAGES = Object.keys(STAGE_LABEL)

export function RunProgress({ run }: { run: any }) {
  const byName = Object.fromEntries((run.stages || []).map((s: any) => [s.name, s]))
  const idx = run.status === 'completed' || run.status === 'completed_with_warnings' ? 3 : Math.max(0, STAGES.findIndex((s) => byName[s]?.status === 'running'))
  const active = run.status === 'queued' || run.status === 'running' ? (idx <= 1 ? 1 : 2) : -1
  return (
    <div>
      <div className="mb-3 hidden sm:block"><PipelineDiagram active={active} compact /></div>
      <ol className="space-y-1.5">
        {STAGES.map((s) => {
          const st = byName[s]
          const status = st?.status || (run.status === 'failed' ? 'skipped' : 'waiting')
          return (
            <li key={s} className="flex items-start gap-2 text-sm">
              {status === 'done' ? <CheckCircle2 size={16} className="mt-0.5 text-ok" /> : status === 'running' ? <Loader2 size={16} className="mt-0.5 animate-spin text-brand" /> : status === 'failed' ? <XCircle size={16} className="mt-0.5 text-danger" /> : status === 'skipped' ? <ShieldAlert size={16} className="mt-0.5 text-warn" /> : <CircleDashed size={16} className="mt-0.5 text-muted" />}
              <div><span className={status === 'waiting' ? 'text-muted' : 'font-semibold'}>{STAGE_LABEL[s]}</span>{st?.detail && <span className="block text-xs text-muted">{st.detail}</span>}</div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function ReasonModal({ title, label, open, onClose, onSubmit, busy, danger }: { title: string; label: string; open: boolean; onClose: () => void; onSubmit: (r: string) => void; busy?: boolean; danger?: boolean }) {
  const [r, setR] = useState('')
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <Field label={label}><textarea className="input min-h-24" value={r} onChange={(e) => setR(e.target.value)} maxLength={1500} /></Field>
      <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className={danger ? 'btn-danger' : 'btn-primary'} disabled={busy || r.trim().length < 3} onClick={() => onSubmit(r.trim())}>Confirm</button></div>
    </Modal>
  )
}

export default function FormDetail() {
  const { id } = useParams()
  const { session, can } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const role = session!.user.role
  const [reason, setReason] = useState<null | 'block' | 'escalate'>(null)
  const [comment, setComment] = useState('')
  const [note, setNote] = useState('')
  const file = useRef<HTMLInputElement>(null)
  const q = useQuery({
    queryKey: ['form', id], queryFn: () => api.get<any>(`/api/forms/${id}`),
    refetchInterval: (query) => (query.state.data?.runs || []).some((r: any) => ['queued', 'running'].includes(r.status)) ? 1500 : false,
  })
  const aiq = useQuery({
    queryKey: ['ai-edits', id], queryFn: () => api.get<AutoEdit[]>(`/api/forms/${id}/ai-edits`), enabled: can('aiedit.view'),
    refetchInterval: (query) => (query.state.data || []).some((a) => ['queued', 'running'].includes(a.status)) ? 1500 : false,
  })
  const aiRun = aiq.data?.[0]
  const aiBusy = !!aiRun && ['queued', 'running'].includes(aiRun.status)
  const f = q.data
  const run = f?.runs?.[0]
  const running = run && ['queued', 'running'].includes(run.status)
  const refresh = () => { qc.invalidateQueries({ queryKey: ['form', id] }); qc.invalidateQueries({ queryKey: ['queue'] }); qc.invalidateQueries({ queryKey: ['repo-forms'] }) }
  useEffect(() => { if (aiRun && !aiBusy) refresh() }, [aiRun?.id, aiRun?.status]) // eslint-disable-line react-hooks/exhaustive-deps
  const act = (fn: () => Promise<any>, ok: string) => fn().then(() => { toast('ok', ok); refresh() }).catch((e) => toast('error', e.message))

  useCopilotHint(aiBusy || running ? 'scanning' : run?.status === 'failed' ? 'alert' : f?.status === 'approved' ? 'celebrate' : 'idle',
    aiBusy ? 'AI editor at work: fixing spelling and grammar, leaving numbers, names and facts untouched. Then it goes to QC.' : running ? 'Comparing the RAW and edited PDFs… extracting text, applying rules, looking for missed and new errors.' : run?.status === 'failed' ? 'The analysis could not finish. Details are shown on this page — nothing was approved.' : f?.status === 'approved' ? 'Approved! A human reviewer validated every finding.' : f?.status === 're_edit_required' ? 'QC feedback is ready. Fix the confirmed items and upload a revised PDF.' : 'This page shows the full history of the form: documents, attempts, feedback and assignments.')

  const upload = useMutation({
    mutationFn: (file: File) => { const fd = new FormData(); fd.set('project_id', String(f.project_id)); fd.append('files', file); fd.set('form_code', f.form_code); fd.set('kind', 'edited'); if (note) fd.set('note', note); return api.upload<any>('/api/repository/upload', fd) },
    onSuccess: (r) => { r.failed ? toast('error', r.results[0].error) : (toast('ok', 'Revised PDF submitted — QC analysis started'), setNote('')); refresh() }, onError: (e: any) => toast('error', e.message),
  })
  const addComment = useMutation({ mutationFn: () => api.post(`/api/forms/${id}/comments`, { body: comment }), onSuccess: () => { setComment(''); refresh() }, onError: (e: any) => toast('error', e.message) })

  const timeline = useMemo(() => {
    if (!f) return []
    const ev: { at: string; kind: string; node: React.ReactNode }[] = []
    f.attempts.forEach((a: any) => ev.push({ at: a.decided_at, kind: 'decision', node: <><b>Review attempt {a.attempt_no}</b> by {a.reviewer?.name || 'reviewer'}: <Badge t={a.decision === 'approved' ? 'ok' : 'warn'}>{a.decision.replace(/_/g, ' ')}</Badge> <span className="text-muted">({a.accepted_count} confirmed, {a.rejected_count} rejected)</span>{a.summary && <p className="text-muted">{a.summary}</p>}</> }))
    f.comments.forEach((c: any) => ev.push({ at: c.created_at, kind: c.kind, node: <><b>{c.author?.name || 'System'}</b> <Badge t={c.kind === 'feedback' ? 'warn' : c.kind === 'escalation' ? 'danger' : 'muted'}>{c.kind}</Badge><p className="whitespace-pre-wrap text-ink/90">{c.body}</p></> }))
    f.assignments.forEach((a: any) => ev.push({ at: a.assigned_at, kind: 'assign', node: <>{a.assigned_by?.name || 'Someone'} assigned <b>{a.assignee.name}</b> as {a.role_kind}{a.note ? ` — ${a.note}` : ''}{!a.active && <span className="text-muted"> (ended)</span>}</> }))
    f.documents.forEach((d: any) => ev.push({ at: d.uploaded_at, kind: 'doc', node: <>Uploaded <b>{d.stored_name}</b> ({d.kind === 'raw' ? 'RAW' : `edited v${d.version_no}`})</> }))
    return ev.sort((a, b) => b.at.localeCompare(a.at))
  }, [f])

  if (q.isLoading) return <div className="space-y-3"><Skeleton className="h-16" /><Skeleton className="h-64" /></div>
  if (q.isError) return <Alert kind="error" title="Form unavailable">{(q.error as Error).message}</Alert>

  const isEditorRole = role === 'editor' || role === 'manager'
  const canUploadEdited = can('docs.upload_edited') && ['pending', 'in_progress', 're_edit_required'].includes(f.status) && f.raw_document_id && (role !== 'editor' || f.assigned_editor?.id === session!.user.id)
  const canStart = can('workflow.start') && ['pending', 're_edit_required'].includes(f.status)
  const raw = f.documents.find((d: any) => d.kind === 'raw')
  const feedback = [...f.comments].reverse().find((c: any) => c.kind === 'feedback')

  return (
    <div>
      <PageHeader title={f.form_code} sub={<>{f.logical_path}</>} actions={
        <>
          <StatusBadge status={f.status} />{f.is_sample && <Badge t="warn">SAMPLE</Badge>}
          {can('docs.ai_edit') && ['pending', 'in_progress', 're_edit_required'].includes(f.status) && f.raw_document_id && !aiBusy && (
            <button className="btn-primary" onClick={() => act(() => api.post(`/api/forms/${id}/ai-edit`), 'AI editor started')}><Bot size={15} /> {f.status === 're_edit_required' ? 'Re-run AI edit' : 'Generate AI edit'}</button>)}
          {canStart && <button className="btn-primary" onClick={() => act(() => api.post(`/api/forms/${id}/start`), 'Editing started')}><Play size={15} /> Start editing</button>}
          {can('qc.report') && f.latest_edited_document_id && <button className="btn-ghost" onClick={() => download(`/api/forms/${id}/qc-report`, `QC_Report_${f.form_code}.pdf`).then(() => toast('ok', 'QC report downloaded')).catch((e) => toast('error', e.message))}><Download size={15} /> QC report</button>}
        </>} />
      {f.status_reason && ['blocked', 'manual_review_required', 're_edit_required'].includes(f.status) && <div className="mb-4"><Alert kind="warn" title={f.status === 'blocked' ? 'Blocked by missing or invalid input' : f.status === 'manual_review_required' ? 'Manual review required' : 'Re-edit required'}>{f.status_reason}</Alert></div>}

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          {role === 'editor' && (feedback || f.confirmed_findings?.length > 0) && (
            <div className="card border-warn/40 p-4">
              <h2 className="font-bold">QC feedback for you</h2>
              {feedback && <p className="mt-2 whitespace-pre-wrap text-sm">{feedback.body}</p>}
              {f.confirmed_findings?.length > 0 && <ul className="mt-3 space-y-2">{f.confirmed_findings.map((c: any) => (
                <li key={c.id} className="rounded-xl border bg-surface2/50 p-3 text-sm"><div className="flex flex-wrap items-center gap-2"><SeverityBadge s={c.severity} /><b>{c.title}</b><span className="text-xs text-muted">{c.category_label ? `${c.category_label} › ${c.error_label}` : (TYPE_LABEL[c.type] || c.type)}{c.points ? ` · −${c.points}` : ''}{c.edited_page ? ` · page ${c.edited_page}` : ''}</span></div>
                  {c.edited_excerpt && <p className="mt-1.5 rounded bg-warn/10 px-2 py-1 font-mono text-xs">“{c.edited_excerpt}”</p>}
                  {c.suggested_correction && <p className="mt-1.5 text-ok">Suggested: {c.suggested_correction}</p>}{c.reviewer_comment && <p className="mt-1 text-xs text-muted">Reviewer: {c.reviewer_comment}</p>}</li>))}</ul>}
            </div>
          )}

          {(canUploadEdited) && (
            <div className="card p-4">
              <h2 className="font-bold">{f.edited_versions ? 'Submit a revised PDF' : 'Submit edited PDF'}</h2>
              <p className="mt-1 text-sm text-muted">Edit the report outside the QC tool, then upload the result here. It is stored as {f.form_code}_2{f.edited_versions ? ` (v${f.edited_versions + 1}, timestamped)` : ''} and the RAW file is never modified.</p>
              <div className="mt-3 flex flex-wrap items-end gap-3"><div className="min-w-60 flex-1"><Field label="Note to reviewer (optional)"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></Field></div>
                <input ref={file} type="file" accept="application/pdf" hidden onChange={(e) => { const fl = e.target.files?.[0]; if (fl) upload.mutate(fl); e.target.value = '' }} />
                <button className="btn-primary" disabled={upload.isPending} onClick={() => file.current?.click()}>{upload.isPending ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />} Choose PDF & submit</button></div>
            </div>
          )}

          {aiRun && can('aiedit.view') && <AiEditPanel edit={aiRun} />}

          <div className="card p-4">
            <h2 className="mb-3 font-bold">Documents</h2>
            <div className="overflow-x-auto"><table className="w-full min-w-[600px]"><thead><tr className="border-b"><th className="th">File</th><th className="th">Type</th><th className="th">Pages</th><th className="th">Extraction</th><th className="th">Uploaded</th><th className="th">SHA-256</th></tr></thead>
              <tbody>{f.documents.map((d: any) => (
                <tr key={d.id} className="border-b last:border-0"><td className="td"><FileText size={14} className="mr-1.5 inline text-brand" />{can('docs.view') ? <a className="font-semibold text-brand hover:underline" href={`/api/documents/${d.id}/file`} target="_blank" rel="noreferrer">{d.stored_name}</a> : d.stored_name}<span className="block text-xs text-muted">{bytes(d.size_bytes)}</span></td>
                  <td className="td">{d.kind === 'raw' ? <Badge t="cyan">RAW · immutable</Badge> : <><Badge t="violet">Edited v{d.version_no}</Badge>{d.origin === 'ai_assisted' && <Badge t="brand" className="ml-1"><Bot size={10} /> AI-edited</Badge>}</>}</td><td className="td tabular-nums">{d.page_count}</td>
                  <td className="td"><Badge t={d.extraction_status === 'ok' ? 'ok' : d.extraction_status === 'pending' ? 'muted' : 'warn'}>{d.extraction_status.replace(/_/g, ' ')}</Badge></td>
                  <td className="td text-muted">{fmtDate(d.uploaded_at)}</td><td className="td font-mono text-[11px] text-muted" title={d.sha256}>{d.sha256.slice(0, 12)}…</td></tr>))}</tbody></table></div>
            {f.pair_status !== 'complete' && !aiBusy && <div className="mt-3"><Alert kind="warn" title="Incomplete pair">{f.pair_status === 'missing_edited' ? `Awaiting ${f.form_code}_2.pdf from the editor.` : `${f.form_code}_1.pdf (RAW) is missing.`}</Alert></div>}
          </div>

          {run && can('qc.view') && role === 'editor' && (
            <div className="card p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h2 className="font-bold">Latest QC analysis <span className="text-sm font-normal text-muted">· attempt {run.attempt_no} · {ago(run.finished_at || run.created_at)}</span></h2>
                <Badge t={run.status === 'completed' ? 'ok' : run.status === 'failed' ? 'danger' : run.status === 'completed_with_warnings' ? 'warn' : 'brand'}>{run.status.replace(/_/g, ' ')}</Badge></div>
              <RunProgress run={run} />
              {run.error && <div className="mt-3"><Alert kind="error" title="Analysis did not complete">{run.error}</Alert></div>}
              {run.ai_status !== 'completed' && !running && <div className="mt-3"><Alert kind="warn" title={`AI analysis: ${run.ai_status.replace(/_/g, ' ')}`}>{run.ai_message || 'AI analysis was not performed.'}</Alert></div>}
            </div>
          )}
        </div>

        <div className="space-y-4">
          <div className="card p-4 text-sm">
            <h2 className="mb-2 font-bold">Details</h2>
            <dl className="grid grid-cols-[110px_1fr] gap-y-2"><dt className="text-muted">Client</dt><dd>{f.client}</dd><dt className="text-muted">Project</dt><dd>{f.project}</dd><dt className="text-muted">Editor</dt><dd>{f.assigned_editor?.name || '—'}</dd>
              <dt className="text-muted">QC reviewer</dt><dd>{f.assigned_reviewer?.name || '—'}</dd><dt className="text-muted">Due</dt><dd>{fmtDate(f.due_at, false)}</dd><dt className="text-muted">Intake</dt><dd>{fmtDate(f.created_at)}</dd><dt className="text-muted">Attempts</dt><dd>{f.attempts.length} review · {f.edited_versions} submission(s)</dd></dl>
            <div className="mt-4 flex flex-wrap gap-2">
              {can('workflow.block') && !['approved', 'closed', 'blocked'].includes(f.status) && <button className="btn-danger !py-1.5 text-xs" onClick={() => setReason('block')}>Block (missing input)</button>}
              {can('workflow.block') && f.status === 'blocked' && <button className="btn-ok !py-1.5 text-xs" onClick={() => act(() => api.post(`/api/forms/${id}/status`, { action: 'unblock' }), 'Form unblocked')}>Unblock</button>}
              {can('workflow.escalate') && f.status === 'qc_review' && <button className="btn-ghost !py-1.5 text-xs" onClick={() => setReason('escalate')}>Escalate dispute</button>}
              {can('workflow.resolve_manual') && f.status === 'manual_review_required' && <button className="btn-ok !py-1.5 text-xs" onClick={() => act(() => api.post(`/api/forms/${id}/resolve-manual`), 'Returned to QC review')}>Return to QC review</button>}
              {can('workflow.close') && f.status === 'approved' && <button className="btn-ok !py-1.5 text-xs" onClick={() => act(() => api.post(`/api/forms/${id}/status`, { action: 'close' }), 'Form closed')}>Close form</button>}
              {can('qc.run') && f.edited_versions > 0 && !running && <button className="btn-ghost !py-1.5 text-xs" onClick={() => act(() => api.post(`/api/forms/${id}/qc/run`), 'Re-analysis started')}>Re-run analysis</button>}
            </div>
          </div>
          <div className="card p-4">
            <h2 className="mb-3 font-bold">History</h2>
            {can('comments.add') && <div className="mb-4 flex gap-2"><input className="input" placeholder="Add a comment…" value={comment} onChange={(e) => setComment(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && comment.trim() && addComment.mutate()} maxLength={2000} /><button className="btn-ghost" disabled={!comment.trim() || addComment.isPending} onClick={() => addComment.mutate()} aria-label="Post comment"><MessageSquarePlus size={16} /></button></div>}
            <ol className="relative space-y-4 border-l pl-4">
              {timeline.map((e, i) => (
                <motion.li key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(i * 0.03, 0.4) }} className="relative text-sm">
                  <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full bg-gradient-to-br from-brand to-violet" /><p className="mb-0.5 text-[11px] text-muted">{fmtDate(e.at)}</p>{e.node}</motion.li>))}
            </ol>
          </div>
        </div>
      </div>
      {run && can('qc.view') && role !== 'editor' && f.raw_document_id && <div className="mt-4"><QCWorkspace formId={id} embedded /></div>}
      <ReasonModal open={reason === 'block'} onClose={() => setReason(null)} title="Block form" label="What input is missing or invalid?" danger onSubmit={(r) => { setReason(null); act(() => api.post(`/api/forms/${id}/status`, { action: 'block', reason: r }), 'Form blocked') }} />
      <ReasonModal open={reason === 'escalate'} onClose={() => setReason(null)} title="Escalate disputed case" label="Why does this need manual review?" onSubmit={(r) => { setReason(null); act(() => api.post(`/api/forms/${id}/escalate`, { reason: r }), 'Escalated for manual review') }} />
    </div>
  )
}
