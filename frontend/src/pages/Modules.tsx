import { CheckCircle2, Clock } from 'lucide-react'
import { Badge, PageHeader } from '../components/ui'
import { useCopilotHint } from '../lib/copilot'

const MODULES: { name: string; desc: string; status: 'live' | 'foundation' | 'planned'; note: string }[] = [
  { name: 'AI-assisted report QC', desc: 'RAW vs edited comparison, rule checks, evidence-linked findings, human decisions.', status: 'live', note: 'Implemented and tested end to end.' },
  { name: 'Report editing assistance (AI editor)', desc: 'RAW upload → AI corrects spelling and grammar → edited PDF goes to QC for RAW vs edited comparison.', status: 'live', note: 'Built-in corrector always available; a language model is used only when an approved provider is configured. Facts, figures and names are never changed; every edit is logged.' },
  { name: 'Production queue monitoring & deadline alerts', desc: 'Due dates, overdue flags and notifications.', status: 'foundation', note: 'Due dates, overdue counts and in-app notifications exist. Email/Slack alerts need an approved channel.' },
  { name: 'Checklist & knowledge management', desc: 'Versioned rules with approval workflow.', status: 'foundation', note: 'Rule library is live; document/knowledge-article storage is planned.' },
  { name: 'Training insights & recurring errors', desc: 'Repeat-error analysis by editor and category.', status: 'foundation', note: 'Repeat-errors report is live; automated training assignments are planned.' },
  { name: 'Automated operational reporting', desc: 'Scheduled report delivery.', status: 'foundation', note: 'On-demand CSV/Excel exports are live; scheduled delivery is planned.' },
  { name: 'Form & survey validation', desc: 'Validating shopper submissions against survey logic.', status: 'planned', note: 'Not implemented. Needs the survey-platform integration and approved credentials.' },
  { name: 'Shopper / editor follow-up drafting', desc: 'Drafting follow-up messages.', status: 'planned', note: 'Not implemented. Sending requires an approved email integration.' },
  { name: 'Client feedback & corrective-action tracking', desc: 'Tracking client-raised issues to closure.', status: 'planned', note: 'Not implemented.' },
  { name: 'EP inventory & capacity planning', desc: 'Matching capacity to demand.', status: 'planned', note: 'Not implemented. Needs the EP inventory data source.' },
  { name: 'SOP & onboarding assistant', desc: 'Answering process questions from SOPs.', status: 'planned', note: 'Not implemented.' },
  { name: 'Process analytics & automation discovery', desc: 'Finding automation opportunities in operations data.', status: 'planned', note: 'Not implemented; the audit trail provides the data foundation.' },
]
const TONE = { live: 'ok', foundation: 'brand', planned: 'muted' } as const
const LABEL = { live: 'Live', foundation: 'Foundation in place', planned: 'Planned — not built' } as const

export default function Modules() {
  useCopilotHint('idle', 'This page is honest about what is built. Only “Live” items are working features.')
  return (
    <div>
      <PageHeader title="Automation modules" sub="The core QC workflow is implemented. Other modules are architectural extension points and are clearly marked where unfinished." />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {MODULES.map((m) => (
          <div key={m.name} className="card p-4">
            <div className="flex items-start justify-between gap-2"><h3 className="font-bold">{m.name}</h3><Badge t={TONE[m.status]}>{m.status === 'live' ? <CheckCircle2 size={11} /> : <Clock size={11} />}{LABEL[m.status]}</Badge></div>
            <p className="mt-1 text-sm text-muted">{m.desc}</p><p className="mt-2 text-xs text-ink/80">{m.note}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
