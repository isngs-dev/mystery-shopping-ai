import { FormEvent, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { LockKeyhole, Zap } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../lib/auth'
import { api } from '../lib/api'
import { Alert, Field, Spinner } from '../components/ui'
import { CopilotCharacter } from '../components/Copilot'
import { Logo, ThemeToggle } from '../components/Layout'

const ROLE_INFO: Record<string, { order: number; blurb: string }> = {
  super_admin: { order: 1, blurb: 'Everything: users, access, audit, who is doing what' },
  admin: { order: 2, blurb: 'Overall working updates, reports, rule approval, audit' },
  qc_reviewer: { order: 4, blurb: 'QC workspace, accept / reject findings, final decision' },
  manager: { order: 3, blurb: 'QC + assign jobs, QC rules, team workload, Google Drive import & sync' },
  editor: { order: 5, blurb: 'Only assigned forms and QC feedback' },
  ai_lead: { order: 6, blurb: 'Author rules, AI and system status' },
  process_owner: { order: 7, blurb: 'Approve and publish QC rules' },
  ops_sponsor: { order: 8, blurb: 'Dashboards, reports, rule approval' },
  reporting: { order: 9, blurb: 'Dashboards and reports (no documents)' },
  client_poc: { order: 10, blurb: 'Approved forms only' },
}

export default function Login() {
  const { session, login, loading, refresh } = useAuth()
  const nav = useNavigate()
  const loc = useLocation() as any
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [change, setChange] = useState(false)
  const [np, setNp] = useState('')
  const demo = useQuery({ queryKey: ['demo-accounts'], queryFn: () => api.get<{ enabled: boolean; accounts: any[] }>('/api/auth/demo-accounts'), retry: false, enabled: !session })
  const [quickBusy, setQuickBusy] = useState<number | null>(null)
  async function quick(id: number) {
    setQuickBusy(id); setError('')
    try { await api.post('/api/auth/demo-login', { user_id: id }); await refresh(); nav(loc.state?.from || '/app', { replace: true }) }
    catch (err: any) { setError(err.message) } finally { setQuickBusy(null) }
  }
  const accounts = [...(demo.data?.accounts || [])].sort((a, b) => (ROLE_INFO[a.role]?.order ?? 99) - (ROLE_INFO[b.role]?.order ?? 99) || a.name.localeCompare(b.name))

  if (!loading && session && !change) return <Navigate to={session.user.must_change_password ? '/login' : loc.state?.from || '/app'} replace />

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      const s = await login(email, password)
      if (s.user.must_change_password) setChange(true)
      else nav(loc.state?.from || '/app', { replace: true })
    } catch (err: any) { setError(err.message) } finally { setBusy(false) }
  }
  async function submitChange(e: FormEvent) {
    e.preventDefault()
    setBusy(true); setError('')
    try { await api.post('/api/auth/change-password', { current_password: password, new_password: np }); await refresh(); setChange(false); nav('/app', { replace: true }) }
    catch (err: any) { setError(err.message) } finally { setBusy(false) }
  }

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden px-4 pb-10 pt-24">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_50%_at_20%_10%,rgb(var(--brand)/.25),transparent),radial-gradient(50%_50%_at_90%_80%,rgb(var(--violet)/.22),transparent)]" />
      <div className="absolute left-4 top-4"><Link to="/"><Logo /></Link></div>
      <div className="absolute right-4 top-4"><ThemeToggle /></div>
      <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} className="glass relative w-full max-w-md p-7">
        <div className="-mt-16 mb-2 flex justify-center"><CopilotCharacter mode={error ? 'alert' : busy ? 'thinking' : 'idle'} size={110} /></div>
        <h1 className="text-center text-2xl font-extrabold">{change ? 'Set a new password' : 'Welcome back'}</h1>
        <p className="mb-5 mt-1 text-center text-sm text-muted">{change ? 'Your account uses a temporary password. Choose your own to continue.' : 'Sign in to your quality operations workspace.'}</p>
        {error && <div className="mb-4"><Alert kind="error">{error}</Alert></div>}
        {!change ? (
          <form onSubmit={submit} className="space-y-4">
            <Field label="Email"><input className="input" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" /></Field>
            <Field label="Password"><input className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
            <button className="btn-primary w-full" disabled={busy}>{busy ? <Spinner className="!text-white" /> : <LockKeyhole size={16} />} Sign in</button>
          </form>
        ) : (
          <form onSubmit={submitChange} className="space-y-4">
            <Field label="New password" hint="At least 10 characters with upper- and lower-case letters and a number."><input className="input" type="password" autoComplete="new-password" required minLength={10} value={np} onChange={(e) => setNp(e.target.value)} /></Field>
            <button className="btn-primary w-full" disabled={busy}>{busy && <Spinner className="!text-white" />} Save and continue</button>
          </form>
        )}
        <p className="mt-5 text-center text-xs text-muted">Accounts are created by your Super Admin. Access is limited to the clients and projects assigned to you.</p>
      </motion.div>

      {demo.data?.enabled && !change && accounts.length > 0 && (
        <motion.section initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }} className="glass relative mt-6 w-full max-w-4xl p-5" aria-label="Quick access to demo accounts">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Zap size={16} className="text-warn" /><h2 className="font-bold">Quick access — {accounts.some((a) => a.is_sample) ? 'demo accounts' : 'team accounts'}</h2>
            <span className="rounded-full border border-warn/30 bg-warn/15 px-2 py-0.5 text-[11px] font-semibold text-warn">{accounts.some((a) => a.is_sample) ? 'DEVELOPMENT ONLY · SAMPLE DATA' : 'DEVELOPMENT ONLY · THIS PC'}</span>
          </div>
          <p className="mb-4 text-xs text-muted">Click an account to sign in directly, no password needed. This panel only appears on a local development server and is never available in production.</p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {accounts.map((a) => (
              <button key={a.id} onClick={() => quick(a.id)} disabled={quickBusy !== null} className="group rounded-xl border bg-surface2/50 p-3 text-left transition hover:-translate-y-0.5 hover:border-brand/60 hover:bg-surface2 disabled:opacity-60">
                <span className="flex items-center justify-between gap-2"><b className="truncate text-sm">{a.name}</b>{quickBusy === a.id ? <Spinner size={14} /> : <span className="text-xs text-brand opacity-0 transition group-hover:opacity-100">Sign in →</span>}</span>
                <span className="block text-[11px] font-semibold uppercase tracking-wide text-cyan">{a.role_label}</span>
                <span className="mt-1 block text-xs text-muted">{ROLE_INFO[a.role]?.blurb}</span>
                <span className="mt-1 block truncate font-mono text-[10px] text-muted/80">{a.email}</span>
              </button>
            ))}
          </div>
        </motion.section>
      )}
    </div>
  )
}
