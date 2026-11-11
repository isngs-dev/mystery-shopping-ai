import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, qs } from '../lib/api'
import { ago, fmtDate, STATUS } from '../lib/format'
import { Badge, PageHeader, Pager, SkeletonRows, StatusBadge, Alert } from '../components/ui'
import { EmptyState } from '../components/Illustrations'
import { useAuth } from '../lib/auth'
import { useCopilotHint } from '../lib/copilot'

const GROUPS: { label: string; value: string }[] = [
  { label: 'All open', value: 'pending,in_progress,qc_review,re_edit_required,manual_review_required,blocked' },
  { label: 'Needs QC', value: 'qc_review' }, { label: 'Re-edit', value: 're_edit_required' }, { label: 'In progress', value: 'in_progress' },
  { label: 'Pending', value: 'pending' }, { label: 'Manual review', value: 'manual_review_required' }, { label: 'Blocked', value: 'blocked' },
  { label: 'Approved / closed', value: 'approved,closed' },
]

/** Review queue (reviewers/managers/admins) and "My work" (editors): the same server-scoped list. */
export default function Queue() {
  const { session } = useAuth()
  const [sp, setSp] = useSearchParams()
  const status = sp.get('status') || GROUPS[0].value
  const [mine, setMine] = useState(session?.user.role === 'qc_reviewer')
  const [offset, setOffset] = useState(0)
  const limit = 25
  const q = useQuery({ queryKey: ['queue', status, mine, offset], queryFn: () => api.get<any>('/api/forms' + qs({ status, mine, offset, limit, sort: 'due' })), refetchInterval: 15000 })
  const editor = session?.user.role === 'editor'
  useCopilotHint('idle', editor ? 'These are the forms assigned to you. Open one to see QC feedback and upload a revised PDF.' : 'Pick a form to open its QC workspace. Overdue items are flagged in red.')
  const now = Date.now()

  return (
    <div>
      <PageHeader title={editor ? 'My work' : 'Review queue'} sub={editor ? 'Forms assigned to you, with QC feedback when a form is sent back.' : 'Pending → In Progress → QC Review → Re-edit Required / Approved → Closed.'}
        actions={!editor && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={mine} onChange={(e) => { setMine(e.target.checked); setOffset(0) }} /> Assigned to me</label>} />
      <div className="mb-4 flex flex-wrap gap-2">
        {GROUPS.map((g) => <button key={g.label} onClick={() => { setSp({ status: g.value }); setOffset(0) }} className={`rounded-full border px-3 py-1 text-xs font-semibold transition ${status === g.value ? 'border-brand bg-brand/20 text-ink' : 'text-muted hover:text-ink'}`}>{g.label}</button>)}
        {!GROUPS.some((g) => g.value === status) && <Badge t="brand">Custom: {status.split(',').map((s) => STATUS[s]?.label || s).join(', ')}</Badge>}
      </div>
      {q.isError && <Alert kind="error">{(q.error as Error).message}</Alert>}
      <div className="card overflow-x-auto">
        {q.isLoading ? <SkeletonRows /> : q.data.items.length === 0 ? <EmptyState kind="success" title="Nothing here">No forms match this view.</EmptyState> : (
          <table className="w-full min-w-[820px]"><thead><tr className="border-b"><th className="th">Form</th><th className="th">Client / project</th><th className="th">Status</th><th className="th">Editor</th><th className="th">Reviewer</th><th className="th">Versions</th><th className="th">Due</th><th className="th">Updated</th></tr></thead>
            <tbody>{q.data.items.map((f: any) => {
              const overdue = f.due_at && new Date(f.due_at).getTime() < now && !['approved', 'closed'].includes(f.status)
              return (
                <tr key={f.id} className="border-b last:border-0 hover:bg-surface2/50">
                  <td className="td"><Link className="font-bold text-brand hover:underline" to={`/app/forms/${f.id}`}>{f.form_code}</Link>{f.is_sample && <Badge t="warn" className="ml-2">SAMPLE</Badge>}</td>
                  <td className="td text-muted">{f.client}<span className="block text-xs">{f.project}</span></td>
                  <td className="td"><StatusBadge status={f.status} /></td>
                  <td className="td">{f.assigned_editor?.name || <span className="text-muted">Unassigned</span>}</td>
                  <td className="td">{f.assigned_reviewer?.name || <span className="text-muted">—</span>}</td>
                  <td className="td tabular-nums">{f.edited_versions || '—'}</td>
                  <td className={`td ${overdue ? 'font-semibold text-danger' : 'text-muted'}`}>{f.due_at ? fmtDate(f.due_at, false) : '—'}{overdue && ' · overdue'}</td>
                  <td className="td text-muted">{ago(f.last_activity_at)}</td>
                </tr>
              )
            })}</tbody></table>
        )}
        {q.data && <Pager total={q.data.total} limit={limit} offset={offset} onChange={setOffset} />}
      </div>
    </div>
  )
}
