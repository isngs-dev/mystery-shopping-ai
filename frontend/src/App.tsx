import { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './lib/auth'
import { Spinner } from './components/ui'
import { EmptyState } from './components/Illustrations'
import Layout from './components/Layout'
import { WithRepositoryTabs } from './components/RepositoryTabs'
import Landing from './pages/Landing'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Repository from './pages/Repository'
import FormDetail from './pages/FormDetail'
import QCWorkspace from './pages/QCWorkspace'
import Queue from './pages/Queue'
import Rules from './pages/Rules'
import Reports from './pages/Reports'
import Modules from './pages/Modules'
import DriveImport from './pages/DriveImport'
import Tracker from './pages/Tracker'
import { QCProjectPage, QCProjectsPage } from './pages/QCProjects'
import { AuditPage, OrgPage, SystemPage, UsersPage } from './pages/Admin'

function Protected({ children, perm }: { children: ReactNode; perm?: string | string[] }) {
  const { session, loading, can } = useAuth()
  const loc = useLocation()
  if (loading) return <div className="grid min-h-screen place-items-center"><Spinner size={32} /></div>
  if (!session) return <Navigate to="/login" state={{ from: loc.pathname }} replace />
  if (session.user.must_change_password) return <Navigate to="/login" replace />
  const perms = perm ? (Array.isArray(perm) ? perm : [perm]) : []
  if (perms.length && !perms.some(can)) return <EmptyState kind="error" title="You don’t have access to this page">Your role ({session.user.role_label}) does not include this area. Ask your Super Admin if you need access.</EmptyState>
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/app" element={<Protected><Layout /></Protected>}>
        <Route index element={<Dashboard />} />
        <Route path="queue" element={<WithRepositoryTabs><Queue /></WithRepositoryTabs>} />
        <Route path="projects" element={<Protected perm="qc.report"><WithRepositoryTabs><QCProjectsPage /></WithRepositoryTabs></Protected>} />
        <Route path="projects/:id" element={<Protected perm="qc.report"><WithRepositoryTabs><QCProjectPage /></WithRepositoryTabs></Protected>} />
        <Route path="repository" element={<WithRepositoryTabs><Repository /></WithRepositoryTabs>} />
        <Route path="forms/:id" element={<FormDetail />} />
        <Route path="forms/:id/qc" element={<Protected perm="qc.view"><QCWorkspace /></Protected>} />
        <Route path="rules" element={<Protected perm="rules.view"><Rules /></Protected>} />
        <Route path="reports" element={<Reports />} />
        <Route path="modules" element={<Modules />} />
        <Route path="tracker" element={<Protected perm="tracker.view"><Tracker /></Protected>} />
        <Route path="admin/drive" element={<Protected perm="integrations.manage"><DriveImport /></Protected>} />
        <Route path="admin/users" element={<Protected perm="users.manage"><UsersPage /></Protected>} />
        <Route path="admin/org" element={<Protected perm={['orgs.manage', 'access.grant']}><OrgPage /></Protected>} />
        <Route path="admin/audit" element={<Protected perm="audit.view"><AuditPage /></Protected>} />
        <Route path="admin/system" element={<Protected perm="system.view"><SystemPage /></Protected>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
