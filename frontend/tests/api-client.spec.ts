import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, ensureSession, getSessionScope, invalidateSession, request } from '../src/api/client'
afterEach(() => { vi.unstubAllGlobals(); invalidateSession() })
describe('same-origin API client', () => {
  it('sends JSON with same-origin cookies', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    expect(await request('/api/tasks', { method: 'POST', body: '{}' })).toEqual({ ok: true })
    expect(fetch).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({ credentials: 'same-origin', headers: { 'Content-Type': 'application/json' } }))
  })
  it('preserves revision conflict and request id for actionable recovery', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'REVISION_CONFLICT', message: '版本冲突', details: { currentRevision: 7 } }, requestId: 'request-1' }), { status: 409 })))
    await expect(request('/api/tasks/1')).rejects.toMatchObject({ status: 409, code: 'REVISION_CONFLICT', details: { currentRevision: 7 }, requestId: 'request-1' })
  })
  it('keeps browser multipart boundary for an upload', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 201 }))
    vi.stubGlobal('fetch', fetch)
    await request('/api/assets', { method: 'POST', body: new FormData() })
    expect(fetch.mock.calls[0][1].headers).not.toHaveProperty('Content-Type')
  })
  it('turns a broken connection into a recoverable message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(request('/api/tasks')).rejects.toBeInstanceOf(ApiError)
    await expect(request('/api/tasks')).rejects.toMatchObject({ code: 'NETWORK_ERROR', status: 0 })
  })
  it('creates a fresh session on explicit retry after an expired cookie', async () => {
    const session = (scope: string) => new Response(JSON.stringify({ expiresAt: '2026-12-01T00:00:00Z' }), { headers: { 'X-Session-Scope': scope } })
    const fetch = vi.fn().mockResolvedValueOnce(session('owner_scope_A')).mockResolvedValueOnce(new Response('{}', { status: 401 })).mockResolvedValueOnce(session('owner_scope_B'))
    vi.stubGlobal('fetch', fetch)
    await ensureSession(); expect(getSessionScope()).toBe('owner_scope_A')
    await expect(request('/api/v2/tasks')).rejects.toMatchObject({ status: 401 })
    await ensureSession(); expect(getSessionScope()).toBe('owner_scope_B')
    expect(fetch.mock.calls.map(call => call[0])).toEqual(['/api/session', '/api/v2/tasks', '/api/session'])
  })
  it('requires stable server scope instead of mixing drafts under a fallback identity', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}')))
    await expect(ensureSession()).rejects.toMatchObject({ code: 'SESSION_SCOPE_MISSING' })
  })
  it('supports an empty successful delete response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
    expect(await request('/api/v2/tasks/1', { method: 'DELETE' })).toBeUndefined()
  })
  it('renews after a successful password reset explicitly clears session scope', async () => {
    const session = (scope: string) => new Response('{}', { headers: { 'X-Session-Scope': scope } })
    const fetch = vi.fn().mockResolvedValueOnce(session('owner_scope_A'))
      .mockResolvedValueOnce(new Response('{"message":"已重置"}', { headers: { 'X-Session-Scope': '' } }))
      .mockResolvedValueOnce(session('owner_scope_B'))
    vi.stubGlobal('fetch', fetch)
    await ensureSession()
    await request('/api/v2/auth/reset-password', { method: 'POST', body: '{}' })
    expect(getSessionScope()).toBe('')
    await ensureSession()
    expect(getSessionScope()).toBe('owner_scope_B')
    expect(fetch).toHaveBeenCalledTimes(3)
  })
})
