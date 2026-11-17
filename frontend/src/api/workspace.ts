import { request } from './client'
import type { Schemas } from './types'

export interface Account { id: string; address: string; nickname: string; role: string; banned: boolean; createdAt: string }
export interface Identity { account: Account | null; verificationMode: string; anonymousTaskCount: number }
export interface Verification { challengeId: string; expiresIn: number; mode: string; developmentCode?: string; message?: string }
export type Material = Schemas['Asset'] & { name: string; tags: string[]; revision: number; visibility: 'private' | 'pending' | 'public' | 'rejected'; reviewNote: string | null; generatedMode: string | null; favorite: boolean; owned: boolean; thumbnailUrl: string }
const json = (body: unknown, method = 'POST') => ({ method, body: JSON.stringify(body) })
export const workspace = {
  me: () => request<Identity>('/api/v2/auth/me'),
  verification: (address: string, purpose: 'register' | 'reset') => request<Verification>('/api/v2/auth/verification', json({ address, purpose })),
  register: (body: { address: string; password: string; nickname: string; challengeId: string; code: string }) => request<{ account: Account }>('/api/v2/auth/register', json(body)),
  login: (address: string, password: string, importAnonymous: boolean) => request<{ account: Account }>('/api/v2/auth/login', json({ address, password, importAnonymous })),
  logout: () => request<{ account: null }>('/api/v2/auth/logout', json({})),
  reset: (body: { address: string; password: string; challengeId: string; code: string }) => request<{ message: string }>('/api/v2/auth/reset-password', json(body)),
  profile: (nickname: string) => request<{ account: Account }>('/api/v2/auth/profile', json({ nickname }, 'PATCH')),
  changePassword: (oldPassword: string, newPassword: string) => request<{ message: string }>('/api/v2/auth/change-password', json({ oldPassword, newPassword })),
  users: (offset = 0) => request<{ items: Account[]; total: number }>('/api/v2/auth/admin/users?limit=50&offset=' + offset),
  updateUser: (id: string, role: string, banned: boolean) => request<Account>('/api/v2/auth/admin/users/' + id, json({ role, banned }, 'PATCH')),
  materials: (scope = 'private', search = '', favorite = false) => request<{ items: Material[] }>('/api/v2/materials?' + new URLSearchParams({ scope, search, favorite: String(favorite) })),
  metadata: (id: string, name: string, tags: string[], requestPublic = false) => request<Material>('/api/v2/materials/' + id, json({ name, tags, requestPublic }, 'PATCH')),
  favorite: (id: string, favorite: boolean) => request<Material>('/api/v2/materials/' + id + '/favorite', json({ favorite })),
  importMaterial: (id: string) => request<Schemas['Asset']>('/api/v2/materials/' + id + '/import', json({})),
  crop: (id: string, body: { x: number; y: number; width: number; height: number }) => request<Schemas['Asset']>('/api/v2/materials/' + id + '/crop', json(body)),
  generate: (prompt: string, sourceAssetId?: string) => request<{ asset: Schemas['Asset']; mode: string; message: string }>('/api/v2/materials/generate', json({ prompt, ...(sourceAssetId ? { sourceAssetId } : {}), width: 1024, height: 1024 })),
  reviewQueue: () => request<{ items: Material[] }>('/api/v2/materials/review-queue'),
  review: (id: string, decision: 'approve' | 'reject', note: string, expectedRevision: number) => request<Material>('/api/v2/materials/' + id + '/review', json({ decision, note, expectedRevision })),
  notifications: () => request<{ items: { job: Schemas['Job']; read: boolean }[] }>('/api/v2/notifications'),
  readNotification: (id: string) => request<unknown>('/api/v2/notifications/' + id + '/read', json({})),
}
