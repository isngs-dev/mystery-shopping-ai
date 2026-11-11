import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from 'react'

export type CopilotMode = 'idle' | 'scanning' | 'thinking' | 'celebrate' | 'alert'
interface CopilotCtx {
  mode: CopilotMode
  message: string
  open: boolean
  setOpen: (v: boolean) => void
  say: (mode: CopilotMode, message: string) => void
}
const Ctx = createContext<CopilotCtx>(null as any)
export const useCopilot = () => useContext(Ctx)

const DEFAULT_MSG = "Hi, I'm QC Copilot. I compare RAW and edited reports, point to the evidence, and you make every call."

export function CopilotProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<CopilotMode>('idle')
  const [message, setMessage] = useState(DEFAULT_MSG)
  const [open, setOpenState] = useState(() => { try { return localStorage.getItem('msai-copilot') !== 'min' } catch { return true } })
  const setOpen = (v: boolean) => { setOpenState(v); try { localStorage.setItem('msai-copilot', v ? 'open' : 'min') } catch { /* ignore */ } }
  const value = useMemo(() => ({ mode, message, open, setOpen, say: (m: CopilotMode, t: string) => { setMode(m); setMessage(t) } }), [mode, message, open])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

/** Pages call this to tell the assistant what is happening; resets to idle on unmount. */
export function useCopilotHint(mode: CopilotMode, message: string) {
  const { say } = useCopilot()
  useEffect(() => {
    say(mode, message)
    return () => say('idle', DEFAULT_MSG)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, message])
}
