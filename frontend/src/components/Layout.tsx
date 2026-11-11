import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, BookOpenCheck, CloudDownload, Table2, FolderTree, Gauge, History, LayoutGrid, LogOut, Menu, Moon, Puzzle, ScrollText, Server, Sun, UserCog, Users2, X, FileBarChart2 } from 'lucide-react'
import clsx from 'clsx'
import { useAuth } from '../lib/auth'
import { api } from '../lib/api'
import { ago } from '../lib/format'
import { Badge } from './ui'

export function ThemeToggle({ className }: { className?: string }) {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme || 'dark')
  const flip = () => {
    const n = theme === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.theme = n
    try { localStorage.setItem('msai-theme', n) } catch { /* ignore */ }
    setTheme(n)
  }
  return (
    <button onClick={flip} className={clsx('btn-ghost !px-2.5', className)} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}>
      {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  )
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-2 font-extrabold tracking-tight">
      <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#c98a52" /><stop offset="1" stopColor="#3f8f66" /></linearGradient></defs>
        <rect width="64" height="64" rx="16" fill="url(#lg)" /><circle cx="26" cy="29" r="4" fill="#0a1b14" /><circle cx="38" cy="29" r="4" fill="#0a1b14" /><rect x="24" y="40" width="16" height="4" rx="2" fill="#f0e0bc" /></svg>
      <span>Mystery Shopping <span className="grad-text">AI</span></span>
    </span>
  )
}

function Notifications() {
  const qc = useQueryClient()
  const nav = useNavigate()
  const [open, setOpen] = useState(false)
  const { data } = useQuery({ queryKey: ['notifications'], queryFn: () => api.get<any[]>('/api/auth/notifications'), refetchInterval: 30000 })
  const unread = (data || []).filter((n) => !n.is_read).length
  return (
    <div className="relative">
      <button className="btn-ghost relative !px-2.5" onClick={() => { setOpen(!open); if (!open && unread) api.post('/api/auth/notifications/read').then(() => qc.invalidateQueries({ queryKey: ['notifications'] })) }} aria-label="Notifications">
        <Bell size={16} />{unread > 0 && <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-danger px-1 text-[10px] font-bold text-white">{unread}</span>}
      </button>
      {open && (
        <div className="glass absolute right-0 z-50 mt-2 w-80 max-w-[90vw] p-2">
          {(data || []).length === 0 && <p className="p-4 text-center text-sm text-muted">No notifications yet.</p>}
          {(data || []).map((n) => (
            <button key={n.id} onClick={() => { setOpen(false); if (n.form_id) nav(`/app/forms/${n.form_id}`) }} className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-surface2">
              <span className={clsx(!n.is_read && 'font-semibold')}>{n.message}</span><span className="block text-xs text-muted">{ago(n.created_at)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Layout() {
  const { session, logout, can } = useAuth()
  const loc = useLocation()
  const nav = useNavigate()
  const [drawer, setDrawer] = useState(false)
  const still = useReducedMotion()
  useEffect(() => setDrawer(false), [loc.pathname])
  if (!session) return null
  const role = session.user.role

  const items: { to: string; label: string; icon: any; show: boolean; end?: boolean; also?: string[] }[] = [
    { to: '/app', label: 'Dashboard', icon: Gauge, show: can('dashboard.view'), end: true },
    // Repository, Review queue / My work and QC projects are tabs of one entry (see RepositoryTabs)
    { to: role === 'client_poc' ? '/app/queue' : '/app/repository', label: 'Repository', icon: FolderTree, show: can('forms.view'), also: ['/app/repository', '/app/queue', '/app/projects'] },
    { to: '/app/rules', label: 'QC Rules', icon: BookOpenCheck, show: can('rules.view') },
    { to: '/app/reports', label: 'Reports', icon: FileBarChart2, show: can('reports.view') },
    { to: '/app/tracker', label: 'Production Tracker', icon: Table2, show: can('tracker.view') },
    { to: '/app/modules', label: 'Automation Modules', icon: Puzzle, show: true },
  ]
  const admin = [
    { to: '/app/admin/users', label: 'Users & Roles', icon: Users2, show: can('users.manage') },
    { to: '/app/admin/drive', label: 'Google Drive Import', icon: CloudDownload, show: can('integrations.manage') },
    { to: '/app/admin/org', label: 'Clients & Access', icon: UserCog, show: can('orgs.manage') || can('access.grant') },
    { to: '/app/admin/audit', label: 'Audit Trail', icon: History, show: can('audit.view') },
    { to: '/app/admin/system', label: 'System Status', icon: Server, show: can('system.view') },
  ]
  const link = (i: (typeof items)[number]) => (
    <NavLink key={i.to} to={i.to} end={i.end} className={({ isActive }) => clsx('flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-semibold transition', (isActive || i.also?.some((p) => loc.pathname.startsWith(p))) ? 'bg-gradient-to-r from-brand/25 to-violet/20 text-ink ring-1 ring-brand/40' : 'text-muted hover:bg-surface2 hover:text-ink')}>
      <i.icon size={17} />{i.label}
    </NavLink>
  )
  const sidebar = (
    <nav className="flex h-full flex-col gap-1 p-3" aria-label="Main">
      <div className="mb-4 px-2 pt-1"><Logo /></div>
      {items.filter((i) => i.show).map(link)}
      {admin.some((i) => i.show) && <p className="mb-1 mt-4 px-3 text-[10px] font-bold uppercase tracking-widest text-muted/70">Administration</p>}
      {admin.filter((i) => i.show).map(link)}
      <div className="mt-auto rounded-xl border bg-surface2/50 p-3 text-xs text-muted">
        <LayoutGrid size={14} className="mb-1 text-cyan" />Every AI finding is validated by a person. Nothing is approved automatically.
      </div>
    </nav>
  )

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[250px_1fr]">
      <aside className="sticky top-0 hidden h-screen border-r bg-surface/70 backdrop-blur lg:block">{sidebar}</aside>
      <AnimatePresence>
        {drawer && (
          <motion.div className="fixed inset-0 z-50 lg:hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <div className="absolute inset-0 bg-black/60" onClick={() => setDrawer(false)} />
            <motion.aside initial={{ x: -280 }} animate={{ x: 0 }} exit={{ x: -280 }} className="absolute inset-y-0 left-0 w-64 border-r bg-surface">
              <button className="absolute right-2 top-2 p-2 text-muted" onClick={() => setDrawer(false)} aria-label="Close menu"><X size={18} /></button>{sidebar}
            </motion.aside>
          </motion.div>
        )}
      </AnimatePresence>
      <div className="min-w-0">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b bg-bg/75 px-4 py-2.5 backdrop-blur-xl sm:px-6">
          <button className="btn-ghost !px-2.5 lg:hidden" onClick={() => setDrawer(true)} aria-label="Open menu"><Menu size={18} /></button>
          <div className="ml-auto flex items-center gap-2">
            <Notifications />
            <ThemeToggle />
            <div className="hidden text-right sm:block"><p className="text-sm font-semibold leading-tight">{session.user.name}</p><p className="text-[11px] text-muted">{session.user.role_label}</p></div>
            {session.user.is_sample && <Badge t="warn">SAMPLE</Badge>}
            <button className="btn-ghost !px-2.5" aria-label="Sign out" onClick={async () => { await logout(); nav('/') }}><LogOut size={16} /></button>
          </div>
        </header>
        <main className="mx-auto max-w-[1500px] px-4 py-6 sm:px-6">
          <AnimatePresence mode="wait">
            <motion.div key={loc.pathname.split('/').slice(0, 4).join('/')} initial={still ? false : { opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  )
}
