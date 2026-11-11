import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { CheckCircle2, Info, Loader2, TriangleAlert, X, XCircle } from 'lucide-react'
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react'
import clsx from 'clsx'
import { REVIEW, SEVERITY, STATUS, tone } from '../lib/format'

export function Badge({ children, t = 'muted', className }: { children: ReactNode; t?: string; className?: string }) {
  return <span className={clsx('inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold', tone(t), className)}>{children}</span>
}
export const StatusBadge = ({ status }: { status: string }) => <Badge t={STATUS[status]?.tone}>{STATUS[status]?.label || status}</Badge>
export const SeverityBadge = ({ s }: { s: string }) => <Badge t={SEVERITY[s]?.tone}>{SEVERITY[s]?.label || s}</Badge>
export const ReviewBadge = ({ s }: { s: string }) => <Badge t={REVIEW[s]?.tone}>{REVIEW[s]?.label || s}</Badge>

export function Spinner({ size = 18, className }: { size?: number; className?: string }) {
  return <Loader2 size={size} className={clsx('animate-spin text-brand', className)} aria-label="Loading" />
}
export const Skeleton = ({ className }: { className?: string }) => <div className={clsx('skeleton', className)} aria-hidden />
export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return <div className="space-y-2 p-4" role="status" aria-label="Loading">{Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div><h1 className="text-2xl font-extrabold tracking-tight">{title}</h1>{sub && <p className="mt-1 max-w-3xl text-sm text-muted">{sub}</p>}</div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="block"><span className="label">{label}</span>{children}{hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}</label>
}

export function Alert({ kind = 'info', children, title }: { kind?: 'info' | 'warn' | 'error' | 'ok'; children: ReactNode; title?: string }) {
  const map = { info: ['brand', Info], warn: ['warn', TriangleAlert], error: ['danger', XCircle], ok: ['ok', CheckCircle2] } as const
  const [t, Icon] = map[kind]
  return (
    <div role={kind === 'error' ? 'alert' : 'status'} className={clsx('flex gap-3 rounded-xl border p-3 text-sm', tone(t))}>
      <Icon size={18} className="mt-0.5 shrink-0" />
      <div className="min-w-0 text-ink/90">{title && <p className="font-semibold text-ink">{title}</p>}{children}</div>
    </div>
  )
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const still = useReducedMotion()
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/60 p-4 backdrop-blur-sm" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
          <motion.div role="dialog" aria-modal="true" aria-label={title} initial={still ? false : { y: 24, scale: 0.97 }} animate={{ y: 0, scale: 1 }} exit={{ y: 24, scale: 0.97 }}
            className={clsx('glass w-full p-5', wide ? 'max-w-2xl' : 'max-w-md')}>
            <div className="mb-4 flex items-center justify-between"><h2 className="text-lg font-bold">{title}</h2>
              <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-surface2" aria-label="Close"><X size={18} /></button></div>
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { key: T; label: string; count?: number }[]; value: T; onChange: (k: T) => void }) {
  return (
    <div role="tablist" className="mb-4 flex flex-wrap gap-1 rounded-xl border bg-surface2/50 p-1">
      {tabs.map((t) => (
        <button key={t.key} role="tab" aria-selected={value === t.key} onClick={() => onChange(t.key)}
          className={clsx('rounded-lg px-3 py-1.5 text-sm font-semibold transition', value === t.key ? 'bg-gradient-to-r from-brand/90 to-violet/90 text-white shadow' : 'text-muted hover:text-ink')}>
          {t.label}{t.count !== undefined && <span className="ml-1.5 rounded-full bg-black/20 px-1.5 text-[11px]">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

/* ---- toasts ---- */
type Toast = { id: number; kind: 'ok' | 'error' | 'info'; text: string }
const ToastCtx = createContext<(kind: Toast['kind'], text: string) => void>(() => {})
export const useToast = () => useContext(ToastCtx)
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])
  const push = useCallback((kind: Toast['kind'], text: string) => {
    const id = Date.now() + Math.random()
    setItems((s) => [...s, { id, kind, text }])
    setTimeout(() => setItems((s) => s.filter((x) => x.id !== id)), kind === 'error' ? 7000 : 4000)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed left-1/2 top-4 z-[60] flex w-[min(92vw,420px)] -translate-x-1/2 flex-col gap-2" aria-live="polite">
        <AnimatePresence>
          {items.map((t) => (
            <motion.div key={t.id} layout initial={{ opacity: 0, y: -14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95 }} className="pointer-events-auto">
              <Alert kind={t.kind === 'ok' ? 'ok' : t.kind === 'error' ? 'error' : 'info'}>{t.text}</Alert>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  )
}

const GLOW: Record<string, string> = { brand: 'bg-brand', violet: 'bg-violet', cyan: 'bg-cyan', ok: 'bg-ok', warn: 'bg-warn', danger: 'bg-danger', muted: 'bg-muted' }

export function Stat({ label, value, sub, tone: t = 'brand', hint, onClick }: { label: string; value: ReactNode; sub?: string; tone?: string; hint?: string; onClick?: () => void }) {
  const Tag: any = onClick ? 'button' : 'div'
  return (
    <Tag onClick={onClick} title={hint} className={clsx('card relative overflow-hidden p-4 text-left', onClick && 'transition hover:-translate-y-0.5 hover:border-brand/40')}>
      <div className={clsx('absolute -right-6 -top-6 h-20 w-20 rounded-full opacity-25 blur-2xl', GLOW[t] || 'bg-brand')} />
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</p>
      <p className="mt-1 text-3xl font-extrabold tabular-nums">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </Tag>
  )
}

export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (o: number) => void }) {
  if (total <= limit) return null
  return (
    <div className="flex items-center justify-between border-t px-4 py-2 text-xs text-muted">
      <span>{offset + 1}–{Math.min(offset + limit, total)} of {total}</span>
      <div className="flex gap-2">
        <button className="btn-ghost !py-1" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>Previous</button>
        <button className="btn-ghost !py-1" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Next</button>
      </div>
    </div>
  )
}
