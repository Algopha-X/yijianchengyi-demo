<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { useServiceStore } from './stores/service'
import AppIcon from './components/AppIcon.vue'
const service = useServiceStore()
onMounted(() => { void service.connect() })
</script>
<template>
  <div class="app-shell">
    <header class="site-header">
      <RouterLink to="/" class="brand" aria-label="一键成艺首页"><span class="brand-mark"><AppIcon name="spark" :size="23" /></span><span>一键成艺<small>文字与版式，恰好相遇</small></span></RouterLink>
      <nav aria-label="主要导航"><RouterLink to="/" exact-active-class="active">首页</RouterLink><RouterLink to="/create" active-class="active">开始创作</RouterLink><RouterLink to="/history" active-class="active">我的作品</RouterLink><RouterLink to="/materials" active-class="active">素材库</RouterLink><RouterLink to="/templates-workbench" active-class="active">模板工作台</RouterLink><RouterLink to="/notifications" active-class="active">消息</RouterLink><RouterLink to="/account" active-class="active">账号</RouterLink></nav>
      <div class="header-status"><span :class="['status-dot', { offline: !service.health || service.error }]" /><span>{{ service.error ? '服务连接异常' : service.health ? (service.health.mode === 'mock' ? 'Mock 演示模式' : '真实模型模式') : '正在连接服务' }}</span></div>
    </header>
    <div v-if="service.error" class="connection-banner" role="alert"><AppIcon name="warning" />{{ service.error }}<button class="text-button" @click="service.connect">重新连接</button></div>
    <main><RouterView /></main>
    <footer class="site-footer"><span>一键成艺 · G15 拼好队</span><span>用清晰的内容，完成一份好设计。</span><span>{{ service.health?.mode === 'real' ? '文字模型已配置 · 结果仍需确认' : '文字 Mock 演示 · 排版与导出由服务端执行' }}</span></footer>
  </div>
</template>
