import { ArrowRight, Bot, CheckCircle2, CircleAlert, Loader2, ShieldCheck, TriangleAlert } from 'lucide-react'
import { ago } from '../lib/format'
import type { AutoEdit } from '../lib/types'
import { Alert, Badge } from './ui'

const STATUS: Record<string, { label: string; tone: string }> = {
  queued: { label: 'Queued', tone: 'muted' }, running: { label: 'Editing…', tone: 'brand' }, completed: { label: 'Edited & sent to QC', tone: 'ok' },
  no_changes: { label: 'Nothing more to fix', tone: 'muted' }, failed: { label: 'Could not edit', tone: 'danger' },
}

/** What the AI editor changed (and what it deliberately left for a human). Shown to editors, managers and QC reviewers. */
export function AiEditPanel({ edit, defaultOpen = true, selected, onSelect }: {
  edit: AutoEdit; defaultOpen?: boolean; selected?: number | 'all' | null; onSelect?: (i: number | 'all' | null) => void
}) {
  const st = STATUS[edit.status]
  const busy = edit.status === 'queued' || edit.status === 'running'
  const rules = edit.edits.filter((e) => e.source === 'rules').length
  const ai = edit.edits.filter((e) => e.source === 'ai').length
  return (
    <details open={defaultOpen || busy} className="card p-4">
      <summary className="flex cursor-pointer flex-wrap items-center gap-2">
        <Bot size={16} className="text-brand" /><span className="font-bold">AI editor</span>
        <Badge t={st.tone}>{busy && <Loader2 size={11} className="animate-spin" />}{edit.status === 'completed' && <CheckCircle2 size={11} />}{st.label}</Badge>
        {edit.engine && <Badge t={edit.engine === 'ai+rules' ? 'violet' : 'muted'}>{edit.engine === 'ai+rules' ? 'Language model + rules' : 'Built-in corrector (no LLM)'}</Badge>}
        <span className="text-xs text-muted">{edit.edits.length} correction{edit.edits.length === 1 ? '' : 's'} · {edit.skipped.length} left for a human · {ago(edit.finished_at || edit.created_at)}</span>
      </summary>
      <div className="mt-3 space-y-3 text-sm">
        {edit.message && <Alert kind={edit.status === 'failed' ? 'error' : 'info'}>{edit.message}</Alert>}
        {edit.stats?.verification && <p className="flex items-center gap-1.5 text-xs text-ok"><ShieldCheck size={14} />{edit.stats.verification}</p>}
        {edit.edits.length > 0 && onSelect && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <button className={`rounded-full border px-2.5 py-0.5 font-semibold ${selected === 'all' ? 'border-brand bg-brand/20' : 'text-muted hover:text-ink'}`} onClick={() => onSelect(selected === 'all' ? null : 'all')}>Highlight all changes on both PDFs</button>
            <span className="text-muted">or click a row to see that change on RAW and edited</span>
          </div>)}
        {edit.edits.length > 0 && (
          <div className="overflow-x-auto"><table className="w-full min-w-[480px]">
            <thead><tr className="border-b"><th className="th">Page</th><th className="th">Change</th><th className="th">Why</th><th className="th">By</th></tr></thead>
            <tbody>{edit.edits.map((e, i) => (
              <tr key={i} onClick={onSelect ? () => onSelect(selected === i ? null : i) : undefined} className={`border-b align-top last:border-0 ${onSelect ? 'cursor-pointer hover:bg-surface2/50' : ''} ${selected === i ? 'bg-brand/10' : ''}`}>
                <td className="td tabular-nums">{e.page}</td>
                <td className="td"><span className="rounded bg-danger/10 px-1.5 py-0.5 font-mono text-xs line-through decoration-danger/60">{e.original}</span><ArrowRight size={12} className="mx-1.5 inline text-muted" /><span className="rounded bg-ok/10 px-1.5 py-0.5 font-mono text-xs">{e.replacement || '(removed)'}</span></td>
                <td className="td text-xs text-muted">{e.reason}</td>
                <td className="td"><Badge t={e.source === 'ai' ? 'violet' : 'muted'}>{e.source === 'ai' ? 'AI' : 'Rules'} · {Math.round(e.confidence * 100)}%</Badge></td>
              </tr>))}</tbody></table></div>
        )}
        {edit.status === 'completed' && edit.edits.length === 0 && <p className="text-muted">No corrections were necessary; the report went to QC unchanged.</p>}
        {edit.skipped.length > 0 && (
          <div>
            <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-warn"><TriangleAlert size={13} /> Not changed — needs a human decision ({edit.skipped.length})</p>
            <ul className="max-h-48 space-y-1 overflow-auto text-xs">{edit.skipped.map((s, i) => (
              <li key={i} className="rounded-lg bg-surface2/50 px-2 py-1.5"><span className="font-mono">{s.original || '—'}</span>{s.proposed ? <> <ArrowRight size={11} className="inline text-muted" /> <span className="font-mono text-muted">{s.proposed}</span></> : null}<span className="block text-muted"><CircleAlert size={11} className="mr-1 inline" />{s.reason}{s.page ? ` · page ${s.page}` : ''}</span></li>))}</ul>
          </div>
        )}
        <p className="text-[11px] text-muted">The AI editor only fixes spelling, grammar, punctuation and capitalisation. It never changes numbers, dates, names or negations, and it never approves a form — a QC reviewer decides.</p>
      </div>
    </details>
  )
}
