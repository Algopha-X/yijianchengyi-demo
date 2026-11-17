<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorText } from '../api/client'
import type { FieldKey, Task, Template } from '../api/types'
import { fieldKeys, fieldLabels } from '../api/types'
import type { LayoutPatch, LayoutResult } from '../api/extensions'
const props = defineProps<{ task: Task; busy: boolean; flush: () => Promise<Task> }>()
const emit = defineEmits<{ saved: [result: LayoutResult] }>()
const document = ref<Template | null>(null), selected = ref<FieldKey | 'image'>('title')
const loading = ref(false), saving = ref(false), error = ref(''), changed = ref(false), customized = ref(false)
const currentField = computed(() => document.value && selected.value !== 'image' ? document.value.fields[selected.value] : null)
const box = computed(() => selected.value === 'image' ? document.value?.imageSlot.box : currentField.value?.box)
let sequence = 0
async function load() {
  const current = ++sequence
  loading.value = true; error.value = ''
  try { const result = await api.layout(props.task.id); if (current === sequence) { document.value = result.document; customized.value = result.customized; changed.value = false } }
  catch (cause) { error.value = errorText(cause) } finally { if (current === sequence) loading.value = false }
}
async function save(reset = false) {
  saving.value = true; error.value = ''
  try {
    const latest = await props.flush()
    const fields: NonNullable<LayoutPatch['fields']> = {}
    if (document.value) for (const key of fieldKeys) {
      const field = document.value.fields[key]
      fields[key] = { fontSize: field.fontSize, minFontSize: field.minFontSize, fontWeight: field.fontWeight, color: field.color, align: field.align, box: { ...field.box } }
    }
    const result = await api.saveLayout(latest.id, reset ? { expectedRevision: latest.revision, reset: true } : { expectedRevision: latest.revision, fields, imageSlot: document.value ? { fit: document.value.imageSlot.fit, box: { ...document.value.imageSlot.box } } : undefined })
    document.value = result.document; customized.value = result.customized; changed.value = false; emit('saved', result)
  } catch (cause) { error.value = errorText(cause) } finally { saving.value = false }
}
watch(() => [props.task.id, props.task.templateId], load, { immediate: true })
</script>
<template><details class="layout-editor"><summary>调整文字与图片排版<span v-if="customized" class="recommend-badge">已自定义</span></summary><p class="field-help">选择对象，调整字号、颜色和位置后保存。保存会使旧预览与确认失效，需再次核对并生成。字体固定为已内嵌的思源黑体。</p><div v-if="loading" class="inline-notice">正在读取排版…</div><div v-if="error" class="error-card" role="alert">{{ error }}<button class="text-button" @click="load">重新读取</button></div><fieldset v-if="document" :disabled="busy || saving || loading" @input="changed = true" @change="changed = true"><label class="field-label">编辑对象<select v-model="selected"><option v-for="key in fieldKeys" :key="key" :value="key">{{ fieldLabels[key] }}</option><option value="image">图片区域</option></select></label><div class="layout-map" :style="{ aspectRatio: document.canvas.width + '/' + document.canvas.height, background: document.canvas.background }"><button v-for="key in fieldKeys" :key="key" type="button" :class="{ selected: selected === key }" :style="{ left: document.fields[key].box.x / document.canvas.width * 100 + '%', top: document.fields[key].box.y / document.canvas.height * 100 + '%', width: document.fields[key].box.width / document.canvas.width * 100 + '%', height: document.fields[key].box.height / document.canvas.height * 100 + '%' }" @click="selected = key">{{ fieldLabels[key] }}</button><button type="button" class="image-map-box" :class="{ selected: selected === 'image' }" :style="{ left: document.imageSlot.box.x / document.canvas.width * 100 + '%', top: document.imageSlot.box.y / document.canvas.height * 100 + '%', width: document.imageSlot.box.width / document.canvas.width * 100 + '%', height: document.imageSlot.box.height / document.canvas.height * 100 + '%' }" @click="selected = 'image'">图片</button></div><p class="field-help">上方为区域示意，实际文字换行以生成预览为准。无图片时仍使用模板的文字区域。</p>
<div v-if="currentField" class="layout-values"><label>字号<input v-model.number="currentField.fontSize" type="number" min="12" max="200" /></label><label>最小字号<input v-model.number="currentField.minFontSize" type="number" min="12" :max="currentField.fontSize" /></label><label>字重<select v-model="currentField.fontWeight"><option :value="400">常规</option><option :value="700">加粗</option></select></label><label>文字颜色<input v-model="currentField.color" type="color" /></label><label>对齐<select v-model="currentField.align"><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select></label></div><label v-else class="field-label">图片适配<select v-model="document.imageSlot.fit"><option value="cover">等比裁剪填充</option><option value="contain">等比完整留白</option></select></label><div v-if="box" class="layout-values"><label v-for="(label, key) in { x: 'X 坐标', y: 'Y 坐标', width: '区域宽度', height: '区域高度' }" :key="key">{{ label }}<input v-model.number="box[key]" type="number" :min="key === 'x' || key === 'y' ? 0 : 1" :max="key === 'x' || key === 'width' ? document.canvas.width : document.canvas.height" /></label></div><div class="inline-actions"><button type="button" class="button primary compact" :disabled="!changed" @click="save(false)">{{ saving ? '正在保存…' : '保存排版调整' }}</button><button type="button" class="text-button" :disabled="!customized && !changed" @click="save(true)">恢复模板排版</button></div></fieldset></details></template>
