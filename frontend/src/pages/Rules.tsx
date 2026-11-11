import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpenCheck, FilePlus2, History, Send, ShieldCheck } from 'lucide-react'
import { api } from '../lib/api'
import { fmtDate } from '../lib/format'
import { Alert, Badge, Field, Modal, PageHeader, SeverityBadge, SkeletonRows, useToast } from '../components/ui'
import { EmptyState } from '../components/Illustrations'
import { useAuth } from '../lib/auth'
import { useErrorTable } from '../lib/errorTable'
import { useCopilotHint } from '../lib/copilot'

const STATUS_TONE: Record<string, string> = { draft: 'muted', pending_approval: 'warn', published: 'ok', rejected: 'danger', retired: 'muted' }
const CHECKS = [
  ['ai_guideline', 'AI guideline (natural-language rule, needs AI)'], ['forbidden_terms', 'Forbidden terms'], ['regex_forbidden', 'Forbidden pattern (regex)'],
  ['required_labels', 'Required fields / labels'], ['max_sentence_words', 'Maximum sentence length'],
]

function configFrom(type: string, v: Record<string, string>) {
  const list = (s: string) => s.split(/\n|,/).map((x) => x.trim()).filter(Boolean)
  if (type === 'forbidden_terms') return { terms: list(v.terms || ''), suggestion: v.suggestion || undefined }
  if (type === 'regex_forbidden') return { pattern: v.pattern || '', message: v.message || undefined, suggestion: v.suggestion || undefined }
  if (type === 'required_labels') return { labels: list(v.labels || '') }
  if (type === 'max_sentence_words') return { max: Number(v.max || 40) }
  return {}
}
function valuesFrom(type: string, c: any): Record<string, string> {
  return { terms: (c.terms || []).join('\n'), suggestion: c.suggestion || '', pattern: c.pattern || '', message: c.message || '', labels: (c.labels || []).join('\n'), max: String(c.max || 40) }
}

function RuleForm({ base, onClose }: { base?: any; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const clients = useQuery({ queryKey: ['clients'], queryFn: () => api.get<any[]>('/api/clients') })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get<any[]>('/api/projects') })
  const table = useErrorTable()
  const isNewVersion = !!base
  const builtin = base?.check_type === 'builtin'
  const [f, setF] = useState({
    rule_key: '', name: base?.name || '', description: base?.description || '', error_subtype: base?.error_subtype || 'terminology', severity: base?.severity || 'minor',
    client_id: base?.client_id ? String(base.client_id) : '', project_id: base?.project_id ? String(base.project_id) : '', form_type: base?.form_type || '',
    check_type: base?.check_type || 'ai_guideline', examples: base?.examples || '', exceptions: base?.exceptions || '', effective_date: base?.effective_date || new Date().toISOString().slice(0, 10), change_note: '',
  })
  const [cv, setCv] = useState(valuesFrom(f.check_type, base?.config || {}))
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  const m = useMutation({
    mutationFn: () => {
      const body: any = { name: f.name, description: f.description, error_subtype: f.error_subtype, severity: f.severity, client_id: f.client_id ? +f.client_id : null, project_id: f.project_id ? +f.project_id : null,
        form_type: f.form_type || null, check_type: f.check_type, config: builtin ? base.config : configFrom(f.check_type, cv), examples: f.examples || null, exceptions: f.exceptions || null, effective_date: f.effective_date }
      return isNewVersion ? api.post(`/api/rules/${base.rule_id}/versions`, { ...body, change_note: f.change_note }) : api.post('/api/rules', { ...body, rule_key: f.rule_key || null, change_note: f.change_note || 'Initial version' })
    },
    onSuccess: () => { toast('ok', isNewVersion ? 'New draft version created' : 'Draft rule created'); qc.invalidateQueries({ queryKey: ['rules'] }); onClose() }, onError: (e: any) => toast('error', e.message),
  })
  return (
    <Modal open onClose={onClose} title={isNewVersion ? `New version of ${base.rule_key}` : 'New QC rule'} wide>
      <div className="space-y-3">
        {!isNewVersion && <Field label="Rule key (optional)" hint="Letters, digits and hyphens, e.g. ACME-TONE"><input className="input" value={f.rule_key} onChange={(e) => set('rule_key', e.target.value.toUpperCase())} /></Field>}
        <Field label="Rule name"><input className="input" value={f.name} onChange={(e) => set('name', e.target.value)} maxLength={200} /></Field>
        <Field label="Description / guideline"><textarea className="input min-h-20" value={f.description} onChange={(e) => set('description', e.target.value)} /></Field>
        <div className="grid gap-3 sm:grid-cols-3"><Field label="QC error (fixed table)" hint="A rule must belong to one error of the QC error table.">
          <select className="input" value={f.error_subtype} onChange={(e) => set('error_subtype', e.target.value)}>{(table.data?.categories || []).map((c) => <optgroup key={c.key} label={`${c.label} · −${c.points}`}>{c.subtypes.filter((x) => !x.manual_only).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}</optgroup>)}</select></Field>
          <Field label="Marks deducted"><input className="input" readOnly value={(() => { const c = table.data?.categories.find((x) => x.subtypes.some((y) => y.key === f.error_subtype)); return c ? `${c.points} (${c.label})` : '—' })()} /></Field>
          <Field label="Effective date"><input type="date" className="input" value={f.effective_date} onChange={(e) => set('effective_date', e.target.value)} /></Field></div>
        <div className="grid gap-3 sm:grid-cols-3"><Field label="Client (blank = all)"><select className="input" value={f.client_id} onChange={(e) => set('client_id', e.target.value)}><option value="">All clients</option>{clients.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
          <Field label="Project (blank = all)"><select className="input" value={f.project_id} onChange={(e) => set('project_id', e.target.value)}><option value="">All projects</option>{projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
          <Field label="Form type (blank = all)"><input className="input" value={f.form_type} onChange={(e) => set('form_type', e.target.value)} placeholder="standard" /></Field></div>
        {builtin ? <Alert kind="info">Built-in check “{base.config.builtin}”. Its logic is part of the platform; you can change severity, scope and wording.</Alert> : <>
          <Field label="How is it checked?"><select className="input" value={f.check_type} onChange={(e) => set('check_type', e.target.value)}>{CHECKS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          {f.check_type === 'forbidden_terms' && <><Field label="Terms (one per line)"><textarea className="input min-h-20" value={cv.terms} onChange={(e) => setCv({ ...cv, terms: e.target.value })} /></Field><Field label="Suggested wording"><input className="input" value={cv.suggestion} onChange={(e) => setCv({ ...cv, suggestion: e.target.value })} /></Field></>}
          {f.check_type === 'regex_forbidden' && <><Field label="Regex pattern" hint="Max 300 chars; nested quantifiers are rejected."><input className="input font-mono" value={cv.pattern} onChange={(e) => setCv({ ...cv, pattern: e.target.value })} /></Field><Field label="Message"><input className="input" value={cv.message} onChange={(e) => setCv({ ...cv, message: e.target.value })} /></Field></>}
          {f.check_type === 'required_labels' && <Field label="Required labels (one per line)" hint="e.g. Store Name — must be followed by a value on the same line."><textarea className="input min-h-20" value={cv.labels} onChange={(e) => setCv({ ...cv, labels: e.target.value })} /></Field>}
          {f.check_type === 'max_sentence_words' && <Field label="Maximum words per sentence"><input type="number" className="input" value={cv.max} onChange={(e) => setCv({ ...cv, max: e.target.value })} /></Field>}
          {f.check_type === 'ai_guideline' && <Alert kind="info">Evaluated by the AI provider when one is configured and approved. Without AI, the run reports that this rule was not evaluated.</Alert>}</>}
        <div className="grid gap-3 sm:grid-cols-2"><Field label="Examples"><textarea className="input min-h-16" value={f.examples} onChange={(e) => set('examples', e.target.value)} /></Field><Field label="Exceptions"><textarea className="input min-h-16" value={f.exceptions} onChange={(e) => set('exceptions', e.target.value)} /></Field></div>
        <Field label={isNewVersion ? 'Change note (required)' : 'Change note'}><input className="input" value={f.change_note} onChange={(e) => set('change_note', e.target.value)} /></Field>
        <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={m.isPending || f.name.trim().length < 3 || (isNewVersion && f.change_note.trim().length < 3)} onClick={() => m.mutate()}>Save as draft</button></div>
      </div>
    </Modal>
  )
}

function RuleDetail({ rule, onClose, onNewVersion }: { rule: any; onClose: () => void; onNewVersion: (r: any) => void }) {
  const { session, can } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const hist = useQuery({ queryKey: ['rule-history', rule.rule_id], queryFn: () => api.get<any[]>(`/api/rules/${rule.rule_id}/history`) })
  const [reject, setReject] = useState('')
  const done = (msg: string) => { toast('ok', msg); qc.invalidateQueries({ queryKey: ['rules'] }); qc.invalidateQueries({ queryKey: ['rule-history'] }); onClose() }
  const submit = useMutation({ mutationFn: (id: number) => api.post(`/api/rules/versions/${id}/submit`), onSuccess: () => done('Submitted for approval'), onError: (e: any) => toast('error', e.message) })
  const decide = useMutation({ mutationFn: (v: { id: number; approve: boolean }) => api.post(`/api/rules/versions/${v.id}/decision`, { approve: v.approve, reason: reject }), onSuccess: (_, v) => done(v.approve ? 'Rule published to production' : 'Rule version rejected'), onError: (e: any) => toast('error', e.message) })
  const latest = hist.data?.[hist.data.length - 1]
  return (
    <Modal open onClose={onClose} title={`${rule.rule_key} — ${rule.name}`} wide>
      <div className="space-y-4 text-sm">
        <p className="text-muted">{rule.description || 'No description.'}</p>
        <div className="flex flex-wrap gap-2"><SeverityBadge s={rule.severity} />{rule.in_qc ? <Badge t="cyan">{rule.category_label}{rule.error_label ? ` › ${rule.error_label}` : ''}</Badge> : <Badge t="warn">Not on the QC error table — not used</Badge>}<Badge t="violet">{rule.scope} scope</Badge><Badge t="muted">{rule.check_type.replace(/_/g, ' ')}</Badge></div>
        {rule.config && Object.keys(rule.config).length > 0 && <pre className="overflow-auto rounded-xl border bg-surface2/50 p-3 text-xs">{JSON.stringify(rule.config, null, 2)}</pre>}
        <div><h3 className="mb-2 flex items-center gap-2 font-bold"><History size={15} /> Revision history</h3>
          <ol className="space-y-2">{(hist.data || []).slice().reverse().map((v) => (
            <li key={v.id} className="rounded-xl border p-3"><div className="flex flex-wrap items-center gap-2"><b>v{v.version}</b><Badge t={STATUS_TONE[v.status]}>{v.status.replace(/_/g, ' ')}</Badge><span className="text-xs text-muted">by {v.created_by_name} · {fmtDate(v.created_at)}{v.approved_by_name && ` · approved by ${v.approved_by_name} ${fmtDate(v.approved_at)}`}</span></div>
              {v.change_note && <p className="mt-1 text-muted">{v.change_note}</p>}{v.rejection_reason && <p className="mt-1 text-danger">Rejected: {v.rejection_reason}</p>}
              <div className="mt-2 flex flex-wrap gap-2">
                {v.status === 'draft' && can('rules.author') && <button className="btn-primary !py-1 text-xs" onClick={() => submit.mutate(v.id)}><Send size={13} /> Submit for approval</button>}
                {v.status === 'pending_approval' && can('rules.approve') && v.created_by !== session!.user.id && <>
                  <input className="input !w-56 !py-1 text-xs" placeholder="Rejection reason (if rejecting)" value={reject} onChange={(e) => setReject(e.target.value)} />
                  <button className="btn-ok !py-1 text-xs" onClick={() => decide.mutate({ id: v.id, approve: true })}><ShieldCheck size={13} /> Approve & publish</button>
                  <button className="btn-danger !py-1 text-xs" disabled={reject.trim().length < 3} onClick={() => decide.mutate({ id: v.id, approve: false })}>Reject</button></>}
                {v.status === 'pending_approval' && v.created_by === session!.user.id && <span className="text-xs text-warn">Awaiting another authorised approver (you authored this version).</span>}
              </div></li>))}</ol></div>
        {can('rules.author') && latest && ['published', 'rejected', 'retired'].includes(latest.status) && <button className="btn-ghost" onClick={() => onNewVersion(latest)}><FilePlus2 size={15} /> Create new version</button>}
      </div>
    </Modal>
  )
}

function ErrorTableCard() {
  const t = useErrorTable()
  const [open, setOpen] = useState(true)
  if (!t.data) return null
  return (
    <div className="card mb-3 p-3">
      <button className="flex w-full items-center justify-between text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="font-bold">QC error table <span className="font-normal text-muted">— the only things QC checks and scores (total {t.data.total_score})</span></span><span className="text-xs text-muted">{open ? 'Hide' : 'Show'}</span></button>
      {open && <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">{t.data.categories.map((c) => (
        <div key={c.key} className="rounded-xl border bg-surface2/40 p-3">
          <div className="flex items-baseline justify-between gap-2"><b className="text-sm leading-snug">{c.label}</b><span className="shrink-0 rounded-full bg-brand/15 px-2 py-0.5 text-xs font-bold text-brand">−{c.points} → {c.score_after}</span></div>
          <ul className="mt-2 space-y-1 text-xs text-muted">{c.subtypes.map((s) => <li key={s.key} title={s.hint}>• {s.label}{s.manual_only && <span className="ml-1 rounded bg-warn/15 px-1 text-[10px] font-semibold text-warn">reviewer checks</span>}</li>)}</ul>
        </div>))}</div>}
      {open && <p className="mt-2 text-xs text-muted">Each category that has a confirmed error takes its marks off the score. Items marked “reviewer checks” need the video, audio, picture or call log, so the QC reviewer adds them by hand. Rules outside this table are never run.</p>}
    </div>
  )
}

export default function Rules() {
  const { can, session } = useAuth()
  const qcache = useQueryClient()
  const toast = useToast()
  const [bulkOpen, setBulkOpen] = useState(false)
  const [status, setStatus] = useState('')
  const [scope, setScope] = useState('')
  const [search, setSearch] = useState('')
  const [form, setForm] = useState<null | { base?: any }>(null)
  const [open, setOpen] = useState<any>(null)
  const q = useQuery({ queryKey: ['rules', status], queryFn: () => api.get<any[]>('/api/rules' + (status ? `?status=${status}` : '')) })
  useCopilotHint('idle', 'Rules are versioned. Published versions are immutable; changes become new drafts that need a second person’s approval.')
  const pending = (q.data || []).filter((r) => r.status === 'pending_approval')
  const approvable = pending.filter((r) => r.created_by !== session!.user.id)
  const bulk = useMutation({
    mutationFn: (approve: boolean) => api.post<any>('/api/rules/bulk-decision', { version_ids: approvable.map((r) => r.id), approve, reason: approve ? undefined : 'Rejected in bulk review' }),
    onSuccess: (r, approve) => { toast('ok', `${r.done.length} rule(s) ${approve ? 'published' : 'rejected'}${r.refused.length ? `, ${r.refused.length} refused` : ''}`); qcache.invalidateQueries({ queryKey: ['rules'] }); setBulkOpen(false) },
    onError: (e: any) => toast('error', e.message),
  })
  const rows = (q.data || []).filter((r) => (!scope || r.scope === scope) && (!search || (r.name + r.rule_key + (r.category_label || '') + (r.error_label || '')).toLowerCase().includes(search.toLowerCase())))
  return (
    <div>
      <PageHeader title="QC rules & knowledge library" sub="Approved rules used by the pipeline. Every QC run records the exact rule versions it applied." actions={can('rules.author') && <button className="btn-primary" onClick={() => setForm({})}><FilePlus2 size={16} /> New rule</button>} />
      <ErrorTableCard />
      {pending.length > 0 && (
        <div className="mb-3"><Alert kind="warn" title={`${pending.length} rule version(s) are waiting for approval`}>
          They are <b>not applied</b> to QC until published.{can('rules.approve') ? <> {approvable.length > 0 ? <button className="ml-1 font-semibold text-brand hover:underline" onClick={() => setBulkOpen(true)}>Review &amp; approve {approvable.length} →</button> : ' You authored all of them, so a different approver must publish them.'}</> : ' An approver (e.g. the Process Owner) must publish them.'}</Alert></div>)}
      <div className="card mb-3 grid gap-3 p-3 sm:grid-cols-3"><input className="input" placeholder="Search rules…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search rules" />
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status"><option value="">Any status</option>{Object.keys(STATUS_TONE).map((s) => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}</select>
        <select className="input" value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Scope"><option value="">Any scope</option>{['general', 'client', 'project', 'form'].map((s) => <option key={s}>{s}</option>)}</select></div>
      <div className="card overflow-x-auto">
        {q.isLoading ? <SkeletonRows /> : rows.length === 0 ? <EmptyState kind="search" title="No rules found" /> : (
          <table className="w-full min-w-[820px]"><thead><tr className="border-b"><th className="th">Rule</th><th className="th">QC error</th><th className="th">Scope</th><th className="th">Check</th><th className="th">Version</th><th className="th">Status</th><th className="th">Effective</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id} className="cursor-pointer border-b last:border-0 hover:bg-surface2/50" onClick={() => setOpen(r)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && setOpen(r)}>
                <td className="td"><BookOpenCheck size={14} className="mr-1.5 inline text-brand" /><b>{r.name}</b><span className="block font-mono text-[11px] text-muted">{r.rule_key}</span></td>
                <td className="td text-xs">{r.in_qc ? <><b className="text-ink/80">{r.category_label}</b><span className="block text-muted">{r.error_label}</span></> : <Badge t="warn">Not used by QC</Badge>}</td><td className="td capitalize">{r.scope}</td><td className="td text-xs text-muted">{r.check_type.replace(/_/g, ' ')}</td>
                <td className="td tabular-nums">v{r.version}{r.active_version && r.active_version !== r.version && <span className="block text-[11px] text-ok">v{r.active_version} live</span>}</td>
                <td className="td"><Badge t={STATUS_TONE[r.status]}>{r.status.replace(/_/g, ' ')}</Badge></td><td className="td text-muted">{r.effective_date}</td></tr>))}</tbody></table>
        )}
      </div>
      <Modal open={bulkOpen} onClose={() => setBulkOpen(false)} title={`Approve ${approvable.length} rule(s)`} wide>
        <p className="mb-2 text-sm text-muted">Check what you are publishing. Each rule applies only to its own client/scope. Rules you authored are excluded.</p>
        <ul className="max-h-72 space-y-1.5 overflow-auto text-sm">{approvable.map((r) => (
          <li key={r.id} className="rounded-xl border bg-surface2/40 p-2.5"><div className="flex flex-wrap items-center gap-2"><b>{r.rule_key}</b><SeverityBadge s={r.severity} /><Badge t="violet">{r.scope}</Badge><Badge t="muted">{r.check_type.replace(/_/g, ' ')}</Badge></div>
            <p className="font-semibold">{r.name}</p><p className="text-xs text-muted">{r.description}</p>{r.change_note && <p className="text-[11px] text-muted">{r.change_note}</p>}</li>))}</ul>
        <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={() => setBulkOpen(false)}>Cancel</button><button className="btn-danger" disabled={bulk.isPending} onClick={() => bulk.mutate(false)}>Reject all</button><button className="btn-ok" disabled={bulk.isPending} onClick={() => bulk.mutate(true)}>Approve &amp; publish all</button></div>
      </Modal>
      {form && <RuleForm base={form.base} onClose={() => setForm(null)} />}
      {open && <RuleDetail rule={open} onClose={() => setOpen(null)} onNewVersion={(b) => { setOpen(null); setForm({ base: b }) }} />}
    </div>
  )
}
