import { motion, useReducedMotion } from 'framer-motion'
import { ReactNode } from 'react'

function Doc({ x, y, rot = 0, accent = '#c98a52', delay = 0, w = 70 }: { x: number; y: number; rot?: number; accent?: string; delay?: number; w?: number }) {
  const still = useReducedMotion()
  return (
    <motion.g style={{ transformOrigin: `${x + w / 2}px ${y + 45}px` }} initial={{ rotate: rot }}
      {...(still ? {} : { animate: { y: [0, -9, 0], rotate: [rot, rot + 2, rot] }, transition: { duration: 5, repeat: Infinity, ease: 'easeInOut', delay } })}>
      <rect x={x} y={y} width={w} height={w * 1.3} rx="8" fill="rgb(var(--surface2))" stroke={accent} strokeOpacity=".55" strokeWidth="1.5" />
      <rect x={x + 10} y={y + 12} width={w * 0.45} height="6" rx="3" fill={accent} opacity=".8" />
      {[0, 1, 2, 3].map((i) => <rect key={i} x={x + 10} y={y + 28 + i * 11} width={w - 20 - (i % 2) * 14} height="4" rx="2" fill="rgb(var(--muted))" opacity=".45" />)}
    </motion.g>
  )
}

export function EmptyIllustration({ kind = 'docs' }: { kind?: 'docs' | 'success' | 'warning' | 'error' | 'search' }) {
  const still = useReducedMotion()
  const color = kind === 'success' ? '#7ccf88' : kind === 'warning' ? '#e8b24a' : kind === 'error' ? '#e26a58' : '#c98a52'
  return (
    <svg width="170" height="120" viewBox="0 0 170 120" aria-hidden>
      <Doc x={20} y={26} rot={-8} accent="#3f8f66" delay={0.3} w={56} />
      <Doc x={92} y={30} rot={7} accent="#9dcdaa" delay={0.8} w={56} />
      <motion.g {...(still ? {} : { animate: { y: [0, -5, 0] }, transition: { duration: 3.2, repeat: Infinity, ease: 'easeInOut' } })}>
        <circle cx="85" cy="62" r="28" fill="rgb(var(--surface))" stroke={color} strokeWidth="2.5" />
        {kind === 'success' && <motion.path d="M72 62 l9 9 l17 -19" stroke={color} strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.8 }} />}
        {kind === 'warning' && <><rect x="83" y="46" width="4" height="20" rx="2" fill={color} /><circle cx="85" cy="73" r="2.8" fill={color} /></>}
        {kind === 'error' && <path d="M74 51 l22 22 M96 51 l-22 22" stroke={color} strokeWidth="5" strokeLinecap="round" />}
        {(kind === 'docs' || kind === 'search') && <><circle cx="82" cy="59" r="11" fill="none" stroke={color} strokeWidth="4" /><path d="M90 67 l11 11" stroke={color} strokeWidth="5" strokeLinecap="round" /></>}
      </motion.g>
    </svg>
  )
}

export function EmptyState({ kind = 'docs', title, children, action }: { kind?: 'docs' | 'success' | 'warning' | 'error' | 'search'; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-10 text-center">
      <EmptyIllustration kind={kind} />
      <h3 className="mt-2 text-base font-bold">{title}</h3>
      {children && <p className="mt-1 max-w-md text-sm text-muted">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

/** RAW PDF -> AI analysis -> QC findings -> human approval, animated. `active` highlights the live stage. */
export function PipelineDiagram({ active = -1, compact = false }: { active?: number; compact?: boolean }) {
  const still = useReducedMotion()
  const steps = [['RAW PDF', 'Original report'], ['AI analysis', 'Extract · compare · rules'], ['QC findings', 'Evidence-linked'], ['Human approval', 'Reviewer decides']]
  const w = 170
  return (
    <svg viewBox={`0 0 ${steps.length * w + 20} ${compact ? 90 : 120}`} className="w-full" role="img" aria-label="Pipeline: RAW PDF, AI analysis, QC findings, human approval">
      <defs><linearGradient id="pl" x1="0" x2="1"><stop offset="0" stopColor="#c98a52" /><stop offset="1" stopColor="#3f8f66" /></linearGradient></defs>
      {steps.map(([t, s], i) => {
        const x = 10 + i * w
        const on = active === i
        return (
          <g key={t}>
            {i > 0 && (
              <g>
                <line x1={x - 22} y1="52" x2={x + 8} y2="52" stroke="rgb(var(--line))" strokeOpacity=".5" strokeWidth="2" strokeDasharray="4 4" />
                {!still && <motion.circle r="3.5" cy="52" fill="#9dcdaa" initial={{ cx: x - 22 }} animate={{ cx: [x - 22, x + 8] }} transition={{ duration: 1.4, repeat: Infinity, delay: i * 0.35, ease: 'linear' }} />}
              </g>
            )}
            <motion.rect x={x + 8} y="14" width={w - 44} height="76" rx="16" fill="rgb(var(--surface))" stroke={on ? '#9dcdaa' : 'url(#pl)'} strokeWidth={on ? 3 : 1.5}
              {...(on && !still ? { animate: { opacity: [1, 0.7, 1] }, transition: { duration: 1.2, repeat: Infinity } } : {})} />
            <circle cx={x + 8 + (w - 44) / 2} cy="40" r="11" fill="url(#pl)" />
            <text x={x + 8 + (w - 44) / 2} y="44" textAnchor="middle" fontSize="11" fontWeight="700" fill="#fff">{i + 1}</text>
            <text x={x + 8 + (w - 44) / 2} y="68" textAnchor="middle" fontSize="12" fontWeight="700" fill="rgb(var(--ink))">{t}</text>
            {!compact && <text x={x + 8 + (w - 44) / 2} y="82" textAnchor="middle" fontSize="9" fill="rgb(var(--muted))">{s}</text>}
          </g>
        )
      })}
    </svg>
  )
}

export function FloatingDocs() {
  return (
    <svg viewBox="0 0 520 380" className="w-full" aria-hidden>
      <Doc x={30} y={60} rot={-10} accent="#3f8f66" delay={0.2} w={110} />
      <Doc x={370} y={40} rot={9} accent="#9dcdaa" delay={0.9} w={110} />
      <Doc x={250} y={250} rot={-4} accent="#c98a52" delay={1.4} w={90} />
      <Doc x={60} y={250} rot={6} accent="#7ccf88" delay={0.6} w={80} />
    </svg>
  )
}
