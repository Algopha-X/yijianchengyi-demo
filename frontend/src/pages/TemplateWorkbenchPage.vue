<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api, ApiError, ensureSession, errorText } from '../api/client'
import { fieldKeys, fieldLabels } from '../api/types'
import type { FieldKey, Fields, Template, TemplateSummary } from '../api/types'
import { workspace } from '../api/workspace'
import type { Identity } from '../api/workspace'
import { workbench } from '../api/workbench'
import type { Grid, GridTarget, Publication, TemplateDraft } from '../api/workbench'

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const sources = ref<TemplateSummary[]>([])
const drafts = ref<TemplateDraft[]>([])
const publications = ref<Publication[]>([])
const identity = ref<Identity | null>(null)
const queue = ref<TemplateDraft[]>([])
const sourceId = ref('')
const newName = ref('我的新模板')
const active = ref<TemplateDraft | null>(null)
const edited = ref<Template | null>(null)
const field = ref<FieldKey>('title')
const useGrid = ref(false)
const layout = ref<Grid>(defaultGrid('poster'))
const loading = ref(true)
const pending = ref('')
const error = ref('')
const message = ref('')
const conflict = ref(false)
const previewUrl = ref('')
const reviewUrl = ref('')
const reviewing = ref('')
const reviewComments = ref<Record<string, string>>({})
const assetId = ref('')
const sample = ref<Fields>({ title: '校园读书会', subtitle: '把一本好书，读成一段相遇', body: '带上一本喜欢的书，分享你的阅读故事。', time: '周六下午', location: '图书馆', organizer: '校园阅读社' })
const reviewer = computed(() => !!identity.value?.account && ['admin', 'reviewer'].includes(identity.value.account.role))
const editable = computed(() => !!active.value && ['draft', 'rejected'].includes(active.value.status))
const dirty = computed(() => !!active.value && !!edited.value && (JSON.stringify(edited.value) !== JSON.stringify(active.value.document)
  || JSON.stringify(useGrid.value ? layout.value : null) !== JSON.stringify(active.value.grid)))
const statusLabels = { draft: '编辑中', pending: '待审核', rejected: '已退回', approved: '已发布' }
const targetLabels: Record<GridTarget, string> = { ...fieldLabels, image: '图片' }
const targets: GridTarget[] = [...fieldKeys, 'image']

function defaultGrid(scenario: string): Grid {
  if (scenario === 'article_image') return { columns: 3, rows: 10, margin: 40, gap: 10, placements: [
    { target: 'title', column: 0, row: 0, columnSpan: 3, rowSpan: 3 },
    { target: 'subtitle', column: 0, row: 3, columnSpan: 3, rowSpan: 1 },
    { target: 'body', column: 0, row: 4, columnSpan: 2, rowSpan: 3 },
    { target: 'image', column: 2, row: 4, columnSpan: 1, rowSpan: 3 },
    { target: 'time', column: 0, row: 8, columnSpan: 1, rowSpan: 1 },
    { target: 'location', column: 1, row: 8, columnSpan: 1, rowSpan: 1 },
    { target: 'organizer', column: 2, row: 8, columnSpan: 1, rowSpan: 1 },
  ] }
  return { columns: 2, rows: 8, margin: 60, gap: 12, placements: [
    { target: 'title', column: 0, row: 0, columnSpan: 2, rowSpan: 1 },
    { target: 'subtitle', column: 0, row: 1, columnSpan: 2, rowSpan: 1 },
    { target: 'body', column: 0, row: 2, columnSpan: 2, rowSpan: 2 },
    { target: 'image', column: 0, row: 4, columnSpan: 2, rowSpan: 2 },
    { target: 'time', column: 0, row: 6, columnSpan: 1, rowSpan: 1 },
    { target: 'location', column: 1, row: 6, columnSpan: 1, rowSpan: 1 },
    { target: 'organizer', column: 0, row: 7, columnSpan: 2, rowSpan: 1 },
  ] }
}
function revokePreview() { if (previewUrl.value) URL.revokeObjectURL(previewUrl.value); previewUrl.value = '' }
function revokeReview() { if (reviewUrl.value) URL.revokeObjectURL(reviewUrl.value); reviewUrl.value = '' }
function accept(value: TemplateDraft) {
  active.value = value; edited.value = clone(value.document); useGrid.value = !!value.grid
  layout.value = clone(value.grid || defaultGrid(value.document.scenario)); conflict.value = false; revokePreview()
  drafts.value = [value, ...drafts.value.filter(item => item.id !== value.id)]
}
function fail(cause: unknown) {
  error.value = errorText(cause)
  if (cause instanceof ApiError && cause.code === 'TEMPLATE_VERSION_CONFLICT') conflict.value = true
  if (cause instanceof ApiError && Array.isArray(cause.details.fields)) {
    const labels = cause.details.fields.map(item => {
      const value = item as { field?: string }
      return fieldLabels[value.field as FieldKey] || value.field
    }).filter(Boolean)
    if (labels.length) error.value += `（${labels.join('、')}）`
  }
}
async function action(name: string, operation: () => Promise<void>) {
  if (pending.value) return
  pending.value = name; error.value = ''; message.value = ''
  try { await ensureSession(); await operation() }
  catch (cause) { fail(cause) }
  finally { pending.value = '' }
}
async function load() {
  loading.value = true; error.value = ''
  try {
    await ensureSession()
    const [catalog, saved, published, me] = await Promise.all([api.templates(), workbench.drafts(), workbench.published(), workspace.me()])
    sources.value = catalog.items; drafts.value = saved.items; publications.value = published.items; identity.value = me
    if (!sourceId.value) sourceId.value = catalog.items[0]?.id || ''
    queue.value = reviewer.value ? (await workbench.queue()).items : []
  } catch (cause) { fail(cause) }
  finally { loading.value = false }
}
async function create() {
  await action('create', async () => { accept(await workbench.create(sourceId.value, newName.value.trim())); message.value = '已创建私有草稿，可开始修改。' })
}
async function selectDraft(item: TemplateDraft) {
  if (dirty.value) return
  await action('open', async () => { accept(await workbench.draft(item.id)); assetId.value = '' })
}
async function saveDraft() {
  if (!active.value || !edited.value) return null
  const saved = await workbench.save(active.value.id, active.value.version, edited.value, useGrid.value ? layout.value : null)
  accept(saved); return saved
}
async function save() { await action('save', async () => { await saveDraft(); message.value = '模板已保存。' }) }
async function reloadDraft() {
  if (!active.value) return
  await action('reload', async () => { accept(await workbench.draft(active.value!.id)); message.value = '已载入服务器最新版本。' })
}
async function preview() {
  await action('preview', async () => {
    const draft = dirty.value ? await saveDraft() : active.value
    if (!draft) return
    revokePreview(); previewUrl.value = await workbench.preview(draft, sample.value, assetId.value || undefined)
    message.value = '已由服务端生成实际预览。修改后请重新预览。'
  })
}
async function submit() {
  await action('submit', async () => {
    const draft = dirty.value ? await saveDraft() : active.value
    if (!draft) return
    accept(await workbench.submit(draft)); message.value = '已提交审核，审核期间该版本将锁定。'
    if (reviewer.value) queue.value = (await workbench.queue()).items
  })
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]; if (!file) return
  await action('upload', async () => { const asset = await api.upload(file); assetId.value = asset.id; revokePreview(); message.value = '试填图片已上传。' })
  input.value = ''
}
async function reviewPreview(item: TemplateDraft) {
  await action('review-preview', async () => { revokeReview(); reviewing.value = item.id; reviewUrl.value = await workbench.reviewPreview(item) })
}
async function review(item: TemplateDraft, decision: 'approve' | 'reject') {
  await action('review', async () => {
    const updated = await workbench.review(item, decision, reviewComments.value[item.id] || '')
    queue.value = queue.value.filter(value => value.id !== item.id)
    if (active.value?.id === item.id) accept(updated)
    publications.value = (await workbench.published()).items
    message.value = decision === 'approve' ? '审核已通过，模板已不可变发布。' : '已退回作者修改。'
    revokeReview(); reviewing.value = ''
  })
}
const boxes = computed(() => {
  if (!edited.value) return []
  const canvas = edited.value.canvas
  return targets.map(target => {
    let box = target === 'image' ? edited.value!.imageSlot.box : edited.value!.fields[target].box
    if (useGrid.value) {
      const placement = layout.value.placements.find(value => value.target === target)
      if (placement) {
        const grid = layout.value
        const width = Math.floor((canvas.width - grid.margin * 2 - (grid.columns - 1) * grid.gap) / Math.max(1, grid.columns))
        const height = Math.floor((canvas.height - grid.margin * 2 - (grid.rows - 1) * grid.gap) / Math.max(1, grid.rows))
        box = { x: grid.margin + placement.column * (width + grid.gap), y: grid.margin + placement.row * (height + grid.gap), width: placement.columnSpan * width + (placement.columnSpan - 1) * grid.gap, height: placement.rowSpan * height + (placement.rowSpan - 1) * grid.gap }
      }
    }
    return { target, style: { left: box.x / canvas.width * 100 + '%', top: box.y / canvas.height * 100 + '%', width: Math.max(0, box.width) / canvas.width * 100 + '%', height: Math.max(0, box.height) / canvas.height * 100 + '%' } }
  })
})
watch([edited, layout, useGrid, sample], revokePreview, { deep: true })
onMounted(load)
onBeforeUnmount(() => { revokePreview(); revokeReview() })
</script>

<template>
  <div class="page workbench-page">
    <div class="section-heading"><div><span class="eyebrow">将版式变成可复用的创作工具</span><h1>模板工作台</h1><p class="muted">从已有模板开始，编辑文字规则与网格布局，试填后交由其他审核员发布。</p></div><button class="button outline" :disabled="loading || !!pending" @click="load">刷新列表</button></div>
    <div v-if="error" class="error-card" role="alert"><p>{{ error }}</p><button v-if="conflict" class="button outline" :disabled="!!pending" @click="reloadDraft">放弃本页修改，载入最新模板</button><button v-else class="button outline" :disabled="!!pending" @click="load">重新连接与加载</button></div>
    <p v-if="message" class="workbench-message" role="status">{{ message }}</p>
    <div v-if="loading" class="empty-state"><span class="spinner" /><p>正在读取模板工作台…</p></div>
    <div v-else class="workbench-layout">
      <aside class="workbench-sidebar">
        <section class="workbench-card"><h2>新建模板</h2><label>起始模板<select v-model="sourceId" :disabled="!!pending || dirty"><option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}</option></select></label><label>模板名称<input v-model="newName" maxlength="60" :disabled="!!pending || dirty" /></label><button class="button primary" :disabled="!!pending || dirty || !sourceId || !newName.trim()" @click="create">创建私有草稿</button><p v-if="dirty" class="muted">请先保存当前修改，再新建或切换模板。</p></section>
        <section class="workbench-card"><h2>我的模板草稿</h2><p v-if="!drafts.length" class="muted">还没有模板草稿，从上方选择起始模板。</p><button v-for="item in drafts" :key="item.id" class="draft-item" :class="{ selected: active?.id === item.id }" :disabled="!!pending || dirty" @click="selectDraft(item)"><strong>{{ item.document.name }}</strong><small>{{ statusLabels[item.status] }} · 版本 {{ item.version }}</small></button></section>
      </aside>
      <section v-if="active && edited" class="workbench-editor workbench-card">
        <div class="workbench-editor-title"><div><h2>{{ active.document.name }}</h2><span class="muted">{{ statusLabels[active.status] }} · 版本 {{ active.version }} · {{ dirty ? '有未保存修改' : '已保存' }}</span></div><button class="button primary" :disabled="!editable || !!pending || !dirty" @click="save">{{ pending === 'save' ? '正在保存…' : '保存模板' }}</button></div>
        <p v-if="active.reviewComment" class="review-note">审核意见：{{ active.reviewComment }}</p><p v-if="!editable" class="review-note">当前版本已锁定。需要调整时，可在左侧创建新草稿。</p>
        <fieldset :disabled="!editable || !!pending" class="workbench-settings">
          <legend>模板外观</legend><div class="workbench-controls"><label>模板名称<input v-model="edited.name" maxlength="60" /></label><label>背景颜色<input v-model="edited.canvas.background" type="color" /></label><label>图片适配<select v-model="edited.imageSlot.fit"><option value="cover">等比裁剪</option><option value="contain">等比留白</option></select></label></div>
          <div class="workbench-field-tabs"><button v-for="key in fieldKeys" :key="key" type="button" :class="{ selected: field === key }" @click="field = key">{{ fieldLabels[key] }}</button></div>
          <div class="workbench-controls"><label>最大字数<input v-model.number="edited.fields[field].maxChars" type="number" min="1" max="5000" /></label><label>最多行数<input v-model.number="edited.fields[field].maxLines" type="number" min="1" max="50" /></label><label>字号<input v-model.number="edited.fields[field].fontSize" type="number" min="12" max="200" /></label><label>最小字号<input v-model.number="edited.fields[field].minFontSize" type="number" min="12" max="200" /></label><label>文字颜色<input v-model="edited.fields[field].color" type="color" /></label><label>文字对齐<select v-model="edited.fields[field].align"><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select></label><label>字重<select v-model.number="edited.fields[field].fontWeight"><option :value="400">常规</option><option :value="700">加粗</option></select></label><label>行距<input v-model.number="edited.fields[field].lineHeight" type="number" min="1" max="2.5" step="0.1" /></label></div>
          <div v-if="!useGrid" class="workbench-controls"><label v-for="axis in (['x', 'y', 'width', 'height'] as const)" :key="axis">{{ { x: '横坐标', y: '纵坐标', width: '区域宽度', height: '区域高度' }[axis] }}<input v-model.number="edited.fields[field].box[axis]" type="number" :min="axis === 'width' || axis === 'height' ? 1 : 0" /></label></div>
          <label class="workbench-checkbox"><input v-model="useGrid" type="checkbox" />使用网格布局与合并单元格</label>
          <div v-if="useGrid" class="grid-editor"><div class="workbench-controls"><label>列数<input v-model.number="layout.columns" type="number" min="1" max="12" /></label><label>行数<input v-model.number="layout.rows" type="number" min="1" max="16" /></label><label>边距<input v-model.number="layout.margin" type="number" min="0" max="200" /></label><label>间距<input v-model.number="layout.gap" type="number" min="0" max="100" /></label></div><div class="grid-table-wrap"><table class="grid-table"><thead><tr><th>区域</th><th>起始列</th><th>起始行</th><th>合并列数</th><th>合并行数</th></tr></thead><tbody><tr v-for="placement in layout.placements" :key="placement.target"><th>{{ targetLabels[placement.target] }}</th><td><input v-model.number="placement.column" type="number" min="0" :max="layout.columns - 1" :aria-label="targetLabels[placement.target] + '起始列'" /></td><td><input v-model.number="placement.row" type="number" min="0" :max="layout.rows - 1" :aria-label="targetLabels[placement.target] + '起始行'" /></td><td><input v-model.number="placement.columnSpan" type="number" min="1" :max="layout.columns" :aria-label="targetLabels[placement.target] + '合并列数'" /></td><td><input v-model.number="placement.rowSpan" type="number" min="1" :max="layout.rows" :aria-label="targetLabels[placement.target] + '合并行数'" /></td></tr></tbody></table></div><p class="muted">起始行列从 0 计数。服务端会检查重叠、越界和文字溢出；当前支持单层网格，不支持嵌套网格。</p></div>
        </fieldset>
        <div class="workbench-preview-grid"><div><h3>结构示意</h3><div class="layout-diagram" :style="{ aspectRatio: `${edited.canvas.width}/${edited.canvas.height}`, background: edited.canvas.background }"><div v-for="box in boxes" :key="box.target" class="diagram-box" :class="{ image: box.target === 'image' }" :style="box.style">{{ targetLabels[box.target] }}</div></div><p class="muted">示意图用于检查区域关系。实际文字与图片请查看服务端预览。</p></div><div><h3>服务端实际预览</h3><img v-if="previewUrl" :src="previewUrl" class="workbench-preview" alt="服务端实际生成的模板预览" /><div v-else class="workbench-preview-empty">{{ pending === 'preview' ? '正在排版生成预览…' : '填写试填内容，点击下方生成预览。' }}</div></div></div>
        <details class="workbench-sample" open><summary>试填内容</summary><div class="workbench-sample-fields"><label v-for="key in fieldKeys" :key="key">{{ fieldLabels[key] }}<textarea v-if="key === 'body'" v-model="sample[key]" rows="3" :disabled="!!pending" /><input v-else v-model="sample[key]" :disabled="!!pending" /></label></div><label class="button outline compact" for="workbench-image-upload">{{ assetId ? "替换试填图片" : "上传试填图片（可选）" }}</label><input id="workbench-image-upload" class="file-input" type="file" accept="image/jpeg,image/png" :disabled="!!pending" @change="upload" /><p v-if="assetId" class="muted">已上传试填图片，仅接收 JPG / PNG。</p><button v-if="assetId" class="text-button" :disabled="!!pending" @click="assetId = ''; revokePreview()">移除试填图片</button></details>
        <div class="workbench-actions"><button class="button outline" :disabled="!!pending" @click="preview">{{ pending === 'preview' ? '正在生成预览…' : dirty ? '保存并生成实际预览' : '生成实际预览' }}</button><button class="button primary" :disabled="!editable || !!pending || !identity?.account" @click="submit">保存并提交审核</button></div><p v-if="!identity?.account" class="muted">匿名会话可编辑和试填；<RouterLink to="/account">登录账号</RouterLink>后可提交审核。发布由其他审核员完成。</p>
      </section>
      <section v-else class="workbench-card workbench-welcome"><h2>从一张模板，开始自己的版式</h2><p>选择起始模板并创建草稿，或继续编辑左侧已有草稿。原始系统模板会保留，修改只影响你的私有草稿。</p></section>
    </div>
    <section v-if="reviewer" class="workbench-card workbench-review"><h2>待审模板 <span class="muted">{{ queue.length }} 份</span></h2><p v-if="!queue.length" class="muted">暂无待审模板。</p><article v-for="item in queue" :key="item.id" class="review-row"><div><h3>{{ item.document.name }}</h3><p class="muted">{{ item.document.scenario === 'poster' ? '海报' : '公众号配图' }} · 待审版本 {{ item.version }}</p><button class="button outline compact" :disabled="!!pending" @click="reviewPreview(item)">生成审核预览</button><img v-if="reviewing === item.id && reviewUrl" :src="reviewUrl" class="review-preview" alt="待审模板的服务端预览" /></div><div><label>审核意见（退回时必填）<textarea v-model="reviewComments[item.id]" maxlength="500" rows="3" :disabled="!!pending" /></label><div class="workbench-actions"><button class="button primary" :disabled="!!pending" @click="review(item, 'approve')">批准并发布</button><button class="button outline" :disabled="!!pending || !reviewComments[item.id]?.trim()" @click="review(item, 'reject')">退回修改</button></div><p class="muted">服务器限制作者自审，批准时会再次校验并生成缩略图。</p></div></article></section>
    <section class="workbench-card workbench-published"><h2>已发布的社区模板</h2><p v-if="!publications.length" class="muted">暂无已审核发布的模板。</p><div class="published-grid"><article v-for="item in publications" :key="item.id"><img :src="item.thumbnailUrl" :alt="item.name" /><h3>{{ item.name }}</h3><p class="muted">{{ item.scenario === 'poster' ? '海报' : '公众号配图' }} · 已审核发布</p><details><summary>查看模板编号</summary><code>{{ item.id }}</code></details></article></div><p class="muted">发布版本不可覆盖。创作页可选择已发布模板，并沿用内容分析、事实确认和真实排版流程。</p></section>
  </div>
</template>

<style scoped>
.workbench-page{padding-top:38px}.workbench-page h1{margin:14px 0}.workbench-page h2{font-size:21px}.workbench-page h3{font-size:15px}.workbench-layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:24px;align-items:start}.workbench-card{background:#fff;border:1px solid var(--line);border-radius:13px;padding:24px;margin-bottom:20px;min-width:0}.workbench-card label{display:block;font-size:12px;color:#5c6b60;margin:12px 0}.workbench-card input:not([type=checkbox]),.workbench-card select,.workbench-card textarea{display:block;width:100%;padding:9px 10px;border:1px solid #d9e0d5;border-radius:6px;background:#fff;color:#263b30;margin-top:7px;min-width:0}.workbench-card input[type=color]{height:39px;padding:4px}.draft-item{display:block;text-align:left;width:100%;border:1px solid #e0e6dc;padding:12px;border-radius:7px;margin-top:9px}.draft-item strong{display:block;font-size:13px}.draft-item small{display:block;margin-top:6px;color:#7e8c7e;font-size:11px}.draft-item.selected{background:#edf4e8;border-color:#91a881}.workbench-editor-title{display:flex;justify-content:space-between;align-items:center;gap:18px;padding-bottom:20px}.workbench-editor-title h2{margin-bottom:8px}.workbench-settings{padding:18px 0 0;border:0;border-top:1px solid #e3e7df;margin:0}.workbench-settings legend{font-size:13px;padding:0 12px 0 0}.workbench-controls{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0 14px}.workbench-controls label{min-width:0}.workbench-field-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:15px 0}.workbench-field-tabs button{font-size:12px;padding:8px 14px;background:#f4f6f0;border-radius:6px}.workbench-field-tabs button.selected{background:#234b3e;color:#fff}.workbench-card .workbench-checkbox{display:flex;align-items:center;gap:9px;font-size:13px;margin:23px 0 10px}.grid-table-wrap{overflow-x:auto}.grid-table{border-collapse:collapse;width:100%;font-size:12px;min-width:450px}.grid-table th,.grid-table td{padding:6px 8px;border-bottom:1px solid #e6eadf;text-align:left}.grid-table th{font-weight:500;color:#5f705f}.grid-table td input{margin:0!important;padding:6px!important}.workbench-preview-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin:25px 0}.layout-diagram{position:relative;width:100%;max-height:490px;border:1px solid #d5ddcf;overflow:hidden}.diagram-box{position:absolute;border:1px dashed #2e694b;background:#b5d4aa40;color:#2c513a;display:flex;align-items:center;justify-content:center;font-size:11px;overflow:hidden}.diagram-box.image{background:#deb16e50;border-color:#ac7a3a;color:#886029}.workbench-preview{display:block;width:100%;max-height:490px;object-fit:contain;background:#f2f3ee}.workbench-preview-empty{border:1px dashed #cdd8c6;background:#f7f9f3;padding:50px 22px;font-size:12px;color:#83917d;line-height:1.8;min-height:180px;display:flex;align-items:center;justify-content:center}.workbench-sample{border-top:1px solid #e4e9de;padding-top:20px}.workbench-sample summary{cursor:pointer;font-size:14px}.workbench-sample-fields{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}.workbench-sample-fields label:has(textarea){grid-column:1/-1}.workbench-actions{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0 10px}.workbench-message,.review-note{border-radius:8px;padding:12px 16px;background:#edf4e6;font-size:13px;color:#49613e}.review-note{background:#fff7e7;color:#826239}.workbench-welcome{padding:40px;line-height:1.8}.workbench-review,.workbench-published{margin-top:24px}.review-row{display:grid;grid-template-columns:1fr 1fr;gap:28px;border-top:1px solid #e4e8de;padding:24px 0}.review-preview{display:block;max-height:400px;max-width:100%;margin-top:15px}.published-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px}.published-grid article{border:1px solid #e6eadd;border-radius:9px;padding:14px;min-width:0}.published-grid img{width:100%;height:220px;object-fit:contain;background:#f2f4ec}.published-grid h3{margin:14px 0 8px}.published-grid summary{font-size:11px;cursor:pointer;color:#64755e}.published-grid code{font-size:10px;overflow-wrap:anywhere;display:block;margin-top:10px}.workbench-page .muted{line-height:1.8;font-size:12px}.workbench-page a{text-decoration:underline}.workbench-editor fieldset:disabled{opacity:.65}.workbench-sidebar .button{width:100%;margin-top:12px}@media(max-width:1050px){.workbench-layout{grid-template-columns:220px minmax(0,1fr)}.workbench-card{padding:19px}.workbench-controls{grid-template-columns:1fr 1fr}.published-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:760px){.workbench-layout{grid-template-columns:1fr}.workbench-sidebar{display:grid;grid-template-columns:1fr 1fr;gap:14px}.workbench-preview-grid,.review-row{grid-template-columns:1fr 1fr}.published-grid{grid-template-columns:1fr 1fr}.workbench-page{padding-top:24px}.workbench-editor-title{flex-wrap:wrap}}@media(max-width:500px){.workbench-sidebar,.workbench-preview-grid,.review-row{grid-template-columns:1fr}.workbench-sample-fields{grid-template-columns:1fr}.published-grid{grid-template-columns:1fr 1fr}.published-grid img{height:150px}.workbench-actions .button{width:100%}}
.workbench-card input.file-input{display:none}
</style>
