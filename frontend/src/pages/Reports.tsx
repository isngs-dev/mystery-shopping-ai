import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { api, download, qs } from '../lib/api'
import { Alert, Field, PageHeader, SkeletonRows, Tabs, useToast } from '../components/ui'
import { EmptyState } from '../components/Illustrations'
import { useCopilotHint } from '../lib/copilot'

const HEAD = (c: string) => c.replace(/_%/g, ' %').replace(/_/g, ' ')

export default function Reports() {
  const toast = useToast()
  const cat = useQuery({ queryKey: ['report-catalog'], queryFn: () => api.get<{ key: string; title: string }[]>('/api/reports') })
  const [key, setKey] = useState('')
  const [f, setF] = useState({ date_from: '', date_to: '', project_id: '', data: 'real' })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get<any[]>('/api/projects') })
  useEffect(() => { if (!key && cat.data?.length) setKey(cat.data[0].key) }, [cat.data, key])
  const params = { ...f, date_from: f.date_from && new Date(f.date_from).toISOString(), date_to: f.date_to && new Date(f.date_to + 'T23:59:59').toISOString() }
  const rep = useQuery({ queryKey: ['report', key, f], queryFn: () => api.get<any>(`/api/reports/${key}` + qs(params)), enabled: !!key })
  useCopilotHint(rep.isFetching ? 'thinking' : 'idle', rep.isFetching ? 'Crunching the numbers from the review database…' : 'Exports contain exactly the rows you see, with the same filters applied.')
  const exp = async (fmt: 'csv' | 'xlsx') => { try { await download(`/api/reports/${key}/export` + qs({ ...params, fmt }), `${key}.${fmt}`) } catch (e: any) { toast('error', e.message) } }
  const r = rep.data
  const summary = r?.summary || {}

  return (
    <div>
      <PageHeader title="Reports & analytics" sub="Scoped to your role. Unique forms and repeat review attempts are reported separately." actions={<><button className="btn-ghost" onClick={() => exp('csv')} disabled={!r}><Download size={15} /> CSV</button><button className="btn-primary" onClick={() => exp('xlsx')} disabled={!r}><Download size={15} /> Excel</button></>} />
      {cat.data && <Tabs value={key} onChange={setKey} tabs={cat.data.map((c) => ({ key: c.key, label: c.title }))} />}
      <div className="card mb-4 grid gap-3 p-3 sm:grid-cols-4">
        <Field label="From"><input type="date" className="input" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></Field>
        <Field label="To"><input type="date" className="input" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></Field>
        <Field label="Project"><select className="input" value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}><option value="">All</option>{projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
      </div>
      {rep.isError && <Alert kind="error">{(rep.error as Error).message}</Alert>}
      {r?.notes?.map((n: string, i: number) => <p key={i} className="mb-2 text-xs text-muted">ℹ {n}</p>)}
      {Object.keys(summary).length > 0 && (
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Object.entries(summary).filter(([, v]) => typeof v !== 'object').map(([k, v]) => <div key={k} className="card p-3"><p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{HEAD(k)}</p><p className="text-2xl font-extrabold tabular-nums">{String(v ?? '—')}</p></div>)}
          {Object.entries(summary).filter(([, v]) => v && typeof v === 'object' && !Array.isArray(v)).map(([k, v]) => <div key={k} className="card p-3"><p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{HEAD(k)}</p><p className="mt-1 text-sm">{Object.entries(v as any).map(([a, b]) => `${a}: ${b}`).join(' · ') || '—'}</p></div>)}
        </div>
      )}
      <div className="card overflow-x-auto">
        {rep.isLoading ? <SkeletonRows /> : !r || r.rows.length === 0 ? <EmptyState kind="search" title="No data for this report yet">Adjust the filters, or complete some QC reviews to populate it.</EmptyState> : (
          <table className="w-full"><thead><tr className="border-b">{r.columns.map((c: string) => <th key={c} className="th whitespace-nowrap">{HEAD(c)}</th>)}</tr></thead>
            <tbody>{r.rows.map((row: any, i: number) => <tr key={i} className="border-b last:border-0 hover:bg-surface2/50">{r.columns.map((c: string) => <td key={c} className="td tabular-nums">{row[c] === null || row[c] === undefined ? <span className="text-muted">—</span> : String(row[c])}</td>)}</tr>)}</tbody></table>
        )}
      </div>
      {key === 'ai_performance' && r?.summary?.by_rule?.length > 0 && (
        <div className="card mt-4 overflow-x-auto"><h3 className="p-4 pb-0 font-bold">Reviewer decisions by rule</h3>
          <table className="w-full"><thead><tr className="border-b"><th className="th">Rule</th><th className="th">Confirmed</th><th className="th">Rejected</th></tr></thead><tbody>{r.summary.by_rule.map((x: any) => <tr key={x.rule} className="border-b last:border-0"><td className="td">{x.rule}</td><td className="td tabular-nums">{x.confirmed}</td><td className="td tabular-nums">{x.rejected}</td></tr>)}</tbody></table></div>
      )}
    </div>
  )
}
