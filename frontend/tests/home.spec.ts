import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import HomePage from '../src/pages/HomePage.vue'
import { api } from '../src/api/client'
vi.mock('../src/api/client', () => ({
  ensureSession: vi.fn().mockResolvedValue({ expiresAt: '2026-11-24T00:00:00Z' }),
  errorText: (error: Error) => error.message,
  api: { tasks: vi.fn(), templates: vi.fn() },
}))
describe('home dashboard', () => {
  beforeEach(() => {
    vi.mocked(api.tasks).mockResolvedValue({ items: [] })
    vi.mocked(api.templates).mockResolvedValue({ items: [] })
  })
  it('clearly identifies Mock and shows an honest empty state', async () => {
    const wrapper = mount(HomePage, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()
    expect(wrapper.text()).toContain('第一份作品，等你开始')
    expect(wrapper.text()).toContain('Mock 模式按标签提取文字')
    expect(wrapper.text()).toContain('版式示意')
    expect(wrapper.text()).not.toContain('已完成')
  })
  it('offers retry after the backend fails, without inventing recent works', async () => {
    vi.mocked(api.tasks).mockRejectedValueOnce(new Error('后端暂不可用'))
    const wrapper = mount(HomePage, { global: { stubs: ['RouterLink'] } })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('后端暂不可用')
    await wrapper.get('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('第一份作品，等你开始')
    expect(wrapper.findAll('.work-card')).toHaveLength(0)
  })
})
