import { beforeEach, describe, expect, it, vi } from 'vitest'
import { JSDOM } from 'jsdom'
import { draftKey, readDraft, readJob, writeDraft, writeJob } from '../src/composables/draftStorage'
import { emptyFields } from '../src/api/types'
import type { Draft } from '../src/api/types'

const draft: Draft = { rawText: '尚未同步的创作文字', scenario: 'poster', style: null, templateId: null, assetIds: [], fields: emptyFields() }
beforeEach(() => {
  vi.restoreAllMocks()
  const storageWindow = new JSDOM('', { url: 'http://localhost' }).window
  vi.stubGlobal('localStorage', storageWindow.localStorage)
  vi.stubGlobal('sessionStorage', storageWindow.sessionStorage)
  localStorage.clear(); sessionStorage.clear()
})
describe('durable session-bound recovery', () => {
  it('survives a closed tab while isolating identities and task ids', () => {
    expect(writeDraft('owner_scope_A', null, draft, null)).toBe(true)
    sessionStorage.clear()
    expect(readDraft('owner_scope_A', null)?.draft).toEqual(draft)
    expect(readDraft('owner_scope_B', null)).toBeNull()
    expect(readDraft('owner_scope_A', 'another-task')).toBeNull()
  })
  it('rejects malformed or forged-scope copies rather than silently adopting them', () => {
    localStorage.setItem(draftKey('owner_scope_A', null), JSON.stringify({ version: 2, scope: 'owner_scope_B', taskId: null, draft }))
    expect(readDraft('owner_scope_A', null)).toBeNull()
    localStorage.setItem(draftKey('owner_scope_A', null), '{broken')
    expect(readDraft('owner_scope_A', null)).toBeNull()
    expect(() => draftKey('', null)).toThrow()
  })
  it('reports browser quota failure without preventing a server save', () => {
    vi.spyOn(Object.getPrototypeOf(localStorage), 'setItem').mockImplementation(() => { throw new DOMException('quota', 'QuotaExceededError') })
    expect(writeDraft('owner_scope_A', null, draft, null)).toBe(false)
  })
  it('retains job recovery across tab closure and never leaks a job to another scope', () => {
    writeJob('owner_scope_A', 'task-1', 'job-1'); sessionStorage.clear()
    expect(readJob('owner_scope_A', 'task-1')).toBe('job-1')
    expect(readJob('owner_scope_B', 'task-1')).toBeNull()
  })
  it('does not let an unchanged tab hide another tab’s offline edits', () => {
    const saved: Draft = { ...draft, rawText: '服务器旧稿' }
    const copy = { version: 2, scope: 'owner_scope_A', taskId: 'task-1', baseRevision: 1 }
    const key = draftKey('owner_scope_A', 'task-1')
    localStorage.setItem(key + ':offline-tab', JSON.stringify({ ...copy, draft, savedAt: 100 }))
    localStorage.setItem(key + ':clean-tab', JSON.stringify({ ...copy, draft: saved, savedAt: 200 }))
    expect(readDraft('owner_scope_A', 'task-1', saved)?.draft.rawText).toBe(draft.rawText)
  })
})
