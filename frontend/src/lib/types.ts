export type Role = 'super_admin' | 'admin' | 'manager' | 'editor' | 'qc_reviewer' | 'ai_lead' | 'process_owner' | 'ops_sponsor' | 'client_poc' | 'reporting'
export interface User { id: number; email: string; name: string; role: Role; role_label: string; level: number; is_active: boolean; must_change_password: boolean; is_sample: boolean; last_login_at?: string; project_ids?: number[] }
export interface Session { user: User; permissions: string[]; project_ids: number[] | null; unread_notifications: number }
export interface Brief { id: number; name: string; role: string }
export interface Doc { id: number; kind: 'raw' | 'edited'; version_no: number; logical_name: string; stored_name: string; page_count: number | null; extraction_status: string; extraction_summary: any; origin?: 'human' | 'ai_assisted'; uploaded_at: string; sha256: string; size_bytes: number; status: string }
export interface FormRow {
  id: number; form_code: string; status: string; status_reason?: string; project_id: number; project: string; client: string; path: string
  assigned_editor: Brief | null; assigned_reviewer: Brief | null; due_at: string | null; is_sample: boolean; created_at: string; updated_at: string
  pair_status: string; edited_versions: number; raw_document_id: number | null; latest_edited_document_id: number | null; last_activity_at: string
  documents?: Doc[]; assignments?: any[]; comments?: any[]; attempts?: any[]; runs?: any[]; confirmed_findings?: Finding[]; logical_path?: string
}
export interface Box { page: number; x0: number; y0: number; x1: number; y1: number }
export interface Finding {
  id: number; run_id: number; form_id: number; type: string; category: string; category_label: string; error_subtype: string | null; error_label: string; points: number | null
  severity: 'critical' | 'major' | 'minor' | 'info'; source: string
  confidence: number | null; rule_label: string | null; rule_version_id: number | null; title: string; description: string
  raw_excerpt: string | null; raw_page: number | null; raw_boxes: Box[]; edited_excerpt: string | null; edited_page: number | null; edited_boxes: Box[]
  suggested_correction: string | null; needs_human_attention: boolean; review_status: string; reviewer_comment: string | null; original: any
}
export interface Run { id: number; status: string; stages: { name: string; status: string; detail?: string }[]; ai_status: string; ai_provider?: string; ai_model?: string; ai_message?: string; rule_versions: any[]; stats: any; error?: string; attempt_no: number; created_at: string; finished_at?: string }

export interface AutoEditItem { page: number; original: string; replacement: string; reason: string; category: string; confidence: number; source: 'rules' | 'ai'; raw_boxes?: Box[]; edited_boxes?: Box[] }
export interface AutoEditSkip { page: number; original: string; proposed: string | null; reason: string }
export interface AutoEdit {
  id: number; form_id: number; status: 'queued' | 'running' | 'completed' | 'no_changes' | 'failed'; engine: string | null; ai_status: string | null; ai_model: string | null
  message: string | null; edits: AutoEditItem[]; skipped: AutoEditSkip[]; stats: any; base_document_id: number; result_document_id: number | null; created_at: string; finished_at: string | null
}
