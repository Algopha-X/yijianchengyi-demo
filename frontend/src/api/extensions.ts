/** Explicit v2 additions. The generated v1 contract types remain unchanged. */
import type { components } from '../types/api'

export type TaskView = components['schemas']['Task'] & {
  displayName: string | null
  managementRevision: number
  activeJob: components['schemas']['Job'] | null
}
export type ExportFormat = 'png' | 'jpg' | 'pdf'
export type ExportScale = 1 | 2
export type FieldLayoutEdit = Partial<Pick<components['schemas']['textField'], 'fontSize' | 'minFontSize' | 'fontWeight' | 'color' | 'align' | 'box'>>
export interface LayoutPatch { expectedRevision: number; fields?: Partial<Record<keyof components['schemas']['Fields'], FieldLayoutEdit>>; imageSlot?: Partial<Pick<components['schemas']['template.schema']['imageSlot'], 'fit' | 'box'>>; reset?: boolean }
export interface LayoutView { revision: number; document: Omit<components['schemas']['template.schema'], '$defs'>; customized: boolean }
export interface LayoutResult { task: TaskView; document: LayoutView['document']; customized: boolean }
