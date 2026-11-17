import type { components } from '../types/api'
import type { TaskView } from './extensions'

export type Schemas = components['schemas']
export type Task = TaskView
export type Fields = Schemas['Fields']
export type FieldKey = keyof Fields
export type Job = Schemas['Job'] & { queuePosition?: number | null }
export type TemplateSummary = Schemas['TemplateSummary']
export type Template = Omit<Schemas['template.schema'], '$defs'>
export type Draft = Pick<Task, 'rawText' | 'scenario' | 'style' | 'templateId' | 'assetIds' | 'fields'>

export const fieldKeys: FieldKey[] = ['title', 'subtitle', 'body', 'time', 'location', 'organizer']
export const fieldLabels: Record<FieldKey, string> = {
  title: '标题', subtitle: '副标题', body: '正文', time: '时间', location: '地点', organizer: '主办方',
}
export const emptyFields = (): Fields => ({ title: '', subtitle: '', body: '', time: '', location: '', organizer: '' })
export const phaseLabels: Record<Task['phase'], string> = {
  draft: '草稿', needs_input: '待补充', needs_confirmation: '待确认', ready_to_render: '可生成',
  rendering: '生成中', preview_ready: '已生成', failed: '待重试',
}
export const styleLabels: Record<TemplateSummary['style'], string> = {
  fresh: '清新', asymmetric: '不对称', lively: '活泼', elegant: '典雅', modern: '现代', retro: '复古',
}
export const stageLabels: Record<Job['stage'], string> = {
  queued: '正在排队', classifying: '正在理解内容', recommending: '正在匹配模板', checking_fields: '正在检查信息',
  polishing: '正在处理文字', validating: '正在校验内容', adapting_images: '正在适配图片', rendering: '正在排版出图',
  done: '处理完成', failed: '处理失败', superseded: '已有新版本',
}

export function draftFromTask(task: Task): Draft {
  return { rawText: task.rawText, scenario: task.scenario, style: task.style, templateId: task.templateId,
    assetIds: [...task.assetIds], fields: { ...task.fields } }
}

export function buildPatch(draft: Draft, saved: Draft, expectedRevision: number): Schemas['TaskPatch'] | null {
  const patch: Schemas['TaskPatch'] = { expectedRevision }
  for (const key of ['rawText', 'scenario', 'style', 'templateId', 'assetIds', 'fields'] as const) {
    if (JSON.stringify(draft[key]) !== JSON.stringify(saved[key])) Object.assign(patch, { [key]: draft[key] })
  }
  return Object.keys(patch).length > 1 ? patch : null
}
