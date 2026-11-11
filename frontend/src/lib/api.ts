export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function messageFrom(data: any, fallback: string): string {
  const d = data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((e) => `${(e.loc || []).slice(1).join('.')}: ${e.msg}`).join('; ')
  return fallback
}

async function request<T = any>(method: string, url: string, body?: any, isForm = false): Promise<T> {
  const headers: Record<string, string> = { 'X-Requested-With': 'XMLHttpRequest' }
  let payload: BodyInit | undefined
  if (body !== undefined) {
    if (isForm) payload = body
    else { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body) }
  }
  let res: Response
  try {
    res = await fetch(url, { method, headers, body: payload, credentials: 'same-origin' })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection and try again.')
  }
  if (res.status === 204) return undefined as T
  const ct = res.headers.get('content-type') || ''
  const data = ct.includes('json') ? await res.json().catch(() => null) : null
  if (!res.ok) {
    if (res.status === 401 && !url.includes('/auth/login')) window.dispatchEvent(new Event('msai:unauthorized'))
    throw new ApiError(res.status, messageFrom(data, `Request failed (${res.status})`))
  }
  return data as T
}

export const api = {
  get: <T = any>(url: string) => request<T>('GET', url),
  post: <T = any>(url: string, body?: any) => request<T>('POST', url, body ?? {}),
  put: <T = any>(url: string, body?: any) => request<T>('PUT', url, body ?? {}),
  patch: <T = any>(url: string, body?: any) => request<T>('PATCH', url, body ?? {}),
  upload: <T = any>(url: string, form: FormData) => request<T>('POST', url, form, true),
}

export function qs(params: Record<string, any>): string {
  const p = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '' && v !== 'all') p.set(k, String(v)) })
  const s = p.toString()
  return s ? `?${s}` : ''
}

export async function download(url: string, fallbackName: string) {
  const res = await fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
  if (!res.ok) {
    const data = await res.json().catch(() => null)
    throw new ApiError(res.status, messageFrom(data, 'Export failed'))
  }
  const blob = await res.blob()
  const cd = res.headers.get('content-disposition') || ''
  const name = /filename="([^"]+)"/.exec(cd)?.[1] || fallbackName
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(a.href), 2000)
}
