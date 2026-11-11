import { AnimatePresence, motion, useReducedMotion, TargetAndTransition, Transition } from 'framer-motion'
import { CopilotMode } from '../lib/copilot'

/** QC Copilot — an original 2D character built from SVG + Framer Motion (no external assets). */
export function CopilotCharacter({ mode = 'idle', size = 120 }: { mode?: CopilotMode; size?: number }) {
  const still = useReducedMotion()
  const alert = mode === 'alert'
  const glow = alert ? '#f08a70' : mode === 'celebrate' ? '#7ccf88' : '#f0c987'
  const loop = (a: TargetAndTransition, t: number): { animate?: TargetAndTransition; transition?: Transition } =>
    (still ? {} : { animate: a, transition: { duration: t, repeat: Infinity, ease: 'easeInOut' } })

  const eyeY = mode === 'thinking' ? -3 : 0
  const eyeX = mode === 'scanning' ? [-4, 4, -4] : 0
  return (
    <svg width={size} height={size} viewBox="0 0 160 160" role="img" aria-label={`QC Copilot assistant, ${mode}`}>
      <defs>
        <linearGradient id="cp-body" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={alert ? '#d9772e' : '#c98a52'} />
          <stop offset="1" stopColor={alert ? '#b8402e' : '#3f8f66'} />
        </linearGradient>
        <radialGradient id="cp-glow"><stop offset="0" stopColor={glow} stopOpacity=".55" /><stop offset="1" stopColor={glow} stopOpacity="0" /></radialGradient>
        <linearGradient id="cp-scan" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#f0c987" stopOpacity="0" /><stop offset=".5" stopColor="#f0c987" stopOpacity=".9" /><stop offset="1" stopColor="#f0c987" stopOpacity="0" /></linearGradient>
      </defs>
      <ellipse cx="80" cy="148" rx="34" ry="5" fill="#000" opacity=".25" />
      <motion.g {...loop(mode === 'celebrate' ? { y: [0, -16, 0] } : alert ? { x: [0, -3, 3, -3, 0] } : { y: [0, -6, 0] }, mode === 'celebrate' ? 0.7 : alert ? 0.5 : 3.4)}>
        <circle cx="80" cy="82" r="62" fill="url(#cp-glow)" />
        {/* antenna */}
        <line x1="80" y1="30" x2="80" y2="18" stroke="#3f8f66" strokeWidth="3" strokeLinecap="round" />
        <motion.circle cx="80" cy="14" r="5" fill={glow} {...loop({ opacity: [1, 0.4, 1], scale: [1, 1.3, 1] }, 1.4)} />
        {/* ears */}
        <rect x="22" y="68" width="10" height="26" rx="5" fill="#7a5532" />
        <rect x="128" y="68" width="10" height="26" rx="5" fill="#7a5532" />
        {/* head */}
        <rect x="28" y="30" width="104" height="86" rx="34" fill="url(#cp-body)" />
        <rect x="28" y="30" width="104" height="86" rx="34" fill="none" stroke="#fff" strokeOpacity=".18" strokeWidth="2" />
        {/* visor */}
        <rect x="40" y="48" width="80" height="48" rx="22" fill="#0a1b14" />
        {/* eyes */}
        <motion.g animate={{ y: eyeY }} transition={{ duration: 0.4 }}>
          <motion.g {...(still || mode !== 'scanning' ? {} : { animate: { x: eyeX }, transition: { duration: 1.6, repeat: Infinity, ease: 'easeInOut' } })}>
            {mode === 'celebrate' ? (
              <>
                <path d="M52 76 Q60 62 68 76" stroke={glow} strokeWidth="5" fill="none" strokeLinecap="round" />
                <path d="M92 76 Q100 62 108 76" stroke={glow} strokeWidth="5" fill="none" strokeLinecap="round" />
              </>
            ) : (
              <>
                <motion.ellipse cx="60" cy="72" rx={alert ? 8.5 : 7} ry={alert ? 11 : 9} fill={glow} style={{ transformOrigin: '60px 72px' }}
                  {...loop({ scaleY: [1, 1, 0.1, 1, 1] }, 4.2)} />
                <motion.ellipse cx="100" cy="72" rx={alert ? 8.5 : 7} ry={alert ? 11 : 9} fill={glow} style={{ transformOrigin: '100px 72px' }}
                  {...loop({ scaleY: [1, 1, 0.1, 1, 1] }, 4.2)} />
              </>
            )}
          </motion.g>
        </motion.g>
        {/* mouth */}
        {mode === 'celebrate' ? <path d="M70 88 Q80 98 90 88" stroke={glow} strokeWidth="3" fill="none" strokeLinecap="round" />
          : alert ? <rect x="74" y="87" width="12" height="3" rx="1.5" fill={glow} />
          : <path d="M72 88 Q80 92 88 88" stroke={glow} strokeOpacity=".8" strokeWidth="2.5" fill="none" strokeLinecap="round" />}
        {/* core */}
        <motion.circle cx="80" cy="128" r="7" fill={glow} {...loop({ opacity: [0.6, 1, 0.6], scale: [1, 1.15, 1] }, mode === 'thinking' ? 0.8 : 2)} style={{ transformOrigin: '80px 128px' }} />
        <rect x="58" y="116" width="44" height="16" rx="8" fill="#2f6b4a" opacity=".9" />
        <circle cx="80" cy="124" r="4.5" fill={glow} />
      </motion.g>

      {/* scanning document + beam */}
      {mode === 'scanning' && (
        <g>
          <rect x="112" y="92" width="38" height="50" rx="5" fill="#f2e9da" opacity=".95" />
          {[0, 1, 2, 3].map((i) => <rect key={i} x="118" y={100 + i * 10} width={i % 2 ? 20 : 26} height="3.5" rx="1.7" fill="#a4b29c" />)}
          <motion.rect x="112" width="38" height="14" fill="url(#cp-scan)" initial={{ y: 92 }} {...(still ? {} : { animate: { y: [92, 128, 92] }, transition: { duration: 1.8, repeat: Infinity, ease: 'easeInOut' } })} />
        </g>
      )}
      {/* thinking dots */}
      {mode === 'thinking' && [0, 1, 2].map((i) => (
        <motion.circle key={i} cx={112 + i * 14} cy={26} r="5" fill="#e3c9a0" {...loop({ y: [0, -8, 0], opacity: [0.4, 1, 0.4] }, 1.1)} transition={{ duration: 1.1, repeat: Infinity, delay: i * 0.18 }} />
      ))}
      {/* celebration confetti */}
      {mode === 'celebrate' && !still && ['#7ccf88', '#e8b24a', '#3f8f66', '#f0c987', '#d9a07a', '#c98a52'].map((c, i) => (
        <motion.rect key={i} x={30 + i * 20} y={20} width="6" height="10" rx="2" fill={c}
          animate={{ y: [10, 120], rotate: [0, 260], opacity: [1, 0] }} transition={{ duration: 1.6, repeat: Infinity, delay: i * 0.15, ease: 'easeOut' }} />
      ))}
      {alert && (
        <g>
          <circle cx="132" cy="34" r="14" fill="#d9533f" />
          <rect x="130" y="25" width="4" height="12" rx="2" fill="#fff" /><circle cx="132" cy="42" r="2.4" fill="#fff" />
        </g>
      )}
    </svg>
  )
}
