<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api, ensureSession, errorText } from '../api/client'
import { workspace } from '../api/workspace'
import type { Material } from '../api/workspace'
const items = ref<Material[]>([]), scope = ref('private'), search = ref(''), onlyFavorites = ref(false)
const loading = ref(true), busy = ref(false), error = ref(''), message = ref(''), canReview = ref(false), reviewing = ref(false)
const editing = ref<Material | null>(null), cropping = ref<Material | null>(null)
const name = ref(''), tags = ref(''), requestPublic = ref(false), reviewNote = ref(''), prompt = ref(''), sourceId = ref('')
const crop = reactive({ x: 0, y: 0, width: 1, height: 1 })
const visibilityLabels = { private: '私有', pending: '待审核', public: '公共', rejected: '审核未通过' }
async function load() {
  loading.value = true; error.value = ''
  try {
    await ensureSession()
    const identity = await workspace.me()
    canReview.value = ['admin', 'reviewer'].includes(identity.account?.role || '')
    items.value = (reviewing.value ? await workspace.reviewQueue() : await workspace.materials(scope.value, search.value, onlyFavorites.value)).items
  } catch (cause) { error.value = errorText(cause) } finally { loading.value = false }
}
async function perform(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try { await ensureSession(); await action() } catch (cause) { error.value = errorText(cause) } finally { busy.value = false }
}
function upload(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]
  if (!file) return
  void perform(async () => {
    if (!['image/jpeg', 'image/png'].includes(file.type) || file.size > 10 * 1024 * 1024) throw new Error('请选择不超过 10 MiB 的 JPG 或 PNG 图片。')
    const asset = await api.upload(file)
    await workspace.metadata(asset.id, file.name.slice(0,80), [])
    await load(); message.value = '图片已上传到当前工作区。'
  }).finally(() => { input.value = '' })
}
function edit(item: Material) { editing.value = item; name.value = item.name; tags.value = item.tags.join('，'); requestPublic.value = false }
function saveMetadata() { return perform(async () => {
  if (!editing.value) return
  const labels = [...new Set(tags.value.split(/[,，]/).map(tag => tag.trim()).filter(Boolean))]
  if (labels.length > 10 || labels.some(tag => tag.length > 20)) throw new Error('最多 10 个标签，每个不超过 20 字。')
  await workspace.metadata(editing.value.id, name.value.trim(), labels, requestPublic.value)
  editing.value = null; await load(); message.value = requestPublic.value ? '已提交公共素材审核；通过前仍仅当前工作区可见。' : '素材信息已更新为私有。'
}) }
function favorite(item: Material) { return perform(async () => { await workspace.favorite(item.id, !item.favorite); await load() }) }
function importItem(item: Material) { return perform(async () => { await workspace.importMaterial(item.id); await load(); message.value = '已复制到我的素材，原公共素材不受影响。' }) }
function openCrop(item: Material) { cropping.value = item; Object.assign(crop, { x: 0, y: 0, width: item.width, height: item.height }) }
function saveCrop() { return perform(async () => { if (!cropping.value) return; await workspace.crop(cropping.value.id, { ...crop }); cropping.value = null; await load(); message.value = '裁剪结果已保存为新图片，原图保留。' }) }
function generate() { return perform(async () => { const result = await workspace.generate(prompt.value.trim(), sourceId.value || undefined); await load(); message.value = result.message + (result.mode === 'mock' ? ' 当前为 Mock 占位素材，并非真实 AI 生图。' : '') }) }
function review(item: Material, decision: 'approve' | 'reject') { return perform(async () => { if (decision === 'reject' && !reviewNote.value.trim()) throw new Error('退回素材时请填写审核理由。'); await workspace.review(item.id, decision, reviewNote.value, item.revision); await load(); message.value = decision === 'approve' ? '已通过公共素材审核。' : '已退回素材。'; reviewNote.value = '' }) }
onMounted(load)
</script>
<template><div class="page materials-page"><div class="section-heading history-heading"><div><span class="eyebrow">让素材，成为创作的一部分</span><h1>素材库</h1><p class="muted">保存自己的图片，收藏公共素材，按模板等比适配。</p></div><label class="button primary" for="library-upload">上传图片</label><input id="library-upload" class="file-input" type="file" accept="image/jpeg,image/png" :disabled="busy" @change="upload" /></div>
<div v-if="error" class="error-card" role="alert">{{ error }}<button class="text-button" :disabled="busy" @click="load">重新加载</button></div><div v-if="message" class="success-notice" role="status">{{ message }}</div>
<div class="material-layout"><section><form class="material-search" @submit.prevent="load"><select v-model="scope" :disabled="reviewing" @change="load"><option value="private">我的素材</option><option value="public">公共素材</option><option value="all">全部可用</option></select><input v-model="search" maxlength="100" placeholder="搜索名称或标签" aria-label="搜索素材" /><button class="button outline compact">搜索</button></form><div class="label-row material-filter"><label class="consent-row"><input v-model="onlyFavorites" type="checkbox" @change="load" /><span>只看收藏</span></label><button v-if="canReview" class="text-button" @click="reviewing = !reviewing; load()">{{ reviewing ? '返回素材库' : '公共素材审核' }}</button></div><label v-if="reviewing" class="field-label">审核备注<input v-model="reviewNote" maxlength="500" placeholder="不能审核自己的素材" /></label>
<div v-if="loading" class="empty-state"><span class="spinner" />正在读取素材…</div><div v-else-if="!items.length" class="empty-state"><h3>还没有符合条件的素材</h3><p>可上传 JPG / PNG，或切换到公共素材。单张最大 10 MiB。</p></div><div v-else class="material-grid"><article v-for="item in items" :key="item.id" class="material-card"><div class="material-thumb"><img :src="item.thumbnailUrl" :alt="item.name" loading="lazy" /><span v-if="item.generatedMode === 'mock'" class="pill">Mock 占位图</span></div><div class="material-copy"><div class="label-row"><h3>{{ item.name }}</h3><span class="material-visibility">{{ visibilityLabels[item.visibility] }}</span></div><p>{{ item.width }} × {{ item.height }} 像素 · {{ Math.round(item.bytes / 1024) }} KiB</p><div class="material-tags"><span v-for="tag in item.tags" :key="tag">{{ tag }}</span></div><p v-if="item.reviewNote">审核备注：{{ item.reviewNote }}</p><div class="material-actions"><button class="text-button" :disabled="busy" @click="favorite(item)">{{ item.favorite ? '取消收藏' : '收藏' }}</button><template v-if="item.owned"><button class="text-button" :disabled="busy" @click="edit(item)">编辑信息</button><button class="text-button" :disabled="busy" @click="openCrop(item)">裁剪</button></template><button v-else class="text-button" :disabled="busy" @click="importItem(item)">加入我的素材</button></div><div v-if="reviewing && !item.owned" class="inline-actions"><button class="button outline compact" :disabled="busy" @click="review(item, 'approve')">通过</button><button class="button outline compact" :disabled="busy" @click="review(item, 'reject')">退回</button></div></div></article></div></section>
<aside class="material-generate editor-panel"><span class="eyebrow">图片生成与变体</span><h2>准备一张新素材</h2><div class="development-notice"><strong>本机默认 Mock 生图</strong><p>描述生成抽象占位图；选择原图时生成简单颜色变体。真实接口未配置时会报错。</p></div><form @submit.prevent="generate"><label class="field-label" for="image-prompt">图片描述</label><textarea id="image-prompt" v-model="prompt" required maxlength="2000" rows="4" placeholder="描述主题或氛围" /><label class="field-label" for="source-material">参考原图（可选）</label><select id="source-material" v-model="sourceId"><option value="">不使用参考原图</option><option v-for="item in items.filter(value => value.owned)" :key="item.id" :value="item.id">{{ item.name }}</option></select><button class="button primary" :disabled="busy || !prompt.trim()">{{ busy ? '正在处理…' : '生成素材' }}</button></form><p class="field-help">生成结果进入私有素材库。在创作页点击“从素材库选择”使用。</p><RouterLink class="text-button" to="/create">去创作 →</RouterLink></aside></div>
<div v-if="editing" class="management-modal" role="dialog" aria-modal="true" aria-label="编辑素材信息"><form class="management-dialog" @submit.prevent="saveMetadata"><h2>编辑素材信息</h2><label class="field-label" for="material-name">名称</label><input id="material-name" v-model="name" required maxlength="80" /><label class="field-label" for="material-tags">标签</label><input id="material-tags" v-model="tags" placeholder="逗号分隔，最多 10 个" /><label class="consent-row"><input v-model="requestPublic" type="checkbox" /><span>提交公共素材审核。未勾选会设为私有；公共可见前需另一位审核员批准。</span></label><div class="inline-actions"><button class="button primary" :disabled="busy || !name.trim()">保存信息</button><button type="button" class="button outline" :disabled="busy" @click="editing = null">取消</button></div><p v-if="error" role="alert">{{ error }}</p></form></div>
<div v-if="cropping" class="management-modal" role="dialog" aria-modal="true" aria-label="裁剪图片"><form class="management-dialog crop-dialog" @submit.prevent="saveCrop"><h2>裁剪图片</h2><p class="field-help">按原图像素填写区域，原点在左上角。原图 {{ cropping.width }} × {{ cropping.height }}；保存生成新图片。</p><div class="crop-preview"><img :src="'/api/assets/' + cropping.id + '/file'" :alt="cropping.name" /><span :style="{ left: crop.x / cropping.width * 100 + '%', top: crop.y / cropping.height * 100 + '%', width: crop.width / cropping.width * 100 + '%', height: crop.height / cropping.height * 100 + '%' }" /></div><div class="crop-fields"><label v-for="(label, key) in { x: '左边距 X', y: '上边距 Y', width: '宽度', height: '高度' }" :key="key">{{ label }}<input v-model.number="crop[key]" type="number" required :min="key === 'x' || key === 'y' ? 0 : 1" :max="key === 'x' || key === 'width' ? cropping.width : cropping.height" /></label></div><div class="inline-actions"><button class="button primary" :disabled="busy">保存裁剪副本</button><button type="button" class="button outline" :disabled="busy" @click="cropping = null">取消</button></div><p v-if="error" role="alert">{{ error }}</p></form></div>
</div></template>
