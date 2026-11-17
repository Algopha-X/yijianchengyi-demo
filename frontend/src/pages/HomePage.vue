<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import { api, ensureSession, errorText } from '../api/client'
import { phaseLabels } from '../api/types'
import type { Task, TemplateSummary } from '../api/types'
const recent = ref<Task[]>([])
const templates = ref<TemplateSummary[]>([])
const loading = ref(true)
const error = ref('')
async function load() {
  loading.value = true; error.value = ''
  try {
    await ensureSession()
    const [history, catalog] = await Promise.all([api.tasks(), api.templates()])
    recent.value = history.items.slice(0, 3); templates.value = catalog.items
  } catch (cause) { error.value = errorText(cause) }
  finally { loading.value = false }
}
onMounted(load)
</script>
<template>
  <div class="page home-page">
    <section class="hero">
      <div class="hero-copy"><span class="eyebrow"><span class="small-line" />给灵感，一个好看的开始</span><h1>把你的文字，<br>变成<span>值得分享</span>的作品。</h1><p>说清想法，补齐信息，确认重要事实。<br>从第一段文字到一张完整海报，让创作自然发生。</p><div class="hero-actions"><RouterLink class="button primary large" to="/create"><AppIcon name="plus" />开始创作<AppIcon name="arrow" :size="18" /></RouterLink><RouterLink class="button ghost" to="/history">查看我的作品</RouterLink></div><div class="hero-note"><AppIcon name="check" :size="16" />无需注册<span>·</span>支持中文排版<span>·</span>PNG / JPG 下载</div></div>
      <div class="hero-art" aria-label="海报排版示意"><span class="art-label"><span class="status-dot" />灵感正在成形</span><div class="floating-note"><AppIcon name="spark" :size="18" /><span>一点文字，无限可能</span></div><div class="sample-poster"><div class="poster-top">CAMPUS READING CLUB <span>01 / 秋日</span></div><div class="poster-stem" /><h2>秋日<br>读书会<span>READ &amp;<br>RECONNECT</span></h2><p>带上一本喜欢的书，<br>分享你的阅读故事。</p><div class="poster-orbit orbit-one" /><div class="poster-orbit orbit-two" /><div class="poster-bottom"><span>校园阅读社<br>图书馆一楼交流区</span><span>14:00<br>2026.11.07</span></div></div><div class="art-caption"><span>版式示意 · 清新校园海报</span><span>1080 × 1440</span></div></div>
    </section>
    <section class="how-section"><div class="section-intro"><span class="eyebrow">从想法到作品</span><h2>创作，简单三步。</h2></div><div class="how-grid"><article><span class="how-number">01</span><div><h3>写下你的想法</h3><p>输入活动文字，按需上传一张图片。<br>不知道怎么开始？试试内置示例。</p></div><AppIcon name="file" :size="26" /></article><article><span class="how-number">02</span><div><h3>确认内容与版式</h3><p>选择模板，补齐缺失信息。<br>重要事实由你确认，创作更安心。</p></div><AppIcon name="grid" :size="26" /></article><article><span class="how-number">03</span><div><h3>生成，然后分享</h3><p>查看真实图片预览，随时返回修改。<br>下载原尺寸 PNG 或 JPG 作品。</p></div><AppIcon name="download" :size="26" /></article></div></section>
    <section class="recent-section"><div class="section-heading"><div><span class="eyebrow">继续你的创作</span><h2>最近作品</h2></div><RouterLink class="text-button" to="/history">全部作品<AppIcon name="arrow" :size="16" /></RouterLink></div>
      <div v-if="loading" class="empty-inline"><span class="spinner" />正在读取你的作品…</div>
      <div v-else-if="error" class="error-card" role="alert"><p>{{ error }}</p><button class="button ghost" @click="load">重试</button></div>
      <div v-else-if="!recent.length" class="empty-inline"><AppIcon name="image" :size="28" /><div><strong>第一份作品，等你开始。</strong><p>完成的海报和保存的草稿都会出现在这里。</p></div><RouterLink class="button outline" to="/create">创建第一份作品</RouterLink></div>
      <div v-else class="work-grid"><RouterLink v-for="item in recent" :key="item.id" :to="`/create/${item.id}`" class="work-card"><div class="work-thumb"><img v-if="item.previewAssetId && item.previewRevision === item.revision" :src="`/api/assets/${item.previewAssetId}/file`" :alt="item.fields.title || '作品预览'" /><AppIcon v-else name="file" :size="32" /><span class="pill">{{ phaseLabels[item.phase] }}</span></div><div class="work-card-copy"><h3>{{ item.fields.title || '未命名创作' }}</h3><p>{{ new Date(item.updatedAt).toLocaleDateString('zh-CN') }}<span>继续编辑 →</span></p></div></RouterLink></div>
    </section>
    <div class="quiet-note">{{ templates.length ? `当前提供 ${templates.length} 款有效模板` : '模板以服务端有效目录为准' }}<span>·</span>默认 Mock 模式按标签提取文字，不调用真实大模型。</div>
  </div>
</template>
