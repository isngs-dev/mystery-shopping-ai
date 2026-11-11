import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion, useReducedMotion, useScroll, useTransform } from 'framer-motion'
import { ArrowRight, BarChart3, FileSearch, GitCompareArrows, Lock, Menu, ShieldCheck, UserCheck, Workflow, X, Eye, ScrollText } from 'lucide-react'
import { Logo, ThemeToggle } from '../components/Layout'
import { CopilotCharacter } from '../components/Copilot'
import { FloatingDocs, PipelineDiagram } from '../components/Illustrations'
import { useAuth } from '../lib/auth'

const fade = { hidden: { opacity: 0, y: 28 }, show: { opacity: 1, y: 0 } }
const Reveal = ({ children, delay = 0, className }: any) => {
  const still = useReducedMotion()
  return <motion.div className={className} variants={fade} initial={still ? 'show' : 'hidden'} whileInView="show" viewport={{ once: true, margin: '-60px' }} transition={{ duration: 0.6, delay }}>{children}</motion.div>
}

function PreviewCard() {
  const still = useReducedMotion()
  const bars = [46, 72, 58, 90, 64, 80, 52]
  return (
    <div className="glass relative overflow-hidden p-4" aria-label="Illustrative product preview">
      <div className="mb-3 flex items-center justify-between text-xs"><span className="font-bold">QC Workspace</span><span className="rounded-full bg-warn/15 px-2 py-0.5 font-semibold text-warn">Illustrative preview</span></div>
      <div className="grid grid-cols-3 gap-2 text-[10px]">
        <div className="rounded-lg border bg-surface2/60 p-2"><p className="font-semibold text-cyan">RAW</p>{[90, 70, 85, 60].map((w, i) => <div key={i} className="mt-1.5 h-1.5 rounded bg-muted/40" style={{ width: `${w}%` }} />)}<div className="mt-1.5 h-1.5 w-2/3 rounded bg-cyan/50" /></div>
        <div className="rounded-lg border border-brand/40 bg-brand/5 p-2">
          <p className="font-semibold text-brand">Findings</p>
          {['Missed correction', 'Amount changed', 'New spelling error'].map((t, i) => (
            <motion.div key={t} initial={still ? false : { opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.5 + i * 0.35 }} className="mt-1.5 flex items-center gap-1 rounded bg-surface px-1.5 py-1">
              <span className={`h-1.5 w-1.5 rounded-full ${['bg-warn', 'bg-danger', 'bg-brand'][i]}`} /><span className="truncate">{t}</span></motion.div>
          ))}
        </div>
        <div className="rounded-lg border bg-surface2/60 p-2"><p className="font-semibold text-violet">Edited</p>{[85, 75, 80, 55].map((w, i) => <div key={i} className="mt-1.5 h-1.5 rounded bg-muted/40" style={{ width: `${w}%` }} />)}<div className="mt-1.5 h-1.5 w-1/2 rounded bg-warn/60" /></div>
      </div>
      <div className="mt-3 flex h-16 items-end gap-1.5">
        {bars.map((h, i) => <motion.div key={i} className="flex-1 rounded-t bg-gradient-to-t from-brand to-violet" initial={still ? false : { height: 0 }} animate={{ height: `${h}%` }} transition={{ delay: 0.3 + i * 0.08, type: 'spring', stiffness: 90 }} />)}
      </div>
    </div>
  )
}

export default function Landing() {
  const { session } = useAuth()
  const [menu, setMenu] = useState(false)
  const hero = useRef<HTMLDivElement>(null)
  const { scrollYProgress } = useScroll({ target: hero, offset: ['start start', 'end start'] })
  const still = useReducedMotion()
  const y1 = useTransform(scrollYProgress, [0, 1], [0, still ? 0 : -90])
  const y2 = useTransform(scrollYProgress, [0, 1], [0, still ? 0 : 70])
  const launch = session ? '/app' : '/login'
  const nav = [['Product', '#product'], ['Features', '#features'], ['Workflow', '#workflow'], ['Security', '#security']]

  const features = [
    { icon: FileSearch, t: 'AI-assisted QC', d: 'Deterministic checks plus contextual AI find missed corrections and newly introduced errors — each tied to quoted evidence and a page.' },
    { icon: GitCompareArrows, t: 'RAW vs edited comparison', d: 'Word-level diffs highlight additions, deletions, changed figures and meaning shifts directly on both PDFs.' },
    { icon: Workflow, t: 'Workflow management', d: 'Assign, review, send back for re-edit and resubmit under the same Form ID with every attempt preserved.' },
    { icon: BarChart3, t: 'Operational analytics', d: 'Backlog, first-pass approval, re-edit rate, turnaround and repeat-error trends from real review data.' },
  ]
  return (
    <div className="relative overflow-x-hidden">
      <header className="fixed inset-x-0 top-0 z-40 border-b bg-bg/70 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <Link to="/"><Logo /></Link>
          <nav className="ml-6 hidden gap-6 text-sm font-semibold text-muted md:flex" aria-label="Primary">{nav.map(([l, h]) => <a key={l} href={h} className="hover:text-ink">{l}</a>)}</nav>
          <div className="ml-auto flex items-center gap-2"><ThemeToggle />
            <Link to="/login" className="btn-ghost hidden sm:inline-flex">Sign In</Link>
            <Link to={launch} className="btn-primary hidden sm:inline-flex">Launch Dashboard</Link>
            <button className="btn-ghost md:hidden !px-2.5" onClick={() => setMenu(!menu)} aria-label="Menu">{menu ? <X size={18} /> : <Menu size={18} />}</button></div>
        </div>
        {menu && <div className="space-y-1 border-t px-4 py-3 md:hidden">{nav.map(([l, h]) => <a key={l} href={h} onClick={() => setMenu(false)} className="block rounded-lg px-3 py-2 font-semibold hover:bg-surface2">{l}</a>)}<Link to="/login" className="block rounded-lg px-3 py-2 font-semibold hover:bg-surface2">Sign In</Link></div>}
      </header>

      <section ref={hero} className="relative isolate pt-28">
        <div className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(60%_50%_at_15%_10%,rgb(var(--brand)/.28),transparent),radial-gradient(45%_45%_at_85%_20%,rgb(var(--violet)/.25),transparent),radial-gradient(40%_40%_at_60%_90%,rgb(var(--cyan)/.14),transparent)]" />
        <div className="mx-auto grid max-w-7xl items-center gap-10 px-4 pb-20 lg:grid-cols-2">
          <div>
            <Reveal><span className="inline-flex items-center gap-2 rounded-full border bg-surface/60 px-3 py-1 text-xs font-semibold text-cyan"><ShieldCheck size={14} /> Human-in-the-loop quality operations</span></Reveal>
            <Reveal delay={0.08}><h1 className="mt-5 text-4xl font-extrabold leading-[1.05] tracking-tight sm:text-5xl lg:text-6xl">Turn Manual Quality Checks Into <span className="grad-text">Intelligent Operations.</span></h1></Reveal>
            <Reveal delay={0.16}><p className="mt-5 max-w-xl text-lg text-muted">Mystery Shopping AI compares every RAW report with its edited version, shows exactly what was missed or introduced, and lets your QC team approve with confidence. <em className="not-italic text-ink">Every report checked. Every detail accounted for.</em></p></Reveal>
            <Reveal delay={0.24}><div className="mt-8 flex flex-wrap gap-3">
              <Link to={launch} className="btn-primary !px-6 !py-3 text-base">Launch Dashboard <ArrowRight size={18} /></Link>
              <a href="#workflow" className="btn-ghost !px-6 !py-3 text-base">Explore the Workflow</a></div></Reveal>
          </div>
          <div className="relative mx-auto w-full max-w-xl">
            <motion.div style={{ y: y1 }} className="absolute inset-0"><FloatingDocs /></motion.div>
            <motion.div style={{ y: y2 }} className="relative grid place-items-center pt-6"><CopilotCharacter mode="scanning" size={230} /></motion.div>
            <div className="relative mx-auto -mt-2 w-[94%] sm:w-[82%]" id="product"><PreviewCard /></div>
          </div>
        </div>
      </section>

      <section id="features" className="mx-auto max-w-7xl px-4 pb-20 pt-16">
        <Reveal><h2 className="text-3xl font-extrabold">One workspace, from raw report to approved deliverable</h2><p className="mt-2 max-w-2xl text-muted">Built around how Mystery Shopping operations teams actually work — folders per client and project, FORMID_1 / FORMID_2 pairs, editors, reviewers and managers.</p></Reveal>
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {features.map((f, i) => (
            <Reveal key={f.t} delay={i * 0.07}><div className="card h-full p-5 transition hover:-translate-y-1 hover:border-brand/40">
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-brand to-violet text-white"><f.icon size={20} /></span>
              <h3 className="mt-4 font-bold">{f.t}</h3><p className="mt-1.5 text-sm text-muted">{f.d}</p></div></Reveal>
          ))}
        </div>
      </section>

      <section id="workflow" className="border-y bg-surface/40 py-20">
        <div className="mx-auto max-w-7xl px-4">
          <Reveal><h2 className="text-3xl font-extrabold">How it works</h2><p className="mt-2 max-w-2xl text-muted">From upload to decision in four steps. The AI proposes — your reviewers decide.</p></Reveal>
          <Reveal delay={0.1}><div className="card mt-8 overflow-x-auto p-4"><div className="min-w-[640px]"><PipelineDiagram active={1} /></div></div></Reveal>
          <div className="mt-8 grid gap-4 md:grid-cols-4">
            {[['Upload the pair', 'FORMID_1.pdf is the immutable RAW. FORMID_2.pdf is the editor’s version. Missing pairs, duplicates and bad names are caught immediately.'],
              ['Extract & compare', 'Text is extracted (OCR for scans), aligned word-by-word, and checked against versioned client, project and form rules.'],
              ['Evidence-linked findings', 'Each finding quotes the source, shows the page, cites the rule version and proposes a correction — never invented.'],
              ['Human decision', 'Accept, reject, edit or escalate each finding, then approve or send back. Resubmissions stay on the same Form ID.']].map(([t, d], i) => (
              <Reveal key={t} delay={i * 0.08}><div className="card h-full p-5"><span className="text-3xl font-extrabold grad-text">0{i + 1}</span><h3 className="mt-2 font-bold">{t}</h3><p className="mt-1 text-sm text-muted">{d}</p></div></Reveal>
            ))}
          </div>
        </div>
      </section>

      <section id="security" className="mx-auto max-w-7xl px-4 py-20">
        <div className="grid items-center gap-10 lg:grid-cols-2">
          <Reveal><h2 className="text-3xl font-extrabold">Enterprise security. <span className="grad-text">Humans stay in charge.</span></h2>
            <p className="mt-3 text-muted">Confidential reports never leave your boundary unless an administrator explicitly approves an AI provider. Uploaded documents are treated as untrusted data and can never instruct the system.</p></Reveal>
          <div className="grid gap-3 sm:grid-cols-2">
            {[[UserCheck, 'Human validation', 'No automatic approvals — every finding gets a reviewer decision.'], [Lock, 'Role-based access', 'Super Admin → Admin → Manager → Editor, enforced on the server per client and project.'],
              [Eye, 'Immutable originals', 'RAW PDFs are never overwritten; resubmissions are versioned with timestamps.'], [ScrollText, 'Full audit trail', 'Accounts, assignments, uploads, decisions and rule versions are all logged.']].map(([I, t, d]: any, i) => (
              <Reveal key={t} delay={i * 0.06}><div className="card h-full p-4"><I size={20} className="text-cyan" /><h3 className="mt-2 text-sm font-bold">{t}</h3><p className="mt-1 text-xs text-muted">{d}</p></div></Reveal>
            ))}
          </div>
        </div>
        <Reveal><div className="glass mt-16 flex flex-col items-center gap-4 p-10 text-center"><CopilotCharacter mode="celebrate" size={130} /><h2 className="text-2xl font-extrabold">Ready to see your next report checked?</h2>
          <Link to={launch} className="btn-primary !px-7 !py-3">Launch Dashboard <ArrowRight size={18} /></Link></div></Reveal>
      </section>
      <footer className="border-t py-8 text-center text-xs text-muted">© {new Date().getFullYear()} Mystery Shopping AI — Intelligent Quality Operations</footer>
    </div>
  )
}
