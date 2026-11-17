<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import AccountSecurityPanel from '../components/AccountSecurityPanel.vue'
import OperationsPanel from '../components/OperationsPanel.vue'
import { ensureSession, errorText } from '../api/client'
import { workspace } from '../api/workspace'
import type { Identity, Verification } from '../api/workspace'
const identity = ref<Identity | null>(null)
const mode = ref<'login' | 'register' | 'reset'>('login')
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const message = ref('')
const address = ref('')
const password = ref('')
const nickname = ref('')
const code = ref('')
const verification = ref<Verification | null>(null)
const importAnonymous = ref(false)
const registrationConsent = ref(false)
const logoutConfirm = ref(false)
watch([address, mode], () => { verification.value = null; code.value = ''; error.value = '' })
async function load() {
  loading.value = true; error.value = ''
  try { await ensureSession(); identity.value = await workspace.me(); nickname.value = identity.value.account?.nickname || '' }
  catch (cause) { error.value = errorText(cause) }
  finally { loading.value = false }
}
async function perform(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try { await ensureSession(); await action() }
  catch (cause) { error.value = errorText(cause) }
  finally { busy.value = false }
}
function sendCode() {
  return perform(async () => { verification.value = await workspace.verification(address.value, mode.value === 'reset' ? 'reset' : 'register'); message.value = verification.value.message || '验证码已准备，请按下方提示完成验证。' })
}
function submit() {
  return perform(async () => {
    if (mode.value === 'login') {
      await workspace.login(address.value, password.value, importAnonymous.value)
      message.value = '登录成功。已切换到账号工作区，原身份的离线副本不会自动恢复到此账号。'
    } else {
      if (!verification.value) throw new Error('请先获取验证码。')
      const payload = { address: address.value, password: password.value, challengeId: verification.value.challengeId, code: code.value }
      if (mode.value === 'register') {
        if (!registrationConsent.value) throw new Error('请先确认将当前匿名工作区绑定到新账号。')
        await workspace.register({ ...payload, nickname: nickname.value.trim() })
        message.value = '注册成功，当前匿名工作区已绑定到新账号。'
      } else {
        const result = await workspace.reset(payload)
        message.value = result.message; mode.value = 'login'
      }
    }
    password.value = ''; code.value = ''; verification.value = null
    await load()
  })
}
function profile() { return perform(async () => { await workspace.profile(nickname.value.trim()); message.value = '昵称已更新。'; await load() }) }
function logout() { return perform(async () => { await workspace.logout(); logoutConfirm.value = false; message.value = '已退出并回到匿名工作区。账号作品仍保留在账号中。'; await load() }) }
onMounted(load)
</script>
<template>
  <div class="page account-page"><div class="section-heading"><div><span class="eyebrow">你的创作工作区</span><h1>账号与身份</h1><p class="muted">账号作品跨登录保留，匿名作品依赖当前浏览器会话。</p></div></div>
    <div v-if="error" class="error-card" role="alert"><p>{{ error }}</p><button class="text-button" :disabled="busy" @click="load">重新连接</button></div>
    <div v-if="message" class="success-notice" role="status">{{ message }}</div>
    <div v-if="loading" class="empty-state"><span class="spinner" />正在读取身份…</div>
    <div v-else-if="identity?.account" class="account-grid"><section class="editor-panel account-panel"><span class="eyebrow">当前账号</span><h2>{{ identity.account.nickname }}</h2><dl class="export-info"><div><dt>账号</dt><dd>{{ identity.account.address }}</dd></div><div><dt>角色</dt><dd>{{ identity.account.role === 'admin' ? '管理员' : identity.account.role === 'reviewer' ? '审核员' : '普通用户' }}</dd></div></dl><form @submit.prevent="profile"><label for="nickname-edit" class="field-label">昵称</label><input id="nickname-edit" v-model="nickname" required maxlength="40" /><button class="button outline" :disabled="busy || !nickname.trim()">保存昵称</button></form><div class="inline-actions"><RouterLink class="button primary" to="/history">查看账号作品</RouterLink><button class="text-button danger-text" :disabled="busy" @click="logoutConfirm = true">退出登录</button></div></section><aside class="account-help"><h3>身份切换与草稿</h3><p>离开创作页面时，未同步文字会保留在当前身份的浏览器副本中。账号切换后，不会自动把旧身份的离线文字写入新身份。</p><p>使用前请先保存重要文字。忘记密码时，可退出后使用“找回密码”。</p></aside></div>
    <div v-else class="account-grid"><section class="editor-panel account-panel"><div class="tabs account-tabs"><button :class="{ selected: mode === 'login' }" @click="mode = 'login'">登录</button><button :class="{ selected: mode === 'register' }" @click="mode = 'register'">注册</button><button :class="{ selected: mode === 'reset' }" @click="mode = 'reset'">找回密码</button></div>
      <div v-if="identity?.verificationMode === 'development'" class="development-notice"><strong>开发验证码模式</strong><p>验证码会直接显示在本页面，未发送真实邮件或短信；此方式仅供本地开发演示。</p></div>
      <form @submit.prevent="submit"><fieldset :disabled="busy"><label class="field-label" for="account-address">邮箱或手机号</label><input id="account-address" v-model="address" required maxlength="254" minlength="5" autocomplete="username" placeholder="输入邮箱或手机号" /><template v-if="mode === 'register'"><label class="field-label" for="account-nickname">昵称</label><input id="account-nickname" v-model="nickname" required maxlength="40" autocomplete="nickname" /></template><label class="field-label" for="account-password">{{ mode === 'reset' ? '新密码' : '密码' }}</label><input id="account-password" v-model="password" type="password" required minlength="8" maxlength="128" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" placeholder="8 至 128 位" />
      <template v-if="mode !== 'login'"><label class="field-label" for="account-code">验证码</label><div class="verification-input"><input id="account-code" v-model="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autocomplete="one-time-code" /><button type="button" class="button outline" :disabled="address.length < 5" @click="sendCode">获取验证码</button></div><div v-if="verification?.developmentCode" class="development-notice"><strong>本地开发验证码：<span data-testid="development-code">{{ verification.developmentCode }}</span></strong><p>有效 {{ Math.floor(verification.expiresIn / 60) }} 分钟。未发送真实邮件或短信。</p></div></template>
      <label v-if="mode === 'login'" class="consent-row"><input v-model="importAnonymous" type="checkbox" /><span>我同意将当前匿名工作区的 {{ identity?.anonymousTaskCount || 0 }} 份服务端作品及素材导入本账号。未勾选则在退出账号后回到原匿名作品；离线副本不自动导入。</span></label><label v-if="mode === 'register'" class="consent-row"><input v-model="registrationConsent" type="checkbox" required /><span>我同意将当前匿名工作区绑定到新账号（含已保存作品和素材）。</span></label><button class="button primary account-submit" :disabled="busy">{{ busy ? '正在处理…' : mode === 'login' ? '登录账号' : mode === 'register' ? '注册并绑定工作区' : '重置密码' }}</button></fieldset></form></section>
      <aside class="account-help"><h3>先创作，也可以稍后登录</h3><p>匿名模式可使用核心排版、草稿和素材功能。登录时可自行决定是否导入当前匿名作品。</p><p>注册会将当前匿名工作区绑定到新账号，请确认这台浏览器中的作品属于你。</p><RouterLink class="text-button" to="/create">返回创作 →</RouterLink></aside></div>
    <AccountSecurityPanel v-if="identity?.account" :account="identity.account" @changed="load" />
    <OperationsPanel v-if="identity?.account?.role === 'admin'" />
    <div v-if="logoutConfirm" class="management-modal" role="dialog" aria-modal="true" aria-label="确认退出登录"><div class="management-dialog"><h2>退出当前账号？</h2><p>账号作品仍会保留。退出后会进入新的匿名会话，请先确认重要修改已保存。</p><div class="inline-actions"><button class="button primary" :disabled="busy" @click="logout">确认退出</button><button class="button outline" :disabled="busy" @click="logoutConfirm = false">取消</button></div></div></div>
  </div>
</template>
