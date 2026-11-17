import { describe, expect, it } from 'vitest'
import { createSaveQueue } from '../src/composables/saveQueue'
import { buildPatch, emptyFields } from '../src/api/types'
import type { Draft } from '../src/api/types'

describe('serialized draft saving', () => {
  it('saves the latest edits after the in-flight request without parallel overwrites', async () => {
    const releases: (() => void)[] = []
    const seen: string[] = []
    let text = '初稿'
    let active = 0
    let maxActive = 0
    const queue = createSaveQueue(async () => {
      active++; maxActive = Math.max(maxActive, active)
      seen.push(text)
      await new Promise<void>(resolve => releases.push(resolve))
      active--
    })
    queue.mark()
    const first = queue.flush()
    text = '第二稿'; queue.mark()
    text = '最新稿'; queue.mark()
    const second = queue.flush()
    expect(seen).toEqual(['初稿'])
    releases.shift()!()
    await Promise.resolve(); await Promise.resolve()
    expect(seen).toEqual(['初稿', '最新稿'])
    releases.shift()!()
    await Promise.all([first, second])
    expect(maxActive).toBe(1)
    expect(queue.dirty).toBe(false)
  })
  it('retains dirty edits when network fails and allows retry', async () => {
    let fails = true
    const queue = createSaveQueue(async () => { if (fails) throw new Error('断网') })
    queue.mark()
    await expect(queue.flush()).rejects.toThrow('断网')
    expect(queue.dirty).toBe(true)
    fails = false
    await queue.flush()
    expect(queue.dirty).toBe(false)
  })
})
describe('revision-bound minimal patches', () => {
  const saved: Draft = { rawText: '原文', scenario: 'poster', style: null, templateId: null, assetIds: [], fields: emptyFields() }
  it('does not invalidate an unchanged task', () => {
    expect(buildPatch(saved, saved, 3)).toBeNull()
  })
  it('keeps all six fields in an edited fields payload and sends the latest revision', () => {
    const changed = { ...saved, fields: { ...saved.fields, time: '明天14:00' } }
    expect(buildPatch(changed, saved, 8)).toEqual({ expectedRevision: 8, fields: changed.fields })
    expect(buildPatch(changed, saved, 8)).not.toHaveProperty('rawText')
  })
})
