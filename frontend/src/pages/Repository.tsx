import { DragEvent, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronRight, Download, FileSpreadsheet, FileUp, Folder, FolderOpen, RefreshCw, Search, UploadCloud } from 'lucide-react'
import { api, download, qs } from '../lib/api'
import { ago, fmtDate, ISSUE_LABEL, STATUS } from '../lib/format'
import { Alert, Badge, Field, Modal, PageHeader, Pager, SkeletonRows, StatusBadge, Tabs, useToast } from '../components/ui'
import { EmptyState } from '../components/Illustrations'
import { useAuth } from '../lib/auth'
import { useCopilotHint } from '../lib/copilot'

function UploadHint({ pending }: { pending: boolean }) {
  useCopilotHint(pending ? 'scanning' : 'idle', pending ? 'Validating each file and checking names, duplicates and pairs…' : 'Upload FORMID_1.pdf (RAW) and FORMID_2.pdf (edited). I will validate each file, then run the comparison.')
  return null
}

function UploadModal({ open, onClose, projects }: { open: boolean; onClose: () => void; projects: any[] }) {
  const qc = useQueryClient()
  const toast = useToast()
  const { can } = useAuth()
  const [files, setFiles] = useState<File[]>([])
  const [project, setProject] = useState('')
  const [formCode, setFormCode] = useState('')
  const [kind, setKind] = useState('')
  const [note, setNote] = useState('')
  const [aiEdit, setAiEdit] = useState(true)
  const [drag, setDrag] = useState(false)
  const [result, setResult] = useState<any>(null)
  const inp = useRef<HTMLInputElement>(null)
  const single = files.length === 1
  const badName = single && !/^[A-Za-z0-9][A-Za-z0-9-]{0,39}_[12]\.pdf$/i.test(files[0].name)
  const m = useMutation({
    mutationFn: async () => {
      const fd = new FormData()
      fd.set('project_id', project)
      files.forEach((f) => fd.append('files', f))
      if (single && formCode) fd.set('form_code', formCode)
      if (single && kind) fd.set('kind', kind)
      if (note) fd.set('note', note)
      fd.set('ai_edit', String(aiEdit))
      return api.upload<any>('/api/repository/upload', fd)
    },
    onSuccess: (r) => {
      setResult(r)
      qc.invalidateQueries()
      r.failed ? toast('error', `${r.uploaded} uploaded, ${r.failed} need attention`) : toast('ok', `${r.uploaded} file(s) uploaded — analysis started`)
    },
    onError: (e: any) => toast('error', e.message),
  })
  const add = (list: FileList | null) => list && setFiles((f) => [...f, ...Array.from(list)].slice(0, 50))
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files) }
  const close = () => { setFiles([]); setResult(null); setFormCode(''); setKind(''); setNote(''); onClose() }
  const hasRaw = files.some((f) => /_1\.pdf$/i.test(f.name)) || (single && kind === 'raw')
  const ready = project && files.length > 0 && (!badName || (formCode && kind))

  return (
    <Modal open={open} onClose={close} title="Upload PDFs" wide>
      <UploadHint pending={m.isPending} />
      {!result ? (
        <div className="space-y-4">
          <Field label="Project / editor folder"><select className="input" value={project} onChange={(e) => setProject(e.target.value)}><option value="">Select…</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.client_name} / {p.name}</option>)}</select></Field>
          <div onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)} onDrop={onDrop} onClick={() => inp.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && inp.current?.click()}
            className={`grid cursor-pointer place-items-center rounded-2xl border-2 border-dashed p-8 text-center transition ${drag ? 'border-cyan bg-cyan/10' : 'hover:border-brand/60'}`}>
            <UploadCloud className="text-brand" size={32} /><p className="mt-2 text-sm font-semibold">Drop PDFs here or click to browse</p>
            <p className="text-xs text-muted">{can('docs.upload_raw') ? 'FORMID_1.pdf = RAW · ' : ''}{can('docs.upload_edited') ? 'FORMID_2.pdf = edited / resubmission · ' : ''}max 25 MB each</p>
            <input ref={inp} type="file" accept="application/pdf,.pdf" multiple hidden onChange={(e) => { add(e.target.files); e.target.value = '' }} />
          </div>
          {files.length > 0 && <ul className="max-h-40 space-y-1 overflow-auto text-sm">{files.map((f, i) => <li key={i} className="flex items-center justify-between rounded-lg bg-surface2/60 px-3 py-1.5"><span className="truncate"><FileUp size={14} className="mr-2 inline text-brand" />{f.name}</span><button className="text-xs text-danger" onClick={() => setFiles(files.filter((_, j) => j !== i))}>remove</button></li>)}</ul>}
          {badName && (
            <Alert kind="warn" title="File name does not follow the convention">Name it FORMID_1.pdf (RAW) or FORMID_2.pdf (edited), or confirm the Form ID and type below.
              <div className="mt-2 grid grid-cols-2 gap-3"><Field label="Form ID"><input className="input" value={formCode} onChange={(e) => setFormCode(e.target.value)} placeholder="FORM001" /></Field>
                <Field label="Type"><select className="input" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">Select…</option>{can('docs.upload_raw') && <option value="raw">RAW (_1)</option>}{can('docs.upload_edited') && <option value="edited">Edited (_2)</option>}</select></Field></div></Alert>
          )}
          {can('docs.upload_raw') && (
            <label className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 text-sm transition ${aiEdit ? 'border-brand/60 bg-brand/10' : ''}`}>
              <input type="checkbox" className="mt-1" checked={aiEdit} onChange={(e) => setAiEdit(e.target.checked)} />
              <span><b>Let the AI editor correct this report and send it to QC</b>{hasRaw || files.length === 0 ? '' : ' (applies to RAW _1 files)'}
                <span className="block text-xs text-muted">Fixes spelling, grammar and punctuation only — never numbers, names or facts. It creates FORMID_2.pdf automatically; QC then compares RAW vs edited. Untick to have a human editor do it.</span></span>
            </label>)}
          <Field label="Note for the reviewer (optional)"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></Field>
          <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={close}>Cancel</button><button className="btn-primary" disabled={!ready || m.isPending} onClick={() => m.mutate()}>{m.isPending ? 'Validating & uploading…' : `Upload ${files.length || ''} file${files.length === 1 ? '' : 's'}`}</button></div>
        </div>
      ) : (
        <div className="space-y-3">
          {result.results.map((r: any, i: number) => (
            <div key={i} className={`rounded-xl border p-3 text-sm ${r.ok ? 'border-ok/30 bg-ok/5' : 'border-danger/30 bg-danger/5'}`}>
              <p className="font-semibold">{r.filename} {r.ok ? <Badge t="ok">Stored as {r.logical_name}{r.version > 1 ? ` · v${r.version}` : ''}</Badge> : <Badge t="danger">Rejected</Badge>}</p>
              {r.error && <p className="mt-1 text-danger">{r.error}</p>}
              {r.warnings?.map((w: string, j: number) => <p key={j} className="mt-1 text-warn">⚠ {w}</p>)}
              {r.ok && r.ai_edit_queued && <p className="mt-1 text-brand">🤖 AI editor started — the edited PDF will go to QC automatically.</p>}
              {r.ok && <Link className="mt-1 inline-block text-brand hover:underline" to={`/app/forms/${r.form_id}`} onClick={close}>Open {r.form_code} →</Link>}
            </div>
          ))}
          {result.failed > 0 && <p className="text-xs text-muted">Rejected files were logged in the exception queue for follow-up.</p>}
          <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={() => setResult(null)}>Upload more</button><button className="btn-primary" onClick={close}>Done</button></div>
        </div>
      )}
    </Modal>
  )
}

export default function Repository() {
  const { can, session } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  // hand a whole project to an editor (QC lead) straight from its folder card
  const canAssign = can('projects.assign_reviewer')
  const overview = useQuery({ queryKey: ['qc-projects', true], queryFn: () => api.get<any[]>('/api/qc/projects'), enabled: can('qc.report'), refetchInterval: 15000 })
  const people = useQuery({ queryKey: ['dir', 'assign-editors'], queryFn: () => api.get<any[]>('/api/users/directory?roles=qc_reviewer,manager&for_project_assignment=true'), enabled: canAssign })
  const [pick, setPick] = useState<Record<number, string>>({})
  const statsOf = new Map<number, any>((overview.data || []).map((o) => [o.id, o]))       // QC progress of every project: it runs by itself after each Drive sync
  const reviewerOf = new Map<number, any>((overview.data || []).map((o) => [o.id, o.reviewer]))
  const suggestedOf = new Map<number, any>((overview.data || []).map((o) => [o.id, o.suggested_reviewer]))     // the person the Drive folder name points to ("Orlando_Devansh")
  const assign = useMutation({
    mutationFn: (v: { id: number; name: string; rid: number }) => api.put<any>(`/api/qc/projects/${v.id}/reviewer`, { reviewer_id: v.rid }),
    onSuccess: (r, v) => {
      toast('ok', `${r.reviewer?.name} now has ${v.name}: ${r.forms} form(s), QC started on ${r.analysis?.queued ?? 0}${r.conflicts?.length ? `, ${r.conflicts.length} skipped (they edited those)` : ''}`)
      setPick((p) => ({ ...p, [v.id]: '' }))
      qc.invalidateQueries({ queryKey: ['qc-projects'] }); qc.invalidateQueries({ queryKey: ['queue'] }); qc.invalidateQueries({ queryKey: ['repo-forms'] })
    },
    onError: (e: any) => toast('error', e.message),
  })
  const [tab, setTab] = useState<'docs' | 'exceptions'>('docs')
  const [upload, setUpload] = useState(false)
  const [f, setF] = useState({ q: '', project_id: '', client_id: '', status: '', pair: '', has_version: '' })
  const [offset, setOffset] = useState(0)
  const limit = 25
  const tree = useQuery({ queryKey: ['tree'], queryFn: () => api.get<any>('/api/repository/tree') })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get<any[]>('/api/projects') })
  const forms = useQuery({ queryKey: ['repo-forms', f, offset], refetchInterval: 15000, queryFn: () => api.get<any>('/api/forms' + qs({ ...f, offset, limit })) })
  const exc = useQuery({ queryKey: ['exceptions'], queryFn: () => api.get<any>('/api/repository/exceptions'), enabled: can('repository.exceptions') })
  const scan = useMutation({ mutationFn: () => api.post<any>('/api/repository/scan'), onSuccess: (r) => { toast('ok', `Scan complete: ${r.registered} registered, ${r.problems} problem(s).`); qc.invalidateQueries() }, onError: (e: any) => toast('error', e.message) })
  const resolve = useMutation({ mutationFn: (v: { id: number; status: string }) => api.post(`/api/repository/exceptions/${v.id}/resolve`, { status: v.status }), onSuccess: () => qc.invalidateQueries({ queryKey: ['exceptions'] }) })
  const canUpload = can('docs.upload_raw') || can('docs.upload_edited')
  const set = (k: string, v: string) => { setF({ ...f, [k]: v }); setOffset(0) }
  useCopilotHint(exc.data?.total ? 'alert' : 'idle', exc.data?.total ? `${exc.data.total} file(s) need intervention — see the Exception queue.` : 'Open a project folder to see all its forms, or search every project by Form ID, file name or checksum.')

  return (
    <div>
      <PageHeader title="PDF repository" sub={<>Workspace <b>{tree.data?.workspace || 'MysteryShop'}</b> / Client / Project or Editor / FORMID_1.pdf (RAW, immutable) · FORMID_2.pdf (edited)</>}
        actions={<>{can('repository.exceptions') && <button className="btn-ghost" onClick={() => scan.mutate()} disabled={scan.isPending}><RefreshCw size={15} className={scan.isPending ? 'animate-spin' : ''} /> Scan storage</button>}{canUpload && <button className="btn-primary" onClick={() => setUpload(true)}><UploadCloud size={16} /> Upload PDFs</button>}</>} />
      <Tabs tabs={[{ key: 'docs', label: 'Forms & documents', count: forms.data?.total }, ...(can('repository.exceptions') ? [{ key: 'exceptions' as const, label: 'Exception queue', count: exc.data?.total }] : [])]} value={tab} onChange={setTab} />

      {tab === 'docs' && (() => {
        const clients: any[] = tree.data?.clients || []
        const selected = clients.flatMap((c) => c.projects.map((p: any) => ({ ...p, client: c.name }))).find((p) => String(p.id) === f.project_id)
        const inProject = !!f.project_id
        const showFolders = !inProject && !f.q
        const clear = () => { setF({ q: '', project_id: '', client_id: '', status: '', pair: '', has_version: '' }); setOffset(0) }
        const searchBox = (
          <div className="relative sm:col-span-2"><Search size={15} className="absolute left-3 top-3 text-muted" /><input className="input !pl-9" placeholder={inProject ? 'Search Form ID, file name or checksum in this project…' : 'Search every project by Form ID, file name or checksum…'} value={f.q} onChange={(e) => set('q', e.target.value)} aria-label="Search forms" /></div>
        )
        if (showFolders) return (
          <div>
            <div className="card mb-4 grid gap-3 p-3 sm:grid-cols-2">{searchBox}</div>
            {tree.isLoading ? <SkeletonRows rows={4} /> : clients.length === 0 ? <EmptyState kind="search" title="No project folders yet">Projects appear here after the next Google Drive sync.</EmptyState> : (
              <div className="space-y-5">{clients.map((c) => (
                <section key={c.id} aria-label={c.name}>
                  <h2 className="mb-2 flex items-center gap-2 text-sm font-bold"><Folder size={16} className="text-brand" /> {c.name} <Badge t="muted">{c.projects.length} project{c.projects.length === 1 ? '' : 's'}</Badge></h2>
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{c.projects.map((p: any) => {
                    const current = reviewerOf.get(p.id)
                    const options = (people.data || []).filter((u) => u.id !== session?.user.id || u.id === current?.id)
                    const hint = !current ? suggestedOf.get(p.id) : null
                    const value = pick[p.id] ?? (hint && options.some((u) => u.id === hint.id) ? String(hint.id) : '')       // pre-selected, never assigned automatically
                    return (
                    <div key={p.id} className="card flex flex-col gap-3 p-4 transition hover:border-brand/60 hover:shadow-glow">
                      <button onClick={() => { setF({ ...f, project_id: String(p.id), client_id: '' }); setOffset(0) }} className="flex items-center gap-3 text-left" aria-label={`Open ${p.name}`}>
                        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-brand/15 text-brand"><FolderOpen size={22} /></span>
                        <span className="min-w-0"><b className="block truncate">{p.name}</b><span className="text-xs text-muted">{p.forms} form{p.forms === 1 ? '' : 's'}</span></span>
                        <ChevronRight size={16} className="ml-auto shrink-0 text-muted" />
                      </button>
                      {can('qc.report') && (() => {
                        const st = statsOf.get(p.id)
                        if (!st) return null
                        const done = st.decided + st.to_review + st.ready_to_decide
                        return (
                          <div className="flex flex-wrap items-center gap-2 border-t pt-3 text-xs">
                            <Badge t={st.forms > 0 && done === st.forms ? 'ok' : st.analysing > 0 ? 'brand' : 'muted'}>QC {done}/{st.forms} done{st.analysing > 0 ? ` · ${st.analysing} running` : ''}</Badge>
                            {st.qc_failed > 0 && <Badge t="danger">{st.qc_failed} failed</Badge>}
                            {st.forms > 0 && <button className="btn-ghost ml-auto !px-2.5 !py-1 text-xs" title="QC report of every form of this project (Excel)" onClick={() => download(`/api/qc/projects/${p.id}/report.xlsx`, `QC_Report_${p.name}.xlsx`).then(() => toast('ok', 'Excel QC report downloaded')).catch((e) => toast('error', e.message))}><FileSpreadsheet size={13} /> QC report (Excel)</button>}
                          </div>)
                      })()}
                      {canAssign && (
                        <div className="border-t pt-3 text-xs">
                          <p className="mb-1.5 text-muted">Editor: {current ? <b className="text-ink">{current.name}</b> : <span className="font-semibold text-warn">not assigned</span>}{hint && <span className="ml-1">· folder name suggests {hint.name}</span>}</p>
                          <div className="flex gap-2">
                            <select className="input !py-1.5 text-xs" value={value} onChange={(e) => setPick({ ...pick, [p.id]: e.target.value })} aria-label={`Assign ${p.name} to an editor`}>
                              <option value="">{current ? 'Change editor…' : 'Assign to editor…'}</option>
                              {options.map((u) => <option key={u.id} value={u.id}>{u.name}{u.id === current?.id ? ' (current)' : ''}</option>)}
                            </select>
                            <button className="btn-primary !px-3 !py-1.5 text-xs" disabled={!value || assign.isPending} onClick={() => assign.mutate({ id: p.id, name: p.name, rid: Number(value) })}>Assign</button>
                          </div>
                        </div>)}
                    </div>)})}</div>
                </section>))}</div>
            )}
          </div>
        )
        return (
          <div>
            <nav className="mb-3 flex flex-wrap items-center gap-1.5 text-sm" aria-label="Folder path">
              <button className="font-semibold text-brand hover:underline" onClick={clear}>All folders</button>
              {selected ? <><ChevronRight size={13} className="text-muted" /><span className="text-muted">{selected.client}</span><ChevronRight size={13} className="text-muted" /><b>{selected.name}</b><Badge t="muted" className="ml-2">{selected.forms} forms</Badge>{can('qc.report') && <button className="btn-ghost ml-auto !py-1 text-xs" onClick={() => download(`/api/qc/projects/${selected.id}/report.xlsx`, `QC_Report_${selected.name}.xlsx`).then(() => toast('ok', 'Excel QC report downloaded')).catch((e) => toast('error', e.message))}><FileSpreadsheet size={14} /> QC report (Excel)</button>}</> : <><ChevronRight size={13} className="text-muted" /><b>Search results</b></>}
            </nav>
            <div className="card mb-3 grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-4">
              {searchBox}
              <select className="input" value={f.status} onChange={(e) => set('status', e.target.value)} aria-label="Processing status"><option value="">Any status</option>{Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
              <select className="input" value={f.pair} onChange={(e) => set('pair', e.target.value)} aria-label="Pair status"><option value="">Any pair state</option><option value="complete">Complete pair</option><option value="missing_edited">Missing edited (_2)</option><option value="missing_raw">Missing RAW (_1)</option></select>
              {!inProject && <select className="input" value={f.project_id} onChange={(e) => set('project_id', e.target.value)} aria-label="Project"><option value="">All projects</option>{projects.data?.map((p) => <option key={p.id} value={p.id}>{p.client_name} / {p.name}</option>)}</select>}
              <select className="input" value={f.has_version} onChange={(e) => set('has_version', e.target.value)} aria-label="Version"><option value="">Any version</option><option value="2">Resubmitted (v2+)</option><option value="3">v3+</option></select>
              <button className="btn-ghost" onClick={() => { setF({ ...f, q: '', status: '', pair: '', has_version: '' }); setOffset(0) }}>Clear filters</button>
            </div>
            <div className="card overflow-x-auto">
              {forms.isLoading ? <SkeletonRows /> : forms.isError ? <div className="p-4"><Alert kind="error">{(forms.error as Error).message}</Alert></div> : forms.data.items.length === 0 ? (
                <EmptyState kind="search" title="No forms found" action={canUpload && <button className="btn-primary" onClick={() => setUpload(true)}>Upload a RAW PDF</button>}>Try different filters, or upload the first RAW / edited pair.</EmptyState>
              ) : (
                <table className="w-full min-w-[760px]"><thead><tr className="border-b"><th className="th">Form ID</th><th className="th">Logical path</th><th className="th">Pair</th><th className="th">Versions</th><th className="th">Status</th><th className="th">Uploaded</th>{can('qc.report') && <th className="th">QC report</th>}</tr></thead>
                  <tbody>{forms.data.items.map((r: any) => (
                    <tr key={r.id} className="border-b last:border-0 hover:bg-surface2/50">
                      <td className="td"><Link to={`/app/forms/${r.id}`} className="font-bold text-brand hover:underline">{r.form_code}</Link>{r.is_sample && <Badge t="warn" className="ml-2">SAMPLE</Badge>}</td>
                      <td className="td text-xs text-muted">{r.path}</td>
                      <td className="td"><Badge t={r.pair_status === 'complete' ? 'ok' : 'warn'}>{r.pair_status === 'complete' ? 'RAW + edited' : r.pair_status === 'missing_edited' ? 'Awaiting edited' : r.pair_status === 'missing_raw' ? 'RAW missing' : 'Empty'}</Badge></td>
                      <td className="td tabular-nums">{r.edited_versions ? `v${r.edited_versions}` : '—'}</td>
                      <td className="td"><StatusBadge status={r.status} /></td>
                      <td className="td text-muted">{fmtDate(r.created_at, false)}</td>
                      {can('qc.report') && <td className="td">{r.edited_versions > 0 ? <button className="btn-ghost !px-2 !py-1 text-xs" title="Download the QC report (PDF) of this form" onClick={() => download(`/api/forms/${r.id}/qc-report`, `QC_Report_${r.form_code}.pdf`).then(() => toast('ok', 'QC report downloaded')).catch((e) => toast('error', e.message))}><Download size={13} /> PDF</button> : <span className="text-xs text-muted">—</span>}</td>}
                    </tr>))}</tbody></table>
              )}
              {forms.data && <Pager total={forms.data.total} limit={limit} offset={offset} onChange={setOffset} />}
            </div>
          </div>
        )
      })()}

      {tab === 'exceptions' && (
        <div className="card overflow-x-auto">
          {exc.isLoading ? <SkeletonRows /> : exc.data.items.length === 0 ? <EmptyState kind="success" title="No exceptions">Missing pairs, duplicate IDs, bad file names and unreadable PDFs show up here.</EmptyState> : (
            <table className="w-full min-w-[720px]"><thead><tr className="border-b"><th className="th">Issue</th><th className="th">Form / file</th><th className="th">Detail</th><th className="th">Raised</th><th className="th" /></tr></thead>
              <tbody>{exc.data.items.map((e: any) => (
                <tr key={e.id} className="border-b align-top last:border-0">
                  <td className="td"><Badge t="warn"><AlertTriangle size={11} />{ISSUE_LABEL[e.issue_type] || e.issue_type}</Badge></td>
                  <td className="td">{e.form_id ? <Link className="font-semibold text-brand hover:underline" to={`/app/forms/${e.form_id}`}>{e.form_code}</Link> : <b>{e.form_code || '—'}</b>}{e.filename && <span className="block text-xs text-muted">{e.filename}</span>}</td>
                  <td className="td max-w-md text-sm text-muted">{e.detail}</td>
                  <td className="td text-muted">{ago(e.created_at)}</td>
                  <td className="td text-right">{!e.derived ? <div className="flex justify-end gap-1"><button className="btn-ok !py-1 text-xs" onClick={() => resolve.mutate({ id: e.id, status: 'resolved' })}>Resolve</button><button className="btn-ghost !py-1 text-xs" onClick={() => resolve.mutate({ id: e.id, status: 'dismissed' })}>Dismiss</button></div> : <span className="text-xs text-muted">clears when fixed</span>}</td>
                </tr>))}</tbody></table>
          )}
        </div>
      )}
      <UploadModal open={upload} onClose={() => setUpload(false)} projects={projects.data || []} />
    </div>
  )
}
