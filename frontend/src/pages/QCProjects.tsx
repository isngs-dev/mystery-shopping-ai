import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Download, FileSpreadsheet, FolderArchive, Play, ScanSearch, UserCheck, Video } from 'lucide-react'
import { api, download } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useCopilotHint } from '../lib/copilot'
import { Alert, Badge, Field, Modal, PageHeader, SkeletonRows, Stat, StatusBadge, useToast } from '../components/ui'
import { EmptyState } from '../components/Illustrations'

const STAGE_TONE: Record<string, string> = { missing_raw: 'danger', waiting_edited: 'muted', analysing: 'brand', qc_failed: 'danger', to_review: 'violet', ready_to_decide: 'warn', decided: 'ok' }

function Progress({ done, total }: { done: number; total: number }) {
  const pct = total ? Math.round((done / total) * 100) : 0
  return (
    <div title={`${done} of ${total} forms decided`}>
      <div className="h-2 overflow-hidden rounded-full bg-surface2"><div className="h-full rounded-full bg-gradient-to-r from-brand to-ok transition-all" style={{ width: `${pct}%` }} /></div>
      <p className="mt-1 text-[11px] text-muted">{done} / {total} forms decided · {pct}%</p>
    </div>
  )
}

function AssignModal({ project, open, onClose }: { project: { id: number; name: string; reviewer?: { id: number; name: string } | null; suggested_reviewer?: { id: number; name: string } | null }; open: boolean; onClose: () => void }) {
  const toast = useToast()
  const qc = useQueryClient()
  const people = useQuery({ queryKey: ['dir', 'project-reviewer', project.id], queryFn: () => api.get<any[]>(`/api/users/directory?roles=qc_reviewer,manager&project_id=${project.id}&for_project_assignment=true`), enabled: open })
  const [rid, setRid] = useState<string>(String(project.reviewer?.id ?? project.suggested_reviewer?.id ?? ''))
  const m = useMutation({
    mutationFn: () => api.put<any>(`/api/qc/projects/${project.id}/reviewer`, { reviewer_id: rid ? Number(rid) : null }),
    onSuccess: (r) => {
      const a = r.analysis || {}
      toast('ok', r.reviewer ? `${r.reviewer.name} now owns ${project.name}: ${r.forms} form(s), QC started on ${a.queued ?? 0}${r.conflicts?.length ? `, ${r.conflicts.length} skipped (reviewer edited them)` : ''}` : 'Reviewer cleared')
      qc.invalidateQueries({ queryKey: ['qc-projects'] }); qc.invalidateQueries({ queryKey: ['qc-project', project.id] }); qc.invalidateQueries({ queryKey: ['queue'] })
      onClose()
    },
    onError: (e: any) => toast('error', e.message),
  })
  return (
    <Modal open={open} onClose={onClose} title={`Assign ${project.name}`}>
      <div className="space-y-3">
        <Alert kind="info">The chosen QC lead becomes reviewer of <b>every form</b> in this project, the QC analysis is started on every form that has both PDFs, and forms that arrive later go to the same person. Nothing is approved automatically.</Alert>
        {!project.reviewer && project.suggested_reviewer && <p className="text-xs text-muted">The Drive folder name suggests <b className="text-ink/80">{project.suggested_reviewer.name}</b> (pre-selected). Choose someone else if needed.</p>}
        <Field label="QC lead / reviewer">
          <select className="input" value={rid} onChange={(e) => setRid(e.target.value)}>
            <option value="">— nobody —</option>
            {(people.data || []).map((u) => <option key={u.id} value={u.id}>{u.name}{u.role === 'manager' ? ' (manager)' : ''}</option>)}
          </select>
        </Field>
        <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={m.isPending} onClick={() => m.mutate()}><UserCheck size={15} /> {m.isPending ? 'Working…' : 'Assign & run QC'}</button></div>
      </div>
    </Modal>
  )
}

function ProjectCard({ p, canAssign, onAssign }: { p: any; canAssign: boolean; onAssign: (p: any) => void }) {
  const { can } = useAuth()
  const toast = useToast()
  const open = p.to_review + p.ready_to_decide
  return (
    <div className="card flex flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><Link to={`/app/projects/${p.id}`} className="block truncate text-lg font-extrabold text-brand hover:underline">{p.name}</Link><p className="truncate text-xs text-muted">{p.client}</p></div>
        {p.reviewer ? <Badge t="brand">{p.reviewer.name}</Badge> : <Badge t="warn">Not assigned</Badge>}
      </div>
      <p className="text-xs text-muted">{p.forms} form{p.forms === 1 ? '' : 's'} on Drive{!p.reviewer && p.suggested_reviewer ? ` · folder name suggests ${p.suggested_reviewer.name}` : ''}</p>
      {p.reviewer && <>
        <Progress done={p.decided} total={p.forms} />
        <div className="grid grid-cols-4 gap-2 text-center text-xs">
          <div><p className="text-lg font-extrabold tabular-nums text-violet">{open}</p><p className="text-muted">to review</p></div>
          <div><p className="text-lg font-extrabold tabular-nums">{p.analysing}</p><p className="text-muted">analysing</p></div>
          <div><p className="text-lg font-extrabold tabular-nums">{p.waiting_edited}</p><p className="text-muted">no edit yet</p></div>
          <div><p className={`text-lg font-extrabold tabular-nums ${p.qc_failed ? 'text-danger' : ''}`}>{p.qc_failed}</p><p className="text-muted">failed</p></div>
        </div></>}
      <div className="mt-auto flex gap-2">
        {p.reviewer && <Link to={`/app/projects/${p.id}`} className="btn-primary flex-1 justify-center"><ScanSearch size={15} /> Open forms</Link>}
        {p.reviewer && can('qc.report') && <button className="btn-ghost" title="QC report of every form of this project (Excel)" aria-label={`Excel QC report for ${p.name}`} onClick={() => download(`/api/qc/projects/${p.id}/report.xlsx`, `QC_Report_${p.name}.xlsx`).then(() => toast('ok', 'Excel QC report downloaded')).catch((e) => toast('error', e.message))}><FileSpreadsheet size={15} /> Excel</button>}
        {canAssign && <button className={p.reviewer ? 'btn-ghost' : 'btn-primary flex-1 justify-center'} onClick={() => onAssign(p)}><UserCheck size={15} /> {p.reviewer ? 'Change' : 'Assign to a QC lead'}</button>}
      </div>
    </div>
  )
}

/** Every Drive project arrives here. Coordinators (manager, Rahat) and admins hand each one to a QC lead; the QC lead sees only the projects
 *  handed to her, opens one and works through its forms. */
export function QCProjectsPage() {
  const { can } = useAuth()
  const canAssign = can('projects.assign_reviewer')
  const [assign, setAssign] = useState<any>(null)
  const [who, setWho] = useState('')
  const q = useQuery({ queryKey: ['qc-projects', canAssign], queryFn: () => api.get<any[]>(`/api/qc/projects${canAssign ? '' : '?mine=true'}`), refetchInterval: 10000 })
  useCopilotHint('idle', canAssign ? 'New Drive projects wait here until you hand them to a QC lead. Once assigned, QC starts on every form of the project.' : 'These are the projects handed to you. Open one to see its forms and check the findings.')
  const all = q.data || []
  const waiting = all.filter((p) => !p.reviewer)
  const assigned = all.filter((p) => p.reviewer && (!who || String(p.reviewer.id) === who))
  const leads = Array.from(new Map(all.filter((p) => p.reviewer).map((p) => [String(p.reviewer.id), p.reviewer.name])).entries())
  const grid = 'grid gap-4 md:grid-cols-2 xl:grid-cols-3'
  return (
    <div>
      <PageHeader title="QC projects" sub={canAssign ? 'Projects synced from Google Drive. Assign each one to a QC lead; every form in it is then analysed and waits in the lead’s queue.' : 'Projects handed to you. Open one to see its forms; QC has already run on every complete pair.'} />
      {q.isError && <Alert kind="error">{(q.error as Error).message}</Alert>}
      {q.isLoading ? <SkeletonRows /> : !all.length ? (
        <EmptyState kind="success" title={canAssign ? 'No projects yet' : 'No project is assigned to you yet'}>{canAssign ? 'Projects appear here automatically after the next Google Drive sync.' : 'The coordinator will hand you a project; every form in it will then be analysed and appear here.'}</EmptyState>
      ) : (
        <div className="space-y-6">
          {canAssign && <section aria-label="Awaiting assignment">
            <h2 className="mb-2 flex items-center gap-2 text-sm font-bold">Awaiting assignment <Badge t={waiting.length ? 'warn' : 'ok'}>{waiting.length}</Badge></h2>
            {waiting.length ? <div className={grid}>{waiting.map((p) => <ProjectCard key={p.id} p={p} canAssign onAssign={setAssign} />)}</div>
              : <p className="rounded-xl border bg-surface2/40 p-3 text-sm text-muted">Every project has a QC lead.</p>}
          </section>}
          <section aria-label="Assigned QC projects">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><h2 className="flex items-center gap-2 text-sm font-bold">{canAssign ? 'Assigned QC projects' : 'My QC projects'} <Badge t="brand">{assigned.length}</Badge></h2>
              {canAssign && leads.length > 1 && <select className="input !w-auto !py-1 text-xs" value={who} onChange={(e) => setWho(e.target.value)} aria-label="QC lead"><option value="">All QC leads</option>{leads.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select>}</div>
            {assigned.length ? <div className={grid}>{assigned.map((p) => <ProjectCard key={p.id} p={p} canAssign={canAssign} onAssign={setAssign} />)}</div>
              : <p className="rounded-xl border bg-surface2/40 p-3 text-sm text-muted">{canAssign ? 'No project has been assigned yet.' : 'Nothing has been handed to you yet.'}</p>}
          </section>
        </div>
      )}
      {assign && <AssignModal key={assign.id} project={assign} open onClose={() => setAssign(null)} />}
    </div>
  )
}

/** One project: every form with its QC stage and findings, plus the reports. */
export function QCProjectPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const { can } = useAuth()
  const toast = useToast()
  const qc = useQueryClient()
  const [assign, setAssign] = useState(false)
  const [filter, setFilter] = useState('')
  const q = useQuery({ queryKey: ['qc-project', Number(id)], queryFn: () => api.get<any>(`/api/qc/projects/${id}`), refetchInterval: (query) => ((query.state.data?.totals?.analysing ?? 0) > 0 ? 4000 : 15000) })
  const run = useMutation({
    mutationFn: () => api.post<any>(`/api/qc/projects/${id}/run`),
    onSuccess: (r) => { toast('ok', r.queued ? `QC started on ${r.queued} form(s)` : 'Every form that can be analysed already has been'); qc.invalidateQueries({ queryKey: ['qc-project', Number(id)] }) },
    onError: (e: any) => toast('error', e.message),
  })
  useCopilotHint('idle', 'Open a form to check its findings. Accept real errors, reject false alarms, then save your decision. The project report updates as you go.')
  if (q.isError) return <Alert kind="error">{(q.error as Error).message}</Alert>
  if (!q.data) return <SkeletonRows />
  const { project, reviewer, forms, totals: t } = q.data
  const shown = forms.filter((f: any) => !filter || f.stage === filter)
  const open = t.to_review + t.ready_to_decide
  const fetchFile = (url: string, name: string, ok: string) => download(url, name).then(() => toast('ok', ok)).catch((e) => toast('error', e.message))
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <button className="btn-ghost !px-2.5" onClick={() => nav('/app/projects')} aria-label="Back to projects"><ArrowLeft size={16} /></button>
        <div className="min-w-0"><h1 className="truncate text-xl font-extrabold">{project.name}</h1><p className="text-sm text-muted">{project.client} · QC lead: {reviewer ? <b className="text-ink">{reviewer.name}</b> : 'not assigned'}</p></div>
        <div className="ml-auto flex flex-wrap gap-2">
          {can('projects.assign_reviewer') && <button className="btn-ghost" onClick={() => setAssign(true)}><UserCheck size={15} /> {reviewer ? 'Change QC lead' : 'Assign QC lead'}</button>}
          {can('qc.run') && <button className="btn-ghost" disabled={run.isPending} onClick={() => run.mutate()}><Play size={15} /> Run QC on pending forms</button>}
          {can('qc.report') && <button className="btn-primary" onClick={() => fetchFile(`/api/qc/projects/${id}/report.xlsx`, `QC_Report_${project.name}.xlsx`, 'Excel QC report downloaded')}><FileSpreadsheet size={15} /> QC report (Excel)</button>}
          {can('qc.report') && <button className="btn-ghost" onClick={() => fetchFile(`/api/qc/projects/${id}/report`, 'Project_QC_report.pdf', 'Project report downloaded')}><Download size={15} /> Project report</button>}
          {can('qc.report') && <button className="btn-ghost" onClick={() => fetchFile(`/api/qc/projects/${id}/reports.zip`, 'Project_QC_reports.zip', 'All reports downloaded')}><FolderArchive size={15} /> All reports (ZIP)</button>}
        </div>
      </div>
      <div className="mb-4 card p-4"><Progress done={t.decided} total={t.forms} /></div>
      {q.data?.error_table && <details className="mb-4 card p-4 text-sm" open={t.average_score != null}>
        <summary className="cursor-pointer font-semibold">QC by error table{t.average_score != null && <span className="ml-2 font-normal text-muted">average score {t.average_score}/100{t.average_score_decided != null && ` · decided forms ${t.average_score_decided}`}</span>}</summary>
        <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">{q.data.error_table.map((c: any) => (
          <div key={c.category} className="rounded-xl border bg-surface2/40 p-3"><p className="text-xs font-bold leading-snug">{c.category} <span className="text-brand">−{c.marks}</span></p>
            <ul className="mt-1.5 space-y-0.5 text-xs text-muted">{c.errors.map((e: any) => <li key={e.key} className="flex justify-between gap-2"><span>{e.label}{e.reviewer_checked ? ' *' : ''}</span><span className="tabular-nums text-ink/80">{e.confirmed || '–'}{e.pending ? <span className="text-warn"> +{e.pending}</span> : null}</span></li>)}</ul></div>))}</div>
        <p className="mt-2 text-[11px] text-muted">Confirmed errors (+ not yet reviewed). * reviewer checks video / audio / picture.</p></details>}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Forms" value={t.forms} tone="brand" onClick={() => setFilter('')} />
        <Stat label="To review" value={open} tone="violet" sub={`${t.pending_findings} findings open`} onClick={() => setFilter('to_review')} />
        <Stat label="Ready to decide" value={t.ready_to_decide} tone="warn" onClick={() => setFilter('ready_to_decide')} />
        <Stat label="Decided" value={t.decided} tone="ok" onClick={() => setFilter('decided')} />
        <Stat label="QC running" value={t.analysing} tone="cyan" onClick={() => setFilter('analysing')} />
        <Stat label="No edited PDF" value={t.waiting_edited} tone="muted" onClick={() => setFilter('waiting_edited')} />
        <Stat label="QC failed" value={t.qc_failed} tone="danger" onClick={() => setFilter('qc_failed')} />
      </div>
      {t.qc_failed > 0 && <div className="mb-4"><Alert kind="warn">{t.qc_failed} form(s) could not be analysed. "Run QC on pending forms" tries them again; the reason is shown in the table.</Alert></div>}
      <div className="card overflow-x-auto">
        {!shown.length ? <EmptyState kind="success" title="No forms in this view">{filter ? 'Click “Forms” above to see all of them.' : 'This project has no forms yet.'}</EmptyState> : (
          <table className="w-full min-w-[900px]"><thead><tr className="border-b"><th className="th">Form</th><th className="th">Stage</th><th className="th">Findings</th><th className="th">QC score</th><th className="th">To review</th><th className="th">Confirmed</th><th className="th">Rejected</th><th className="th">Form status</th><th className="th">Editor</th><th className="th" /></tr></thead>
            <tbody>{shown.map((f: any) => (
              <tr key={f.form_id} className="border-b last:border-0 hover:bg-surface2/50">
                <td className="td"><Link className="font-bold text-brand hover:underline" to={`/app/forms/${f.form_id}`}>{f.form_code}</Link>{f.recordings > 0 && <Video size={13} className="ml-2 inline text-muted" aria-label="Has a recording" />}</td>
                <td className="td"><Badge t={STAGE_TONE[f.stage]}>{f.stage_label}</Badge>{f.run_error && <span className="mt-1 block max-w-[240px] truncate text-xs text-danger" title={f.run_error}>{f.run_error}</span>}</td>
                <td className="td tabular-nums">{f.findings.total}</td>
                <td className="td tabular-nums" title={f.errors ? `Grammar ${f.errors.grammar} · Missed instruction ${f.errors.missed_instruction} · Contradiction ${f.errors.contradiction} · Wrong location ${f.errors.wrong_location} (confirmed)` : ''}>{f.score ?? '—'}{f.score != null && f.score < 100 && <span className="ml-1 text-xs text-muted">({['grammar', 'missed_instruction', 'contradiction', 'wrong_location'].filter((k) => f.errors?.[k] > 0).map((k) => ({ grammar: 'G', missed_instruction: 'MI', contradiction: 'C', wrong_location: 'WL' } as any)[k]).join(' ')})</span>}</td>
                <td className={`td tabular-nums ${f.review.pending ? 'font-semibold text-violet' : 'text-muted'}`}>{f.review.pending}</td>
                <td className="td tabular-nums">{f.review.confirmed}</td>
                <td className="td tabular-nums">{f.review.rejected}</td>
                <td className="td"><StatusBadge status={f.status} /></td>
                <td className="td text-muted">{f.editor || '—'}</td>
                <td className="td text-right">{f.run_id && <Link className="btn-ghost !py-1" to={`/app/forms/${f.form_id}/qc`}><ScanSearch size={14} /> {f.stage === 'decided' ? 'View' : 'Review'}</Link>}</td>
              </tr>
            ))}</tbody></table>
        )}
      </div>
      {assign && <AssignModal project={{ id: project.id, name: project.name, reviewer }} open onClose={() => setAssign(false)} />}
    </div>
  )
}
