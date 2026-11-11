import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, CloudDownload, Download, FolderInput, HardDriveDownload, Loader2, PauseCircle, PlayCircle, RefreshCw, ShieldCheck, UploadCloud, XCircle } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { ago, fmtDate } from '../lib/format'
import { Alert, Badge, Field, PageHeader, useToast } from '../components/ui'
import { useCopilotHint } from '../lib/copilot'

const STAT_LABEL: Record<string, string> = {
  clients_created: 'Clients created', projects_created: 'Projects created', raw_imported: 'RAW PDFs imported', edited_imported: 'Edited PDFs imported', ai_edited: 'AI-edited (no _2 file)',
  qc_runs: 'QC analyses run', already_imported: 'Already imported (skipped)', tracker_rows: 'Tracker rows', rules_submitted: 'QC rules awaiting approval', naming_errors: 'Bad file names',
  unmapped_suffix_files: 'Files with undefined suffix (_3, _4…)', missing_raw: 'Edited without RAW', rejected: 'Rejected PDFs', rule_documents_found: 'Rule documents found', qc_errors: 'QC errors', read_errors: 'Read errors',
}
const BAD = new Set(['naming_errors', 'unmapped_suffix_files', 'missing_raw', 'rejected', 'qc_errors', 'read_errors'])
const CHUNK = 25

async function downloadZip(ids: number[], name: string) {
  const res = await fetch('/api/reports/qc-batch', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify({ form_ids: ids }) })
  if (!res.ok) throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail || 'Download failed')
  const blob = await res.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob); a.download = name
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(a.href), 3000)
}


function AutoSyncCard() {
  const qc = useQueryClient()
  const toast = useToast()
  const st = useQuery({ queryKey: ['drive-auto'], queryFn: () => api.get<any>('/api/integrations/drive/auto'), refetchInterval: 5000 })
  const d = st.data
  const [f, setF] = useState<any>(null)
  useEffect(() => { if (d && !f) setF({ enabled: d.enabled, interval_minutes: d.interval_minutes, source: d.source, folder_id: d.folder_id || '', local_path: d.local_path || '', run_qc: d.run_qc, ai_edit_missing: d.ai_edit_missing, ai_edit_after_hours: d.ai_edit_after_hours, max_forms_per_cycle: d.max_forms_per_cycle }) }, [d]) // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: (enabled: boolean) => api.put<any>('/api/integrations/drive/auto', { ...f, enabled, folder_id: f.folder_id || null, local_path: f.local_path || null }),
    onSuccess: (r) => { toast('ok', r.enabled ? 'Automatic sync is ON. The first sync starts within a minute.' : 'Automatic sync is OFF.'); qc.invalidateQueries({ queryKey: ['drive-auto'] }) },
    onError: (e: any) => toast('error', e.message),
  })
  const now = useMutation({ mutationFn: () => api.post<any>('/api/integrations/drive/auto/run-now'), onSuccess: () => { toast('ok', 'Sync started'); qc.invalidateQueries({ queryKey: ['drive-jobs'] }); qc.invalidateQueries({ queryKey: ['drive-auto'] }) }, onError: (e: any) => toast('error', e.message) })
  if (!d || !f) return <div className="card mb-4 p-4"><Loader2 className="animate-spin text-brand" /></div>
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const mins = d.seconds_until_next != null ? Math.ceil(d.seconds_until_next / 60) : null
  const r = d.last_result?.stats
  return (
    <div className={`card mb-4 p-4 ${d.enabled ? 'border-ok/40' : ''}`}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 font-bold"><RefreshCw size={16} className={`text-brand ${d.running_job_id ? 'animate-spin' : ''}`} /> Automatic sync</h2>
        <Badge t={d.enabled ? 'ok' : 'muted'}>{d.enabled ? 'ON' : 'OFF'}</Badge>
        {d.running_job_id && <Badge t="brand">syncing now…</Badge>}
        {!d.scheduler_running && <Badge t="warn">scheduler not running on this server</Badge>}
        <span className="text-xs text-muted">New or changed RAW / edited PDFs in the Drive folder appear here by themselves, get QC'd, and admins are notified.</span>
      </div>
      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Connect via"><select className="input" value={f.source} onChange={(e) => set('source', e.target.value)}><option value="google_drive">Google Drive API</option><option value="local">Synced folder on this server</option></select></Field>
            <Field label="Check every"><select className="input" value={f.interval_minutes} onChange={(e) => set('interval_minutes', +e.target.value)}>{[1, 2, 5, 10, 15, 30, 60].map((m) => <option key={m} value={m}>{m} minute{m > 1 ? 's' : ''}</option>)}</select></Field>
            <Field label="Max new forms / cycle"><input type="number" min={1} max={2000} className="input" value={f.max_forms_per_cycle} onChange={(e) => set('max_forms_per_cycle', +e.target.value || 1)} /></Field>
          </div>
          {f.source === 'google_drive'
            ? <Field label="Drive folder ID" hint={d.google.configured ? `Connected as ${d.google.service_account_email}` : 'Google is not connected yet (see “Live sync with Google Drive” below).'}><input className="input font-mono text-xs" value={f.folder_id} onChange={(e) => set('folder_id', e.target.value)} placeholder="1vgvK45CRBViNHDGA78tlgXWk5nnjGgAK" /></Field>
            : <Field label="Folder path" hint="Install Google Drive for desktop, make the folder available offline, and point this at it (it must be inside IMPORT_LOCAL_ROOT)."><input className="input" value={f.local_path} onChange={(e) => set('local_path', e.target.value)} placeholder="Mystery Shopping_QC" /></Field>}
          <div className="space-y-2 rounded-xl border bg-surface2/40 p-3 text-sm">
            <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={f.run_qc} onChange={(e) => set('run_qc', e.target.checked)} /><span><b>Run QC on each new RAW vs edited pair</b></span></label>
            <label className="flex flex-wrap items-center gap-2"><input type="checkbox" checked={f.ai_edit_missing} onChange={(e) => set('ai_edit_missing', e.target.checked)} /><span><b>AI-edit a RAW that still has no edited file after</b></span>
              <input type="number" min={0} max={720} className="input !w-20 !py-1" disabled={!f.ai_edit_missing} value={f.ai_edit_after_hours} onChange={(e) => set('ai_edit_after_hours', Math.max(0, +e.target.value || 0))} /><span>hours</span></label>
            <p className="text-xs text-muted">When the human editor's file arrives later it becomes the newest version; the AI version stays in the history.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate(true)}><PlayCircle size={15} /> {d.enabled ? 'Save changes' : 'Turn ON & start'}</button>
            {d.enabled && <button className="btn-ghost" disabled={save.isPending} onClick={() => save.mutate(false)}><PauseCircle size={15} /> Turn OFF</button>}
            <button className="btn-ghost" disabled={now.isPending || !!d.running_job_id} onClick={() => now.mutate()}><RefreshCw size={15} /> Sync now</button>
          </div>
        </div>
        <div className="space-y-2 text-sm">
          <p className="text-xs font-bold uppercase tracking-wider text-muted">Status</p>
          <p>Last sync: <b>{d.last_run_at ? ago(d.last_run_at + 'Z') : 'never'}</b>{d.last_result && <> · <Badge t={d.last_result.status === 'completed' ? 'ok' : 'danger'}>{d.last_result.status}</Badge></>}</p>
          <p>Next sync: <b>{d.enabled ? (d.running_job_id ? 'running' : mins === null ? 'starting…' : mins <= 0 ? 'any moment' : `in ~${mins} min`) : '— (off)'}</b></p>
          {r && <div className="grid grid-cols-2 gap-1.5 text-xs">{([['raw_imported', 'New RAW'], ['edited_imported', 'New edited'], ['edited_resubmitted', 'Revised edited'], ['ai_edited', 'AI-edited'], ['qc_runs', 'QC runs'], ['already_imported', 'Unchanged']] as const).map(([k, l]) => (
            <div key={k} className="rounded-lg border bg-surface2/40 px-2 py-1"><span className="text-muted">{l}</span> <b className="float-right tabular-nums">{r[k] || 0}</b></div>))}</div>}
          {d.last_error && <Alert kind="error" title="Last sync problem">{d.last_error}</Alert>}
          {!d.enabled && !d.last_run_at && <p className="text-xs text-muted">Turn it ON once Google Drive is connected (or a synced folder is set).</p>}
        </div>
      </div>
    </div>
  )
}

export default function DriveImport() {
  const qc = useQueryClient()
  const toast = useToast()
  const [aiEdit, setAiEdit] = useState(true)
  const [runQc, setRunQc] = useState(true)
  const [auto, setAuto] = useState(true)
  const [folder, setFolder] = useState('')
  const [local, setLocal] = useState('')
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState<number | null>(null)
  const inp = useRef<HTMLInputElement>(null)
  const autoDone = useRef<Set<number>>(new Set())

  const status = useQuery({ queryKey: ['drive-status'], queryFn: () => api.get<any>('/api/integrations/drive/status') })
  const jobs = useQuery({
    queryKey: ['drive-jobs'], queryFn: () => api.get<any[]>('/api/integrations/drive/jobs'),
    refetchInterval: (q) => (q.state.data || []).some((j: any) => ['queued', 'running'].includes(j.status)) ? 1500 : false,
  })
  const latest = jobs.data?.[0]
  const running = latest && ['queued', 'running'].includes(latest.status)
  useCopilotHint(running ? 'scanning' : latest?.status === 'failed' ? 'alert' : 'idle',
    running ? 'Importing the Drive folder: validating each PDF, extracting text and running QC…' : latest?.status === 'failed' ? 'The import failed — the reason is shown on this page.' : 'Bring RAW/edited PDFs from your Drive folder into the platform, with QC rules and the tracker.')

  const started = (j: any) => { toast('ok', `Import #${j.id} started`); qc.invalidateQueries({ queryKey: ['drive-jobs'] }) }
  const err = (e: any) => toast('error', e.message)
  const upload = useMutation({
    mutationFn: (file: File) => { const fd = new FormData(); fd.set('file', file); fd.set('ai_edit_missing', String(aiEdit)); fd.set('run_qc', String(runQc)); return api.upload<any>('/api/integrations/drive/import-zip', fd) },
    onSuccess: started, onError: err,
  })
  const sync = useMutation({ mutationFn: () => api.post<any>('/api/integrations/drive/sync', { folder_id: folder || null, ai_edit_missing: aiEdit, run_qc: runQc }), onSuccess: started, onError: err })
  const importLocal = useMutation({ mutationFn: () => api.post<any>('/api/integrations/drive/import-local', { path: local, ai_edit_missing: aiEdit, run_qc: runQc }), onSuccess: started, onError: err })

  const ids: number[] = latest?.stats?.form_ids || []
  const chunk = async (n: number) => {
    setBusy(n)
    try { await downloadZip(ids.slice(n * CHUNK, (n + 1) * CHUNK), `QC_Reports_import${latest.id}_part${n + 1}.zip`); toast('ok', 'QC reports downloaded') } catch (e: any) { toast('error', e.message) } finally { setBusy(null) }
  }
  // "automatically download": when a MANUAL import finishes, the first batch of QC reports downloads by itself (never for scheduled auto-sync cycles)
  useEffect(() => {
    if (auto && latest?.status === 'completed' && !latest.options?.auto && ids.length && !autoDone.current.has(latest.id) && latest.created_at && Date.now() - new Date(latest.finished_at || latest.created_at).getTime() < 120000) {
      autoDone.current.add(latest.id)
      chunk(0)
    }
  }, [latest?.id, latest?.status]) // eslint-disable-line react-hooks/exhaustive-deps
  const chunks = Math.ceil(ids.length / CHUNK)

  return (
    <div>
      <PageHeader title="Google Drive import" sub="Keep the “Mystery Shopping_QC” folder connected, or bring it in once: Client / Project-or-Editor folders with ID_1.pdf (RAW) and ID_2.pdf (edited), the tracker spreadsheet and the QC rule documents." />
      <AutoSyncCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card space-y-4 p-4">
          <h2 className="flex items-center gap-2 font-bold"><HardDriveDownload size={16} className="text-brand" /> 1 · Upload the Drive folder as a ZIP</h2>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-muted"><li>In Google Drive, right-click the folder <b className="text-ink">Mystery Shopping_QC</b> → <b className="text-ink">Download</b>.</li><li>Drop the ZIP below. No Google credentials are needed.</li></ol>
          <div onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)} onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) upload.mutate(f) }}
            onClick={() => inp.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && inp.current?.click()}
            className={`grid cursor-pointer place-items-center rounded-2xl border-2 border-dashed p-8 text-center transition ${drag ? 'border-cyan bg-cyan/10' : 'hover:border-brand/60'}`}>
            {upload.isPending ? <Loader2 className="animate-spin text-brand" /> : <UploadCloud className="text-brand" size={30} />}
            <p className="mt-2 text-sm font-semibold">{upload.isPending ? 'Uploading…' : 'Drop the Drive ZIP here or click to browse'}</p>
            <input ref={inp} type="file" accept=".zip,application/zip" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = '' }} />
          </div>
          <div className="space-y-2 rounded-xl border bg-surface2/40 p-3 text-sm">
            <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={runQc} onChange={(e) => setRunQc(e.target.checked)} /><span><b>Run QC on every RAW vs edited pair</b><span className="block text-xs text-muted">Compares ID_1 with ID_2 and applies the approved QC rules.</span></span></label>
            <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={aiEdit} onChange={(e) => setAiEdit(e.target.checked)} /><span><b>Let the AI editor edit RAWs that have no ID_2 file</b><span className="block text-xs text-muted">Spelling and grammar only; then QC compares RAW vs the AI-edited PDF.</span></span></label>
            <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={auto} onChange={(e) => setAuto(e.target.checked)} /><span><b>Download QC reports automatically when the import finishes</b></span></label>
          </div>
        </div>

        <div className="space-y-4">
          <div className="card space-y-3 p-4">
            <h2 className="flex items-center gap-2 font-bold"><CloudDownload size={16} className="text-brand" /> 2 · Live sync with Google Drive</h2>
            {status.data?.configured ? <Alert kind="ok" title="Google Drive connected">Service account <b>{status.data.service_account_email}</b>. Share the folder with this address (Viewer).</Alert>
              : <Alert kind="warn" title="Not connected yet">{status.data?.help || 'Checking…'}<br />To connect: create a Google Cloud service account, enable the Drive API, download its JSON key, set <code>GOOGLE_SERVICE_ACCOUNT_FILE</code>, and share the Drive folder with the service account’s e-mail.</Alert>}
            <Field label="Drive folder ID" hint="The part of the folder URL after /folders/"><input className="input font-mono text-xs" value={folder} onChange={(e) => setFolder(e.target.value)} placeholder={status.data?.default_folder_id || '1vgvK45CRBViNHDGA78tlgXWk5nnjGgAK'} /></Field>
            <button className="btn-primary" disabled={!status.data?.configured || sync.isPending || !!running} onClick={() => sync.mutate()}><CloudDownload size={15} /> Sync from Google Drive</button>
          </div>
          <div className="card space-y-3 p-4">
            <h2 className="flex items-center gap-2 font-bold"><FolderInput size={16} className="text-brand" /> 3 · Import a folder on this server</h2>
            <p className="text-xs text-muted">Copy or unzip the Drive folder into <code className="break-all">{status.data?.local_import_root}</code> (for example a Google Drive for desktop copy), then enter its name.</p>
            <div className="flex gap-2"><input className="input" value={local} onChange={(e) => setLocal(e.target.value)} placeholder="Mystery Shopping_QC" /><button className="btn-ghost" disabled={!local || importLocal.isPending || !!running} onClick={() => importLocal.mutate()}>Import</button></div>
          </div>
        </div>
      </div>

      {latest && (
        <div className="card mt-4 p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2"><h2 className="font-bold">Import #{latest.id}</h2>
            <Badge t={latest.status === 'completed' ? 'ok' : latest.status === 'failed' ? 'danger' : 'brand'}>{running && <Loader2 size={11} className="animate-spin" />}{latest.status === 'completed' && <CheckCircle2 size={11} />}{latest.status === 'failed' && <XCircle size={11} />}{latest.status}</Badge>
            <span className="text-xs text-muted">{latest.source} · {fmtDate(latest.created_at)}</span></div>
          {latest.error && <div className="mb-3"><Alert kind="error" title="Import failed">{latest.error}</Alert></div>}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {Object.entries(latest.stats).filter(([k, v]) => typeof v === 'number' && STAT_LABEL[k]).map(([k, v]) => (
              <div key={k} className={`rounded-xl border p-2.5 ${BAD.has(k) && (v as number) > 0 ? 'border-warn/40 bg-warn/5' : 'bg-surface2/40'}`}><p className="text-[11px] font-semibold uppercase tracking-wide text-muted">{STAT_LABEL[k]}</p><p className="text-xl font-extrabold tabular-nums">{v as number}</p></div>))}
          </div>
          {latest.status === 'completed' && (
            <div className="mt-4 space-y-3">
              {latest.stats.rules_submitted > 0 && <Alert kind="info" title={`${latest.stats.rules_submitted} QC rules were created from the checklists and guidelines`}>They are <b>pending approval</b> and are not applied to QC until an approver publishes them. <Link className="font-semibold text-brand hover:underline" to="/app/rules">Review &amp; approve rules →</Link> Then re-run analysis on the forms.</Alert>}
              {(latest.stats.unmapped_suffix_files > 0 || latest.stats.naming_errors > 0 || latest.stats.missing_raw > 0) && <Alert kind="warn" title="Some files were not imported"><Link className="font-semibold text-brand hover:underline" to="/app/repository">Open the exception queue →</Link> It lists files with an undefined suffix (only _1 = RAW and _2 = edited are defined), bad names, and edited files without a RAW.</Alert>}
              {ids.length > 0 && (
                <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold"><ShieldCheck size={14} className="mr-1 inline text-ok" />QC reports ({ids.length} forms):</span>
                  {Array.from({ length: chunks }).map((_, n) => <button key={n} className="btn-ghost !py-1 text-xs" disabled={busy !== null} onClick={() => chunk(n)}>{busy === n ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />} {n * CHUNK + 1}–{Math.min((n + 1) * CHUNK, ids.length)}</button>)}
                  <span className="text-xs text-muted">Reports are DRAFT until a reviewer saves the QC decision.</span></div>)}
            </div>
          )}
          <details className="mt-4 text-xs" open={latest.status === 'failed'}><summary className="cursor-pointer font-semibold text-muted">Log ({latest.log.length} lines)</summary>
            <ul className="mt-2 max-h-56 space-y-0.5 overflow-auto rounded-xl bg-surface2/50 p-3 font-mono">{latest.log.map((l: string, i: number) => <li key={i}>{l}</li>)}</ul></details>
        </div>
      )}
      {(jobs.data?.length || 0) > 1 && (
        <div className="card mt-4 overflow-x-auto"><h3 className="p-4 pb-0 text-sm font-bold">Earlier imports</h3>
          <table className="w-full"><tbody>{jobs.data!.slice(1).map((j) => <tr key={j.id} className="border-b last:border-0"><td className="td font-semibold">#{j.id}</td><td className="td text-muted">{j.source}</td><td className="td"><Badge t={j.status === 'completed' ? 'ok' : j.status === 'failed' ? 'danger' : 'brand'}>{j.status}</Badge></td><td className="td text-muted">{fmtDate(j.created_at)}</td></tr>)}</tbody></table></div>
      )}
    </div>
  )
}
