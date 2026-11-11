import { Fragment, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, KeyRound, ShieldCheck, UserPlus } from 'lucide-react'
import { api, qs } from '../lib/api'
import { fmtDate, ROLE_LABEL } from '../lib/format'
import { Alert, Badge, Field, Modal, PageHeader, Pager, SkeletonRows, Tabs, useToast } from '../components/ui'
import { EmptyState } from '../components/Illustrations'
import { useAuth } from '../lib/auth'
import { useCopilotHint } from '../lib/copilot'

const ROLES = Object.keys(ROLE_LABEL)

function AccessModal({ user, projects, onClose }: { user: any; projects: any[]; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [sel, setSel] = useState<Set<number>>(new Set(user.project_ids || []))
  const m = useMutation({ mutationFn: () => api.put(`/api/users/${user.id}/access`, { project_ids: [...sel] }), onSuccess: () => { toast('ok', 'Access updated'); qc.invalidateQueries({ queryKey: ['users'] }); qc.invalidateQueries({ queryKey: ['access-list'] }); onClose() }, onError: (e: any) => toast('error', e.message) })
  const grouped = projects.reduce((a: Record<string, any[]>, p) => ((a[p.client_name] ||= []).push(p), a), {})
  return (
    <Modal open onClose={onClose} title={`Project access — ${user.name}`} wide>
      <p className="mb-3 text-sm text-muted">New accounts start with no access. Grant only the clients and projects this person needs.</p>
      <div className="max-h-80 space-y-3 overflow-auto">{Object.entries(grouped).map(([c, ps]) => (
        <div key={c}><p className="mb-1 text-xs font-bold uppercase tracking-wider text-muted">{c}</p>
          {ps.map((p) => <label key={p.id} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-surface2"><input type="checkbox" checked={sel.has(p.id)} onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(p.id) : n.delete(p.id); setSel(n) }} />{p.name}</label>)}</div>))}
        {projects.length === 0 && <p className="text-sm text-muted">No projects available to assign.</p>}</div>
      <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={() => m.mutate()} disabled={m.isPending}>Save access</button></div>
    </Modal>
  )
}

function Secret({ title, value, onClose }: { title: string; value: string; onClose: () => void }) {
  const toast = useToast()
  return (
    <Modal open onClose={onClose} title={title}>
      <Alert kind="warn" title="Shown only once">Copy this temporary password now and share it securely. The user must change it at first sign-in.</Alert>
      <div className="mt-3 flex items-center gap-2"><code className="flex-1 rounded-xl border bg-bg/60 px-3 py-2 font-mono text-sm">{value}</code>
        <button className="btn-ghost" onClick={() => { navigator.clipboard?.writeText(value); toast('ok', 'Copied') }} aria-label="Copy password"><Copy size={15} /></button></div>
      <div className="mt-4 flex justify-end"><button className="btn-primary" onClick={onClose}>Done</button></div>
    </Modal>
  )
}

export function UsersPage() {
  const { session } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const users = useQuery({ queryKey: ['users'], queryFn: () => api.get<any[]>('/api/users') })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get<any[]>('/api/projects') })
  const [create, setCreate] = useState(false)
  const [form, setForm] = useState({ email: '', name: '', role: 'editor' })
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(null)
  const [access, setAccess] = useState<any>(null)
  useCopilotHint('idle', 'Only the Super Admin creates accounts. New accounts have no project access until you grant it.')
  const refresh = () => qc.invalidateQueries({ queryKey: ['users'] })
  const add = useMutation({ mutationFn: () => api.post<any>('/api/users', form), onSuccess: (r) => { setCreate(false); setForm({ email: '', name: '', role: 'editor' }); refresh(); setSecret({ title: `Account created — ${r.user.name}`, value: r.temporary_password }) }, onError: (e: any) => toast('error', e.message) })
  const patch = useMutation({ mutationFn: (v: { id: number; body: any }) => api.patch(`/api/users/${v.id}`, v.body), onSuccess: () => { toast('ok', 'Account updated'); refresh() }, onError: (e: any) => toast('error', e.message) })
  const reset = useMutation({ mutationFn: (id: number) => api.post<any>(`/api/users/${id}/reset-password`), onSuccess: (r, id) => setSecret({ title: 'Password reset', value: r.temporary_password }), onError: (e: any) => toast('error', e.message) })

  return (
    <div>
      <PageHeader title="Users & roles" sub="Level 1 Super Admin → Level 2 Admin → Level 3 Editor & Manager → Level 4 Editor. QC reviewers and other roles sit alongside." actions={<button className="btn-primary" onClick={() => setCreate(true)}><UserPlus size={16} /> Create account</button>} />
      <div className="card overflow-x-auto">
        {users.isLoading ? <SkeletonRows /> : (
          <table className="w-full min-w-[820px]"><thead><tr className="border-b"><th className="th">Name</th><th className="th">Role</th><th className="th">Projects</th><th className="th">Last sign-in</th><th className="th">Status</th><th className="th" /></tr></thead>
            <tbody>{users.data!.map((u) => (
              <tr key={u.id} className="border-b last:border-0">
                <td className="td"><b>{u.name}</b>{u.is_sample && <Badge t="warn" className="ml-2">SAMPLE</Badge>}<span className="block text-xs text-muted">{u.email}</span></td>
                <td className="td"><select className="input !w-auto !py-1 text-xs" value={u.role} disabled={u.id === session!.user.id} onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value } })} aria-label={`Role for ${u.name}`}>{ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select></td>
                <td className="td tabular-nums">{u.role === 'super_admin' ? 'All' : u.project_ids.length}</td>
                <td className="td text-muted">{fmtDate(u.last_login_at)}</td>
                <td className="td">{u.is_active ? <Badge t="ok">Active</Badge> : <Badge t="danger">Deactivated</Badge>}{u.must_change_password && <Badge t="warn" className="ml-1">Temp password</Badge>}{u.is_coordinator && <Badge t="brand" className="ml-1">Coordinator</Badge>}</td>
                <td className="td"><div className="flex justify-end gap-1">
                  {['qc_reviewer', 'admin'].includes(u.role) && <button className="btn-ghost !py-1 text-xs" title="A coordinator sees every project and hands projects to QC leads" onClick={() => patch.mutate({ id: u.id, body: { is_coordinator: !u.is_coordinator } })}>{u.is_coordinator ? 'Remove coordinator' : 'Make coordinator'}</button>}
                  {u.role !== 'super_admin' && <button className="btn-ghost !py-1 text-xs" onClick={() => setAccess(u)}><ShieldCheck size={13} /> Access</button>}
                  <button className="btn-ghost !py-1 text-xs" onClick={() => reset.mutate(u.id)}><KeyRound size={13} /> Reset</button>
                  {u.id !== session!.user.id && <button className={u.is_active ? 'btn-danger !py-1 text-xs' : 'btn-ok !py-1 text-xs'} onClick={() => patch.mutate({ id: u.id, body: { is_active: !u.is_active } })}>{u.is_active ? 'Deactivate' : 'Reactivate'}</button>}</div></td></tr>))}</tbody></table>
        )}
      </div>
      <Modal open={create} onClose={() => setCreate(false)} title="Create account">
        <div className="space-y-3"><Field label="Full name"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Email"><input type="email" className="input" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <Field label="Role"><select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select></Field>
          <p className="text-xs text-muted">A temporary password is generated and shown once. Grant project access after creating the account.</p>
          <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={() => setCreate(false)}>Cancel</button><button className="btn-primary" disabled={add.isPending || form.name.length < 2 || !form.email.includes('@')} onClick={() => add.mutate()}>Create</button></div></div>
      </Modal>
      {access && <AccessModal user={access} projects={projects.data || []} onClose={() => setAccess(null)} />}
      {secret && <Secret {...secret} onClose={() => setSecret(null)} />}
    </div>
  )
}

export function OrgPage() {
  const { can } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const [tab, setTab] = useState<'org' | 'access'>(can('orgs.manage') ? 'org' : 'access')
  const clients = useQuery({ queryKey: ['clients'], queryFn: () => api.get<any[]>('/api/clients') })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get<any[]>('/api/projects') })
  const people = useQuery({ queryKey: ['access-list'], queryFn: () => api.get<any[]>('/api/users/access-list'), enabled: can('access.grant') })
  const [cn, setCn] = useState('')
  const [pf, setPf] = useState({ client_id: '', name: '', form_type: 'standard' })
  const [access, setAccess] = useState<any>(null)
  useCopilotHint('idle', 'Each client has its own folder, and each project or editor has a dedicated subfolder beneath it.')
  const done = (m: string) => { toast('ok', m); qc.invalidateQueries({ queryKey: ['clients'] }); qc.invalidateQueries({ queryKey: ['projects'] }); qc.invalidateQueries({ queryKey: ['tree'] }) }
  const addClient = useMutation({ mutationFn: () => api.post('/api/clients', { name: cn }), onSuccess: () => { setCn(''); done('Client folder created') }, onError: (e: any) => toast('error', e.message) })
  const addProject = useMutation({ mutationFn: () => api.post('/api/projects', { ...pf, client_id: +pf.client_id }), onSuccess: () => { setPf({ ...pf, name: '' }); done('Project folder created') }, onError: (e: any) => toast('error', e.message) })
  const toggle = useMutation({ mutationFn: (p: any) => api.patch(`/api/projects/${p.id}`, { is_active: !p.is_active }), onSuccess: () => done('Project updated'), onError: (e: any) => toast('error', e.message) })
  return (
    <div>
      <PageHeader title="Clients, projects & access" sub="Workspace / Client / Project or Editor. Access is explicit — no role gets every client automatically." />
      <Tabs value={tab} onChange={setTab} tabs={[...(can('orgs.manage') ? [{ key: 'org' as const, label: 'Clients & projects' }] : []), ...(can('access.grant') ? [{ key: 'access' as const, label: 'Project access' }] : [])]} />
      {tab === 'org' && (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-4">
            <div className="card space-y-3 p-4"><h3 className="font-bold">New client</h3><Field label="Client name"><input className="input" value={cn} onChange={(e) => setCn(e.target.value)} /></Field><button className="btn-primary w-full" disabled={cn.trim().length < 2 || addClient.isPending} onClick={() => addClient.mutate()}>Create client folder</button></div>
            <div className="card space-y-3 p-4"><h3 className="font-bold">New project / editor folder</h3>
              <Field label="Client"><select className="input" value={pf.client_id} onChange={(e) => setPf({ ...pf, client_id: e.target.value })}><option value="">Select…</option>{clients.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
              <Field label="Project or editor name"><input className="input" value={pf.name} onChange={(e) => setPf({ ...pf, name: e.target.value })} /></Field>
              <Field label="Form type"><input className="input" value={pf.form_type} onChange={(e) => setPf({ ...pf, form_type: e.target.value })} /></Field>
              <button className="btn-primary w-full" disabled={!pf.client_id || pf.name.trim().length < 2 || addProject.isPending} onClick={() => addProject.mutate()}>Create project folder</button></div>
          </div>
          <div className="card overflow-x-auto lg:col-span-2">{projects.isLoading ? <SkeletonRows /> : (projects.data || []).length === 0 ? <EmptyState kind="docs" title="No projects yet">Create a client, then a project or editor folder.</EmptyState> : (
            <table className="w-full"><thead><tr className="border-b"><th className="th">Client</th><th className="th">Project / editor</th><th className="th">Form type</th><th className="th">Status</th><th className="th" /></tr></thead>
              <tbody>{projects.data!.map((p) => <tr key={p.id} className="border-b last:border-0"><td className="td">{p.client_name}</td><td className="td font-semibold">{p.name}{p.is_sample && <Badge t="warn" className="ml-2">SAMPLE</Badge>}</td><td className="td text-muted">{p.form_type}</td><td className="td">{p.is_active ? <Badge t="ok">Active</Badge> : <Badge t="muted">Inactive</Badge>}</td>
                <td className="td text-right"><button className="btn-ghost !py-1 text-xs" onClick={() => toggle.mutate(p)}>{p.is_active ? 'Deactivate' : 'Activate'}</button></td></tr>)}</tbody></table>)}</div>
        </div>
      )}
      {tab === 'access' && (
        <div className="card overflow-x-auto">{people.isLoading ? <SkeletonRows /> : (
          <table className="w-full min-w-[640px]"><thead><tr className="border-b"><th className="th">Person</th><th className="th">Role</th><th className="th">Projects granted</th><th className="th" /></tr></thead>
            <tbody>{(people.data || []).map((u) => <tr key={u.id} className="border-b last:border-0"><td className="td font-semibold">{u.name}<span className="block text-xs font-normal text-muted">{u.email}</span></td><td className="td">{u.role_label}</td><td className="td tabular-nums">{u.project_ids.length}</td>
              <td className="td text-right"><button className="btn-ghost !py-1 text-xs" onClick={() => setAccess(u)}><ShieldCheck size={13} /> Edit access</button></td></tr>)}</tbody></table>)}
          <p className="p-3 text-xs text-muted">Admins can change access only for lower-level roles and only for projects they hold themselves. Editors see forms assigned to them.</p></div>
      )}
      {access && <AccessModal user={access} projects={projects.data || []} onClose={() => setAccess(null)} />}
    </div>
  )
}

export function AuditPage() {
  const [f, setF] = useState({ action: '', q: '', form_id: '' })
  const [offset, setOffset] = useState(0)
  const [open, setOpen] = useState<number | null>(null)
  const limit = 50
  const q = useQuery({ queryKey: ['audit', f, offset], queryFn: () => api.get<any>('/api/audit' + qs({ ...f, offset, limit })) })
  useCopilotHint('idle', 'Every account change, upload, assignment, decision and rule version is recorded here.')
  const groups = ['', 'auth', 'user', 'access', 'document', 'form', 'qc', 'finding', 'rule', 'report', 'exception', 'project', 'client']
  return (
    <div>
      <PageHeader title="Audit trail" sub="Append-only record of important actions. Admins see events for their projects; the Super Admin sees everything." />
      <div className="card mb-3 grid gap-3 p-3 sm:grid-cols-3">
        <select className="input" value={f.action} onChange={(e) => { setF({ ...f, action: e.target.value }); setOffset(0) }} aria-label="Action group">{groups.map((g) => <option key={g} value={g}>{g ? `${g}.*` : 'All actions'}</option>)}</select>
        <input className="input" placeholder="Search actor or action…" value={f.q} onChange={(e) => { setF({ ...f, q: e.target.value }); setOffset(0) }} aria-label="Search audit" />
        <input className="input" placeholder="Form record ID" value={f.form_id} onChange={(e) => { setF({ ...f, form_id: e.target.value.replace(/\D/g, '') }); setOffset(0) }} aria-label="Form record ID" /></div>
      <div className="card overflow-x-auto">{q.isLoading ? <SkeletonRows rows={8} /> : q.data.items.length === 0 ? <EmptyState kind="search" title="No events match" /> : (
        <table className="w-full min-w-[760px]"><thead><tr className="border-b"><th className="th">When</th><th className="th">Actor</th><th className="th">Action</th><th className="th">Entity</th><th className="th" /></tr></thead>
          <tbody>{q.data.items.map((e: any) => (<Fragment key={e.id}>
            <tr className="border-b hover:bg-surface2/50"><td className="td whitespace-nowrap text-muted">{fmtDate(e.created_at)}</td><td className="td">{e.actor || 'system'}</td><td className="td"><Badge t="brand">{e.action}</Badge></td><td className="td text-xs text-muted">{e.entity_type}{e.entity_id ? ` #${e.entity_id}` : ''}{e.form_id ? ` · form ${e.form_id}` : ''}</td>
              <td className="td text-right">{e.detail && <button className="text-xs text-brand hover:underline" onClick={() => setOpen(open === e.id ? null : e.id)}>{open === e.id ? 'hide' : 'details'}</button>}</td></tr>
            {open === e.id && <tr className="border-b bg-surface2/40"><td colSpan={5} className="px-4 py-2"><pre className="overflow-auto text-xs">{JSON.stringify(e.detail, null, 2)}</pre></td></tr>}</Fragment>))}</tbody></table>)}
        {q.data && <Pager total={q.data.total} limit={limit} offset={offset} onChange={setOffset} />}</div>
    </div>
  )
}

export function SystemPage() {
  const q = useQuery({ queryKey: ['system'], queryFn: () => api.get<any>('/api/system/status'), refetchInterval: 20000 })
  useCopilotHint(q.data && (!q.data.ai.usable || !q.data.ocr.available) ? 'alert' : 'idle', q.data && !q.data.ai.usable ? 'AI analysis is not active. Deterministic checks still run and every run says so.' : 'Platform health and integration status.')
  if (q.isLoading) return <SkeletonRows rows={6} />
  if (q.isError) return <Alert kind="error">{(q.error as Error).message}</Alert>
  const s = q.data
  const Row = ({ label, ok, children }: { label: string; ok: boolean; children: React.ReactNode }) => (
    <div className="card p-4"><div className="flex items-center justify-between"><h3 className="font-bold">{label}</h3><Badge t={ok ? 'ok' : 'warn'}>{ok ? 'Ready' : 'Needs attention'}</Badge></div><div className="mt-2 space-y-1 text-sm text-muted">{children}</div></div>)
  return (
    <div>
      <PageHeader title="System status" sub="Integrations that need credentials or approval are listed honestly — nothing is simulated." />
      <div className="grid gap-4 md:grid-cols-2">
        <Row label="AI provider" ok={s.ai.usable}><p>Configured: <b className="text-ink">{s.ai.configured_provider}</b>{s.ai.model && <> · model <b className="text-ink">{s.ai.model}</b></>}</p><p>{s.ai.message}</p><p>External providers allowed: <b className="text-ink">{String(s.ai.allow_external)}</b></p></Row>
        <Row label="OCR (scanned PDFs)" ok={s.ocr.available}>{s.ocr.available ? <p>Tesseract at {s.ocr.command}</p> : <p>{s.ocr.reason} Scanned pages are reported as “OCR unavailable” and routed to manual review.</p>}</Row>
        <Row label="Document storage" ok={!!s.storage.configured}><p>Adapter: <b className="text-ink">{s.storage.adapter}</b></p>{s.storage.root && <p className="break-all">{s.storage.root}</p>}{s.storage.detail && <p>{s.storage.detail}</p>}</Row>
        <Row label="Platform" ok><p>Environment: {s.environment} · Database: {s.database}</p><p>{s.forms} forms indexed · {s.failed_runs} failed run(s)</p><p>Independent review policy (no self-approval): <b className="text-ink">{String(!s.policy.allow_self_review)}</b> · Upload limit {s.policy.max_upload_mb} MB</p></Row>
      </div>
    </div>
  )
}
