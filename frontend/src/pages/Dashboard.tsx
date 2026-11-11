import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, qs } from '../lib/api'
import { hrs, pct, STATUS } from '../lib/format'
import { Alert, PageHeader, Skeleton, Stat, Field } from '../components/ui'
import { EmptyState } from '../components/Illustrations'
import { useCopilotHint } from '../lib/copilot'
import { useAuth } from '../lib/auth'

const COLORS = ['#c98a52', '#3f8f66', '#9dcdaa', '#7ccf88', '#e8b24a', '#e26a58', '#d9a07a', '#a4b29c']
const STATUS_COLOR: Record<string, string> = { pending: '#a4b29c', in_progress: '#c98a52', qc_review: '#3f8f66', re_edit_required: '#e8b24a', approved: '#7ccf88', closed: '#4fae6c', manual_review_required: '#d9772e', blocked: '#e26a58' }

function ChartCard({ title, sub, children, empty }: { title: string; sub?: string; children: React.ReactNode; empty?: boolean }) {
  return (
    <div className="card p-4">
      <h3 className="text-sm font-bold">{title}</h3>{sub && <p className="text-xs text-muted">{sub}</p>}
      <div className="mt-3 h-64">{empty ? <p className="grid h-full place-items-center text-sm text-muted">No data for these filters yet.</p> : children}</div>
    </div>
  )
}
const tip = { contentStyle: { background: 'rgb(var(--surface))', border: '1px solid rgb(var(--line) / .3)', borderRadius: 12, fontSize: 12 }, labelStyle: { color: 'rgb(var(--ink))' } }
const axis = { stroke: 'rgb(var(--muted))', fontSize: 11 }

export default function Dashboard() {
  const nav = useNavigate()
  const { session } = useAuth()
  const [f, setF] = useState({ date_from: '', date_to: '', project_id: '', severity: '', data: 'real' })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get<any[]>('/api/projects') })
  const q = useQuery({
    queryKey: ['dashboard', f], refetchInterval: 30000,
    queryFn: () => api.get<any>('/api/dashboard' + qs({ ...f, date_from: f.date_from && new Date(f.date_from).toISOString(), date_to: f.date_to && new Date(f.date_to + 'T23:59:59').toISOString() })),
  })
  const d = q.data
  const k = d?.kpis
  useCopilotHint(q.isLoading ? 'thinking' : k?.manual_review || k?.blocked || k?.overdue ? 'alert' : 'idle',
    q.isLoading ? 'Gathering the latest numbers…' : k?.overdue ? `${k.overdue} form(s) are past their due date. Open the Review Queue to take action.` : k?.manual_review || k?.blocked ? `${k.manual_review + k.blocked} form(s) need manual attention.` : 'All clear. Click any tile to drill into the forms behind it.')
  const go = (status: string) => nav(`/app/queue?status=${status}`)

  return (
    <div>
      <PageHeader title="Operations dashboard" sub={session?.user.role === 'editor' ? 'Your assigned work and results.' : 'Live metrics from the review database. Unique forms and review attempts are counted separately.'} />
      <div className="card mb-5 grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="From (intake date)"><input type="date" className="input" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></Field>
        <Field label="To"><input type="date" className="input" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></Field>
        <Field label="Project"><select className="input" value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}><option value="">All projects</option>{projects.data?.map((p) => <option key={p.id} value={p.id}>{p.client_name} — {p.name}</option>)}</select></Field>
        <Field label="Finding severity"><select className="input" value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}><option value="">All severities</option>{['critical', 'major', 'minor', 'info'].map((s) => <option key={s}>{s}</option>)}</select></Field>
      </div>

      {q.isError && <Alert kind="error" title="Could not load the dashboard">{(q.error as Error).message}</Alert>}
      {d?.sample_data.notice && f.data !== 'real' && <div className="mb-4"><Alert kind="warn" title="Sample data is included">{d.sample_data.sample_forms} of {k.total_forms} forms are labelled SAMPLE demo records. Choose “Real only” to hide them.</Alert></div>}
      {q.isLoading && <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-24" />)}</div>}
      {d && k.total_forms === 0 && <div className="card"><EmptyState kind="docs" title="No forms match these filters">Upload a RAW and edited PDF pair in the Repository, or widen the filters.</EmptyState></div>}
      {d && k.total_forms > 0 && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Unique forms indexed" value={k.total_forms} sub={`${k.review_attempts} review attempts · ${k.resubmissions} resubmissions`} hint={d.definitions.unique_forms} onClick={() => nav('/app/repository')} />
            <Stat label="Eligible for QC" value={k.eligible_for_qc} sub="RAW + edited pair present" tone="cyan" onClick={() => go('qc_review,in_progress,re_edit_required')} />
            <Stat label="Pending · backlog" value={`${k.pending} · ${k.backlog}`} sub={k.overdue ? `${k.overdue} overdue` : 'none overdue'} tone={k.overdue ? 'danger' : 'brand'} onClick={() => go('pending,in_progress,qc_review,re_edit_required,manual_review_required,blocked')} />
            <Stat label="Under QC review" value={k.under_review} tone="violet" onClick={() => go('qc_review')} />
            <Stat label="Approved" value={k.approved} tone="ok" onClick={() => go('approved,closed')} />
            <Stat label="Manual review" value={k.manual_review} tone="warn" onClick={() => go('manual_review_required')} />
            <Stat label="Blocked (input)" value={k.blocked} tone="danger" sub="missing / invalid input" onClick={() => go('blocked')} />
            <Stat label="Re-edit required" value={k.re_edit_required} tone="warn" onClick={() => go('re_edit_required')} />
            <Stat label="QC coverage" value={pct(k.qc_coverage)} hint={d.definitions.qc_coverage} sub="submitted forms with a human decision" tone="cyan" />
            <Stat label="First-pass approval" value={pct(k.first_pass_approval_rate)} hint={d.definitions.first_pass_approval_rate} tone="ok" />
            <Stat label="Avg turnaround" value={hrs(k.avg_turnaround_h)} hint={d.definitions.avg_turnaround_h} sub="intake → approval" />
            <Stat label="Re-edit rate" value={pct(k.re_edit_rate)} hint={d.definitions.re_edit_rate} tone="warn" />
          </div>

          <div className="mt-5 grid gap-4 lg:grid-cols-2">
            <ChartCard title="Forms by status" sub="Click a slice to open those forms" empty={!d.status_distribution.length}>
              <ResponsiveContainer><PieChart><Pie data={d.status_distribution.map((s: any) => ({ ...s, name: STATUS[s.status]?.label }))} dataKey="count" nameKey="name" innerRadius={55} outerRadius={90} paddingAngle={2} onClick={(s: any) => go(s.status)} cursor="pointer">
                {d.status_distribution.map((s: any) => <Cell key={s.status} fill={STATUS_COLOR[s.status] || '#a4b29c'} />)}</Pie><Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 11 }} /></PieChart></ResponsiveContainer>
            </ChartCard>
            <ChartCard title="Review decisions per week" sub="Approvals vs re-edits vs manual / blocked" empty={!d.weekly_decisions.length}>
              <ResponsiveContainer><BarChart data={d.weekly_decisions}><CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--line) / .15)" /><XAxis dataKey="week" {...axis} tickFormatter={(w) => w.slice(5)} /><YAxis {...axis} allowDecimals={false} /><Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="approved" stackId="a" fill="#7ccf88" name="Approved" radius={[0, 0, 0, 0]} /><Bar dataKey="re_edit" stackId="a" fill="#e8b24a" name="Re-edit" /><Bar dataKey="manual" stackId="a" fill="#e26a58" name="Manual / blocked" radius={[6, 6, 0, 0]} /></BarChart></ResponsiveContainer>
            </ChartCard>
            <ChartCard title="Confirmed findings by category" sub="Only findings a reviewer accepted" empty={!d.findings_by_category.length}>
              <ResponsiveContainer><BarChart data={d.findings_by_category} layout="vertical" margin={{ left: 30 }}><CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--line) / .15)" /><XAxis type="number" {...axis} allowDecimals={false} /><YAxis type="category" dataKey="category" {...axis} width={110} /><Tooltip {...tip} /><Bar dataKey="count" radius={[0, 6, 6, 0]}>{d.findings_by_category.map((_: any, i: number) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}</Bar></BarChart></ResponsiveContainer>
            </ChartCard>
            <ChartCard title="All findings by severity" sub="Across all analysis runs" empty={!d.findings_by_severity.some((s: any) => s.count)}>
              <ResponsiveContainer><BarChart data={d.findings_by_severity}><CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--line) / .15)" /><XAxis dataKey="severity" {...axis} /><YAxis {...axis} allowDecimals={false} /><Tooltip {...tip} /><Bar dataKey="count" radius={[6, 6, 0, 0]}>{d.findings_by_severity.map((s: any) => <Cell key={s.severity} fill={{ critical: '#e26a58', major: '#e8b24a', minor: '#c98a52', info: '#a4b29c' }[s.severity as 'info']} />)}</Bar></BarChart></ResponsiveContainer>
            </ChartCard>
          </div>

          <div className="mt-5 grid gap-4 lg:grid-cols-3">
            {(['project', 'editor', 'reviewer'] as const).map((g) => (
              <ChartCard key={g} title={`Workload by ${g}`} sub="Open vs completed forms" empty={!d.workload[g].length}>
                <ResponsiveContainer><BarChart data={d.workload[g]} layout="vertical" margin={{ left: 40 }}><CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--line) / .15)" /><XAxis type="number" {...axis} allowDecimals={false} /><YAxis type="category" dataKey="name" {...axis} width={120} /><Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 11 }} /><Bar dataKey="open" stackId="w" fill="#3f8f66" name="Open" /><Bar dataKey="done" stackId="w" fill="#7ccf88" name="Done" radius={[0, 6, 6, 0]} /></BarChart></ResponsiveContainer>
              </ChartCard>
            ))}
          </div>
          {d.repeat_error_categories.length > 0 && (
            <div className="card mt-5 p-4"><h3 className="text-sm font-bold">Repeat error trend</h3><p className="text-xs text-muted">Categories confirmed again after a re-edit (same form, multiple attempts)</p>
              <div className="mt-3 flex flex-wrap gap-2">{d.repeat_error_categories.map((r: any) => <span key={r.category} className="rounded-full border border-warn/30 bg-warn/10 px-3 py-1 text-xs font-semibold text-warn">{r.category} · {r.forms} form{r.forms > 1 ? 's' : ''}</span>)}</div></div>
          )}
        </>
      )}
    </div>
  )
}
