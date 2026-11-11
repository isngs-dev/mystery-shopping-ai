import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Search } from 'lucide-react'
import { api, qs } from '../lib/api'
import { Alert, Badge, PageHeader, Pager, SkeletonRows } from '../components/ui'
import { EmptyState } from '../components/Illustrations'
import { useCopilotHint } from '../lib/copilot'

const TONE: Record<string, string> = { Finalized: 'ok', Incomplete: 'warn', 'Hold B': 'danger', 'Edited & Saved': 'brand', Accepted: 'ok' }
const COLORS = ['#c98a52', '#3f8f66', '#e8b24a', '#9dcdaa', '#e26a58', '#d9a07a', '#7ccf88']

export default function Tracker() {
  const [f, setF] = useState({ q: '', form: '', editor: '', ops_status: '', sheet: '' })
  const [offset, setOffset] = useState(0)
  const limit = 40
  const sum = useQuery({ queryKey: ['tracker-sum'], queryFn: () => api.get<any>('/api/integrations/tracker/summary') })
  const list = useQuery({ queryKey: ['tracker', f, offset], queryFn: () => api.get<any>('/api/integrations/tracker' + qs({ ...f, offset, limit })) })
  useCopilotHint('idle', 'This is the operations tracker imported from Drive (read-only). Shop IDs that match a form in the repository link straight to it.')
  const set = (k: string, v: string) => { setF({ ...f, [k]: v }); setOffset(0) }
  const s = sum.data
  const tip = { contentStyle: { background: 'rgb(var(--surface))', border: '1px solid rgb(var(--line) / .3)', borderRadius: 12, fontSize: 12 } }

  return (
    <div>
      <PageHeader title="Production tracker" sub={<>Read-only view of the tracker spreadsheet imported from Drive{s?.sources?.length ? <> ({s.sources.join(', ')})</> : null}. Credentials found in the sheet are never imported.</>} />
      {sum.isLoading ? <SkeletonRows rows={3} /> : !s?.total ? (
        <div className="card"><EmptyState kind="docs" title="No tracker data yet">Import the Drive folder (it contains the tracker .xlsx) from <Link className="text-brand hover:underline" to="/app/admin/drive">Google Drive import</Link>.</EmptyState></div>
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-3">
            {([['Rows by OPS status', s.by_status, 'ops_status'], ['Rows by editor', s.by_editor, 'editor'], ['Rows by form', s.by_form, 'form']] as const).map(([title, data, key]) => (
              <div key={title} className="card p-4"><h3 className="text-sm font-bold">{title}</h3><p className="text-xs text-muted">{s.total} rows · click a bar to filter</p>
                <div className="mt-2 h-52"><ResponsiveContainer><BarChart data={data} layout="vertical" margin={{ left: 40 }}><CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--line) / .15)" /><XAxis type="number" stroke="rgb(var(--muted))" fontSize={11} allowDecimals={false} /><YAxis type="category" dataKey="name" stroke="rgb(var(--muted))" fontSize={11} width={110} /><Tooltip {...tip} />
                  <Bar dataKey="count" radius={[0, 6, 6, 0]} cursor="pointer" onClick={(d: any) => set(key, d.name === '—' ? '' : d.name)}>{data.map((_: any, i: number) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}</Bar></BarChart></ResponsiveContainer></div></div>
            ))}
          </div>
          <div className="card mt-4 grid gap-3 p-3 sm:grid-cols-3 lg:grid-cols-5">
            <div className="relative sm:col-span-2"><Search size={15} className="absolute left-3 top-3 text-muted" /><input className="input !pl-9" placeholder="Search shop ID, location, form, comment…" value={f.q} onChange={(e) => set('q', e.target.value)} aria-label="Search tracker" /></div>
            <select className="input" value={f.sheet} onChange={(e) => set('sheet', e.target.value)} aria-label="Sheet"><option value="">All sheets</option>{s.by_sheet.map((x: any) => <option key={x.name}>{x.name}</option>)}</select>
            <select className="input" value={f.ops_status} onChange={(e) => set('ops_status', e.target.value)} aria-label="OPS status"><option value="">Any status</option>{s.by_status.map((x: any) => x.name !== '—' && <option key={x.name}>{x.name}</option>)}</select>
            <button className="btn-ghost" onClick={() => { setF({ q: '', form: '', editor: '', ops_status: '', sheet: '' }); setOffset(0) }}>Clear filters</button>
          </div>
          {(f.form || f.editor) && <div className="mt-2 flex gap-2 text-xs">{f.form && <Badge t="brand">Form: {f.form}</Badge>}{f.editor && <Badge t="brand">Editor: {f.editor}</Badge>}</div>}
          <div className="card mt-3 overflow-x-auto">
            {list.isLoading ? <SkeletonRows /> : list.isError ? <div className="p-4"><Alert kind="error">{(list.error as Error).message}</Alert></div> : list.data.items.length === 0 ? <EmptyState kind="search" title="No rows match" /> : (
              <table className="w-full min-w-[900px]"><thead><tr className="border-b"><th className="th">Shop ID</th><th className="th">Form / location</th><th className="th">Shop date</th><th className="th">Editor</th><th className="th">OPS status</th><th className="th">QC</th><th className="th">Comments to shopper</th></tr></thead>
                <tbody>{list.data.items.map((r: any) => (
                  <tr key={r.id} className="border-b align-top last:border-0 hover:bg-surface2/50">
                    <td className="td font-mono text-xs">{r.form_id ? <Link className="font-bold text-brand hover:underline" to={`/app/forms/${r.form_id}`}>{r.shop_id}</Link> : r.shop_id}<span className="block text-[10px] text-muted">{r.sheet}</span></td>
                    <td className="td"><b>{r.form}</b><span className="block text-xs text-muted">{r.location}</span></td><td className="td text-muted">{r.shop_date}</td><td className="td">{r.editor}</td>
                    <td className="td">{r.ops_status && <Badge t={TONE[r.ops_status] || 'muted'}>{r.ops_status}</Badge>}</td>
                    <td className="td text-xs">{r.qc_member && <span className="block">{r.qc_member}</span>}{r.qc_status && <Badge t={TONE[r.qc_status] || 'muted'}>{r.qc_status}</Badge>}</td>
                    <td className="td max-w-sm text-xs text-muted"><span className="line-clamp-3">{r.comments_to_shopper}</span></td></tr>))}</tbody></table>)}
            {list.data && <Pager total={list.data.total} limit={limit} offset={offset} onChange={setOffset} />}
          </div>
        </>
      )}
    </div>
  )
}
