import { ApiError, invalidateSession, request } from './client'
import type { Fields, Template, TemplateSummary } from './types'

export type GridTarget = keyof Fields | 'image'
export interface Placement { target: GridTarget; column: number; row: number; columnSpan: number; rowSpan: number }
export interface Grid { columns: number; rows: number; margin: number; gap: number; placements: Placement[] }
export interface TemplateDraft {
  id: string; version: number; status: 'draft' | 'pending' | 'rejected' | 'approved'
  sourceTemplateId: string; document: Template; grid: Grid | null; reviewComment: string
  publicationId: string | null; createdAt: string; updatedAt: string
}
export type Publication = TemplateSummary & { publishedAt: string }
const json = (body: unknown, method = 'POST') => ({ method, body: JSON.stringify(body) })

async function imageRequest(path: string, body: unknown): Promise<string> {
  let response: Response
  try { response = await fetch(path, { ...json(body), credentials: 'same-origin', headers: { 'Content-Type': 'application/json' } }) }
  catch { throw new ApiError(0, 'NETWORK_ERROR', '预览连接中断，请检查网络后重试；编辑内容已保留。') }
  if (!response.ok) {
    if (response.status === 401) invalidateSession()
    const error = await response.json().catch(() => null) as { error?: { code: string; message: string; details?: Record<string, unknown> } } | null
    throw new ApiError(response.status, error?.error?.code || 'PREVIEW_FAILED', error?.error?.message || '预览失败，请重试', error?.error?.details)
  }
  return URL.createObjectURL(await response.blob())
}

export const workbench = {
  drafts: () => request<{ items: TemplateDraft[] }>('/api/v2/template-drafts'),
  draft: (id: string) => request<TemplateDraft>('/api/v2/template-drafts/' + id),
  create: (sourceTemplateId: string, name: string) => request<TemplateDraft>('/api/v2/template-drafts', json({ sourceTemplateId, name })),
  save: (id: string, expectedVersion: number, document: Template, grid: Grid | null) => request<TemplateDraft>('/api/v2/template-drafts/' + id, json({ expectedVersion, document, grid }, 'PATCH')),
  preview: (draft: TemplateDraft, fields: Fields, assetId?: string) => imageRequest(`/api/v2/template-drafts/${draft.id}/preview`, { expectedVersion: draft.version, fields, ...(assetId ? { assetId } : {}) }),
  submit: (draft: TemplateDraft) => request<TemplateDraft>(`/api/v2/template-drafts/${draft.id}/submit`, json({ expectedVersion: draft.version })),
  queue: () => request<{ items: TemplateDraft[] }>('/api/v2/template-reviews'),
  review: (draft: TemplateDraft, decision: 'approve' | 'reject', comment: string) => request<TemplateDraft>(`/api/v2/template-drafts/${draft.id}/review`, json({ expectedVersion: draft.version, decision, comment })),
  reviewPreview: (draft: TemplateDraft) => imageRequest(`/api/v2/template-drafts/${draft.id}/review-preview`, { expectedVersion: draft.version }),
  published: () => request<{ items: Publication[]; total: number }>('/api/v2/published-templates'),
}
