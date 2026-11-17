import type { Schemas } from './types'
import type { ExportFormat, ExportScale, LayoutPatch, LayoutResult, LayoutView, TaskView } from './extensions'

let sessionRequest: Promise<Schemas['Session']> | undefined
let sessionScope = ''
export function getSessionScope() { return sessionScope }
export function invalidateSession() { sessionRequest = undefined; sessionScope = '' }

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string,
    public details: Record<string, unknown> = {}, public requestId = '') {
    super(message)
    this.name = 'ApiError'
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { ...init, credentials: 'same-origin', headers: {
      ...(init.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    } })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'NETWORK_ERROR', '连接失败，内容已保留。请检查服务是否启动，再重试。')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as Schemas['ErrorResponse'] | null
    if (response.status === 401) invalidateSession()
    throw new ApiError(response.status, body?.error?.code || 'HTTP_ERROR',
      body?.error?.message || '请求未能完成，请稍后重试', body?.error?.details, body?.requestId)
  }
  const scopeHeader = response.headers.get('X-Session-Scope')
  if (scopeHeader === '') invalidateSession()
  if (path === '/api/session' || scopeHeader) {
    const scope = scopeHeader || ''
    if (!/^[a-zA-Z0-9_-]{8,128}$/.test(scope)) throw new ApiError(503, 'SESSION_SCOPE_MISSING', '会话服务尚未就绪，请稍后重新连接。')
    if (path !== '/api/session') invalidateSession()
    sessionScope = scope
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '操作未能完成，请重试'
}

export function ensureSession(renew = false): Promise<Schemas['Session']> {
  if (renew || !sessionRequest) {
    sessionRequest = request<Schemas['Session']>('/api/session', { method: 'POST' })
      .catch(error => { invalidateSession(); throw error })
  }
  return sessionRequest
}

export const api = {
  health: () => request<Schemas['Health']>('/api/health'),
  async templates() {
    const [system, published] = await Promise.all([
      request<Schemas['TemplateList']>('/api/templates'),
      request<Schemas['TemplateList']>('/api/v2/published-templates'),
    ])
    return { items: [...system.items, ...published.items] }
  },
  async template(id: string) {
    if (id.startsWith('draft-')) {
      const result = await request<{ document: import('./types').Template }>(`/api/v2/published-templates/${encodeURIComponent(id)}`)
      return result.document
    }
    return request<import('./types').Template>(`/api/templates/${encodeURIComponent(id)}`)
  },
  tasks: () => request<{ items: TaskView[] }>('/api/v2/tasks'),
  task: (id: string) => request<TaskView>(`/api/v2/tasks/${encodeURIComponent(id)}`),
  async create(body: Schemas['TaskCreate']) {
    const result = await request<Schemas['Task']>('/api/tasks', { method: 'POST', body: JSON.stringify(body) })
    return api.task(result.id)
  },
  async patch(id: string, body: Schemas['TaskPatch']) {
    await request<Schemas['Task']>(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
    return api.task(id)
  },
  async confirm(id: string, body: Schemas['ConfirmRequest']) {
    await request<Schemas['Task']>(`/api/tasks/${id}/confirm`, { method: 'POST', body: JSON.stringify(body) })
    return api.task(id)
  },
  rename: (task: TaskView, displayName: string) => request<TaskView>(`/api/v2/tasks/${task.id}/rename`, {
    method: 'POST', body: JSON.stringify({ expectedRevision: task.revision, expectedManagementRevision: task.managementRevision, displayName }),
  }),
  copy: (task: TaskView) => request<TaskView>(`/api/v2/tasks/${task.id}/copy`, { method: 'POST', body: JSON.stringify({ expectedRevision: task.revision }) }),
  remove: (task: TaskView) => request<void>(`/api/v2/tasks/${task.id}?expectedRevision=${task.revision}`, { method: 'DELETE' }),
  start: (id: string, kind: 'analyze' | 'render', revision: number, key: string) => request<Schemas['Job']>(`/api/tasks/${id}/${kind}`, {
    method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify({ expectedRevision: revision }),
  }),
  job: (id: string, signal?: AbortSignal) => request<import('./types').Job>(`/api/v2/jobs/${id}`, { signal }),
  layout: (id: string) => request<LayoutView>(`/api/v2/tasks/${id}/layout`),
  saveLayout: (id: string, body: LayoutPatch) => request<LayoutResult>(`/api/v2/tasks/${id}/layout`, { method: 'PATCH', body: JSON.stringify(body) }),
  upload: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<Schemas['Asset']>('/api/assets', { method: 'POST', body })
  },
  async download(id: string, revision: number, format: ExportFormat, filename: string, scale: ExportScale = 1) {
    let response: Response
    try { response = await fetch(`/api/v2/tasks/${id}/export?format=${format}&expectedRevision=${revision}&scale=${scale}`, { credentials: 'same-origin' }) }
    catch { throw new ApiError(0, 'NETWORK_ERROR', '下载连接中断，请检查网络后重试；作品已保留。') }
    if (!response.ok) {
      if (response.status === 401) invalidateSession()
      const body = await response.json().catch(() => null) as Schemas['ErrorResponse'] | null
      throw new ApiError(response.status, body?.error?.code || 'EXPORT_FAILED', body?.error?.message || '导出未能完成，请重试。', body?.error?.details, body?.requestId)
    }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `${filename.replace(/[\\/:*?"<>|]/g, '').slice(0, 60) || '一键成艺'}.${format}`
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
}
