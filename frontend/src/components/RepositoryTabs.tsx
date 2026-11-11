import { ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Tabs } from './ui'
import { useAuth } from '../lib/auth'

type Key = 'files' | 'queue' | 'projects'

/** Repository, Review queue and QC projects live together under one "Repository" entry; each keeps its own URL. */
export function RepositoryTabs() {
  const { session, can } = useAuth()
  const loc = useLocation()
  const nav = useNavigate()
  const role = session?.user.role
  const tabs: { key: Key; label: string; to: string }[] = [
    ...(can('forms.view') && role !== 'client_poc' ? [{ key: 'files' as Key, label: 'Repository', to: '/app/repository' }] : []),
    ...(can('forms.view') ? [{ key: 'queue' as Key, label: role === 'editor' ? 'My work' : 'Review queue', to: '/app/queue' }] : []),
    ...(can('qc.report') ? [{ key: 'projects' as Key, label: 'QC projects', to: '/app/projects' }] : []),
  ]
  const value: Key = loc.pathname.startsWith('/app/queue') ? 'queue' : loc.pathname.startsWith('/app/projects') ? 'projects' : 'files'
  if (tabs.length < 2) return null
  return <Tabs value={value} onChange={(k) => nav(tabs.find((t) => t.key === k)!.to)} tabs={tabs.map(({ key, label }) => ({ key, label }))} />
}

export function WithRepositoryTabs({ children }: { children: ReactNode }) {
  return <div><RepositoryTabs />{children}</div>
}
