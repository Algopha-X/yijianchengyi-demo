<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ensureSession, errorText } from '../api/client'
import { workspace } from '../api/workspace'
import type { Material } from '../api/workspace'
const emit = defineEmits<{ close: []; selected: [id: string] }>()
const items = ref<Material[]>([]), scope = ref('private'), search = ref(''), loading = ref(true), pending = ref(''), error = ref('')
async function load() {
  loading.value = true; error.value = ''
  try { await ensureSession(); items.value = (await workspace.materials(scope.value, search.value)).items }
  catch (cause) { error.value = errorText(cause) } finally { loading.value = false }
}
async function select(item: Material) {
  pending.value = item.id; error.value = ''
  try { const asset = item.owned ? item : await workspace.importMaterial(item.id); emit('selected', asset.id) }
  catch (cause) { error.value = errorText(cause) } finally { pending.value = '' }
}
onMounted(load)
</script>
<template><div class="management-modal" role="dialog" aria-modal="true" aria-label="从素材库选择图片"><section class="material-picker"><div class="label-row"><h2>从素材库选择图片</h2><button class="button outline compact" :disabled="!!pending" @click="emit('close')">关闭</button></div><p class="field-help">公共素材先复制到当前工作区，再用于作品。</p><form class="material-search" @submit.prevent="load"><select v-model="scope" @change="load"><option value="private">我的素材</option><option value="public">公共素材</option><option value="all">全部可用</option></select><input v-model="search" maxlength="100" placeholder="搜索名称或标签" aria-label="搜索素材" /><button class="button outline compact">搜索</button></form><div v-if="error" class="error-card" role="alert">{{ error }}<button class="text-button" @click="load">重试</button></div><div v-if="loading" class="empty-state"><span class="spinner" />正在读取素材…</div><div v-else-if="!items.length" class="empty-state">没有可用素材，请先在素材库上传图片。</div><div v-else class="material-picker-grid"><button v-for="item in items" :key="item.id" class="material-pick-card" :disabled="!!pending" @click="select(item)"><img :src="item.thumbnailUrl" :alt="item.name" loading="lazy" /><strong>{{ item.name }}</strong><small>{{ item.width }} × {{ item.height }} · {{ item.owned ? '我的素材' : '公共素材' }}</small><span v-if="item.generatedMode === 'mock'" class="recommend-badge">Mock 占位图</span></button></div></section></div></template>
