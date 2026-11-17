<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ensureSession, errorText } from '../api/client'
import { workspace } from '../api/workspace'
import type { Schemas } from '../api/types'
const router = useRouter()
const items = ref<{ job: Schemas['Job']; read: boolean }[]>([]), loading = ref(true), error = ref(''), onlyUnread = ref(false)
const filtered = computed(() => items.value.filter(item => !onlyUnread.value || !item.read))
let timer: ReturnType<typeof setInterval> | undefined
async function load(silent = false) {
  if (!silent) loading.value = true
  error.value = ''
  try { await ensureSession(); items.value = (await workspace.notifications()).items }
  catch (cause) { error.value = errorText(cause) } finally { loading.value = false }
}
async function open(item: typeof items.value[number]) {
  error.value = ''
  try { await workspace.readNotification(item.job.id); item.read = true; await router.push('/create/' + item.job.taskId) }
  catch (cause) { error.value = errorText(cause) }
}
onMounted(() => { void load(); timer = setInterval(() => { if (!document.hidden) void load(true) }, 30000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>
<template><div class="page notifications-page"><div class="section-heading"><div><span class="eyebrow">让处理结果，及时被看见</span><h1>处理消息</h1><p class="muted">分析或生成结束后，结果会保留在这里。打开消息继续查看对应作品。</p></div></div><div class="label-row"><label class="consent-row"><input v-model="onlyUnread" type="checkbox" /><span>只看未读</span></label><button class="button outline compact" @click="load(false)">刷新消息</button></div><div v-if="error" class="error-card" role="alert">{{ error }}<button class="text-button" @click="load(false)">重新加载</button></div><div v-if="loading" class="empty-state"><span class="spinner" />正在读取消息…</div><div v-else-if="!filtered.length" class="empty-state"><h3>暂时没有{{ onlyUnread ? '未读' : '处理' }}消息</h3><p>完成一次分析或生成后，就会在这里收到结果记录。</p></div><div v-else class="notification-list"><button v-for="item in filtered" :key="item.job.id" :class="['notification-item', { unread: !item.read }]" @click="open(item)"><span class="notification-dot" /><span><strong>{{ item.job.kind === 'render' ? '作品生成' : '内容分析' }}{{ item.job.status === 'succeeded' ? '已完成' : item.job.status === 'failed' ? '未能完成' : '已有新版本' }}</strong><small>{{ item.job.error?.message || (item.job.status === 'succeeded' ? '打开作品，查看处理结果。' : '草稿已保留，打开作品查看详情。') }}</small><small>{{ new Date(item.job.finishedAt || item.job.createdAt).toLocaleString('zh-CN') }} · 版本 {{ item.job.revision }}</small></span><span class="field-help">{{ item.read ? '已读' : '未读' }} →</span></button></div></div></template>
