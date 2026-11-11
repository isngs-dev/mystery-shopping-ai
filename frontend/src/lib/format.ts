export const STATUS: Record<string, { label: string; tone: string }> = {
  pending: { label: 'Pending', tone: 'muted' },
  in_progress: { label: 'In Progress', tone: 'brand' },
  qc_review: { label: 'QC Review', tone: 'violet' },
  re_edit_required: { label: 'Re-edit Required', tone: 'warn' },
  approved: { label: 'Approved', tone: 'ok' },
  closed: { label: 'Closed', tone: 'ok' },
  manual_review_required: { label: 'Manual Review', tone: 'warn' },
  blocked: { label: 'Blocked', tone: 'danger' },
}
export const SEVERITY: Record<string, { label: string; tone: string; rank: number }> = {
  critical: { label: 'Critical', tone: 'danger', rank: 0 },
  major: { label: 'Major', tone: 'warn', rank: 1 },
  minor: { label: 'Minor', tone: 'brand', rank: 2 },
  info: { label: 'Info', tone: 'muted', rank: 3 },
}
export const REVIEW: Record<string, { label: string; tone: string }> = {
  pending: { label: 'Needs review', tone: 'muted' },
  accepted: { label: 'Accepted', tone: 'ok' },
  rejected: { label: 'Rejected', tone: 'danger' },
  edited: { label: 'Edited & accepted', tone: 'ok' },
  not_applicable: { label: 'Not applicable', tone: 'muted' },
  escalated: { label: 'Escalated', tone: 'warn' },
}
export const TYPE_LABEL: Record<string, string> = {
  reviewer_added: 'Added by reviewer',
  missed_correction: 'Missed correction',
  new_error: 'New error',
  grammar: 'Grammar',
  spelling: 'Spelling',
  missing_info: 'Missing information',
  meaning_change: 'Meaning change',
  formatting: 'Formatting',
  instruction_violation: 'Instruction violation',
  extraction_issue: 'Extraction issue',
  security: 'Security',
}
export const ISSUE_LABEL: Record<string, string> = {
  naming_error: 'Incorrect file name',
  invalid_pdf: 'Invalid PDF',
  unreadable: 'Unreadable PDF',
  duplicate: 'Duplicate',
  missing_raw: 'Missing RAW (_1)',
  missing_edited: 'Missing edited (_2)',
  too_large: 'File too large',
  invalid_state: 'Wrong state',
  unknown_folder: 'Unknown folder',
  extraction_failed: 'Extraction failed',
  extraction_ocr_unavailable: 'OCR unavailable',
  extraction_partial: 'Partial extraction',
}
export const ROLE_LABEL: Record<string, string> = {
  super_admin: 'Super Admin',
  admin: 'Admin',
  manager: 'Editor & QC Manager',
  editor: 'Editor',
  qc_reviewer: 'Editor & QC Lead',
  ai_lead: 'AI Executive / AI Lead',
  process_owner: 'Operations Process Owner',
  ops_sponsor: 'Operations Sponsor',
  client_poc: 'Project / Client POC',
  reporting: 'Management / Reporting',
}

const TONES: Record<string, string> = {
  muted: 'bg-muted/15 text-muted border-muted/25',
  brand: 'bg-brand/15 text-brand border-brand/30',
  violet: 'bg-violet/15 text-violet border-violet/30',
  warn: 'bg-warn/15 text-warn border-warn/30',
  ok: 'bg-ok/15 text-ok border-ok/30',
  danger: 'bg-danger/15 text-danger border-danger/30',
  cyan: 'bg-cyan/15 text-cyan border-cyan/30',
}
export const tone = (t: string) => TONES[t] || ''

export function fmtDate(iso?: string | null, withTime = true) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString(undefined, withTime ? { dateStyle: 'medium', timeStyle: 'short' } : { dateStyle: 'medium' })
}
export function ago(iso?: string | null) {
  if (!iso) return '—'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
export const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v}%`)
export const hrs = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : v >= 48 ? `${(v / 24).toFixed(1)} d` : `${v} h`
export function bytes(n: number) {
  return n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`
}
