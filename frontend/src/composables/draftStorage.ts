import type { Draft, Fields } from '../api/types'

export interface DraftCopy {
  version: 2
  scope: string
  taskId: string | null
  savedAt: number
  baseRevision: number | null
  draft: Draft
  beforeFields?: Fields
  storageKey?: string
}

const prefix = 'ycy-v2:'
// Each page writes its own copy: closing an unchanged tab must not overwrite
// another tab's offline edits, even when both edit the same server task.
const writer = crypto.randomUUID()
const validScope = (scope: string) => /^[a-zA-Z0-9_-]{8,128}$/.test(scope)
export function draftKey(scope: string, id: string | null) {
  if (!validScope(scope)) throw new Error('会话尚未就绪，不能保存本地草稿。')
  return `${prefix}draft:${scope}:${id || 'new'}`
}
export function jobKey(scope: string, id: string) {
  if (!validScope(scope)) throw new Error('会话尚未就绪，不能保存任务状态。')
  return `${prefix}job:${scope}:${id}`
}
function isDraft(value: unknown): value is Draft {
  if (!value || typeof value !== 'object') return false
  const draft = value as Draft
  return typeof draft.rawText === 'string' && ['poster', 'article_image'].includes(draft.scenario) &&
    Array.isArray(draft.assetIds) && draft.assetIds.length <= 1 && draft.assetIds.every(id => typeof id === 'string') &&
    !!draft.fields && ['title', 'subtitle', 'body', 'time', 'location', 'organizer'].every(key => typeof draft.fields[key as keyof Fields] === 'string')
}
export function writeDraft(scope: string, taskId: string | null, draft: Draft, baseRevision: number | null, beforeFields?: Fields) {
  const copy: DraftCopy = { version: 2, scope, taskId, draft, baseRevision, beforeFields, savedAt: Date.now() }
  try { localStorage.setItem(draftKey(scope, taskId) + ':' + writer, JSON.stringify(copy)); return true }
  catch { return false }
}
export function readDraft(scope: string, taskId: string | null, baseline?: Draft): DraftCopy | null {
  if (!validScope(scope)) return null
  try {
    const base = draftKey(scope, taskId)
    const copies: DraftCopy[] = []
    for (let index = 0; index < localStorage.length; index++) {
      const key = localStorage.key(index)
      if (!key || (key !== base && !key.startsWith(base + ':'))) continue
      try {
        const value = JSON.parse(localStorage.getItem(key) || 'null') as DraftCopy | null
        if (!value || value.version !== 2 || value.scope !== scope || value.taskId !== taskId || !isDraft(value.draft) || !Number.isFinite(value.savedAt)) continue
        if (baseline && JSON.stringify(value.draft) === JSON.stringify(baseline)) continue
        copies.push({ ...value, storageKey: key })
      } catch { /* one corrupt copy cannot hide another valid copy */ }
    }
    return copies.sort((a, b) => b.savedAt - a.savedAt)[0] || null
  } catch { return null }
}
export function removeDraft(scope: string, id: string | null, selectedKey?: string) {
  try {
    const base = draftKey(scope, id)
    const key = selectedKey || base + ':' + writer
    if (key === base || key.startsWith(base + ':')) localStorage.removeItem(key)
  } catch { /* server draft remains available */ }
}
export function writeJob(scope: string, id: string, jobId: string) {
  try { localStorage.setItem(jobKey(scope, id), jobId) } catch { /* v2 task.activeJob is authoritative */ }
}
export function readJob(scope: string, id: string) {
  try { return localStorage.getItem(jobKey(scope, id)) } catch { return null }
}
export function removeJob(scope: string, id: string) {
  try { localStorage.removeItem(jobKey(scope, id)) } catch { /* completed server job is authoritative */ }
}
