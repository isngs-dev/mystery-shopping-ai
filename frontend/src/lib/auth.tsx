import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api } from './api'
import type { Session } from './types'

interface AuthCtx {
  session: Session | null
  loading: boolean
  login: (email: string, password: string) => Promise<Session>
  logout: () => Promise<void>
  refresh: () => Promise<void>
  can: (perm: string) => boolean
}
const Ctx = createContext<AuthCtx>(null as any)
export const useAuth = () => useContext(Ctx)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try { setSession(await api.get<Session>('/api/auth/me')) } catch { setSession(null) }
  }, [])

  useEffect(() => { refresh().finally(() => setLoading(false)) }, [refresh])
  useEffect(() => {
    const h = () => setSession(null)
    window.addEventListener('msai:unauthorized', h)
    return () => window.removeEventListener('msai:unauthorized', h)
  }, [])

  const value = useMemo<AuthCtx>(() => ({
    session, loading, refresh,
    login: async (email, password) => { const s = await api.post<Session>('/api/auth/login', { email, password }); setSession(s); return s },
    logout: async () => { try { await api.post('/api/auth/logout') } finally { setSession(null) } },
    can: (perm) => !!session?.permissions.includes(perm),
  }), [session, loading, refresh])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
