<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ensureSession, errorText, invalidateSession } from '../api/client'
import { workspace } from '../api/workspace'
import type { Account } from '../api/workspace'
const props = defineProps<{ account: Account }>()
const emit = defineEmits<{ changed: [] }>()
const busy = ref(false), error = ref(''), message = ref(''), oldPassword = ref(''), newPassword = ref(''), confirmPassword = ref('')
const passwordConfirm = ref(false), users = ref<Account[]>([]), total = ref(0), offset = ref(0), editing = ref<Account | null>(null), role = ref('user'), banned = ref(false)
async function loadUsers() {
  if (props.account.role !== 'admin') return
  error.value = ''
  try { const result = await workspace.users(offset.value); users.value = result.items; total.value = result.total }
  catch (cause) { error.value = errorText(cause) }
}
function askPassword() { error.value = ''; if (newPassword.value !== confirmPassword.value) { error.value = '两次新密码输入不一致。'; return }; passwordConfirm.value = true }
async function changePassword() {
  busy.value = true; error.value = ''
  try {
    const result = await workspace.changePassword(oldPassword.value, newPassword.value)
    message.value = result.message
    oldPassword.value = ''; newPassword.value = ''; confirmPassword.value = ''; passwordConfirm.value = false
    invalidateSession(); await ensureSession(true); emit('changed')
  } catch (cause) { error.value = errorText(cause) } finally { busy.value = false }
}
async function saveUser() {
  if (!editing.value) return
  busy.value = true; error.value = ''
  try { await workspace.updateUser(editing.value.id, role.value, banned.value); editing.value = null; message.value = '账号权限已更新。'; await loadUsers(); emit('changed') }
  catch (cause) { error.value = errorText(cause) } finally { busy.value = false }
}
onMounted(loadUsers)
</script>
<template><section class="security-panel"><h2>账号安全</h2><div v-if="error" class="error-card" role="alert">{{ error }}</div><div v-if="message" class="success-notice">{{ message }}</div><details><summary>修改密码</summary><form class="password-form" @submit.prevent="askPassword"><label>当前密码<input v-model="oldPassword" type="password" required minlength="8" maxlength="128" autocomplete="current-password" /></label><label>新密码<input v-model="newPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" /></label><label>再次输入新密码<input v-model="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" /></label><p class="field-help">修改成功后，其他已登录会话会失效；当前会话会安全续期。</p><button class="button outline" :disabled="busy">修改密码</button></form></details><details v-if="account.role === 'admin'" class="user-management"><summary>成员权限管理（{{ total }}）</summary><p class="field-help">角色与停用状态变更需要明确确认；服务端保护最后一位有效管理员。</p><button class="text-button" :disabled="busy" @click="loadUsers">刷新成员</button><div class="user-table"><table><thead><tr><th>账号</th><th>昵称</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="user in users" :key="user.id"><td>{{ user.address }}</td><td>{{ user.nickname }}</td><td>{{ user.role === 'admin' ? '管理员' : user.role === 'reviewer' ? '审核员' : '普通用户' }}</td><td>{{ user.banned ? '已停用' : '正常' }}</td><td><button class="text-button" :disabled="busy" @click="editing = user; role = user.role; banned = user.banned">管理权限</button></td></tr></tbody></table></div><div class="inline-actions"><button class="text-button" :disabled="offset === 0" @click="offset -= 50; loadUsers()">上一页</button><span class="field-help">{{ offset + 1 }} – {{ Math.min(offset + 50, total) }} / {{ total }}</span><button class="text-button" :disabled="offset + 50 >= total" @click="offset += 50; loadUsers()">下一页</button></div></details>
<div v-if="passwordConfirm" class="management-modal" role="dialog" aria-modal="true" aria-label="确认修改密码"><div class="management-dialog"><h2>确认修改密码？</h2><p>其他已登录会话将失效，当前会话会续期。请使用新密码进行后续登录。</p><div class="inline-actions"><button class="button primary" :disabled="busy" @click="changePassword">确认修改密码</button><button class="button outline" :disabled="busy" @click="passwordConfirm = false">取消</button></div></div></div>
<div v-if="editing" class="management-modal" role="dialog" aria-modal="true" aria-label="确认修改账号权限"><form class="management-dialog" @submit.prevent="saveUser"><h2>修改账号权限</h2><p>{{ editing.address }}（{{ editing.nickname }}）</p><label class="field-label">角色<select v-model="role"><option value="user">普通用户</option><option value="reviewer">审核员</option><option value="admin">管理员</option></select></label><label class="consent-row"><input v-model="banned" type="checkbox" /><span>停用此账号，撤销其登录会话</span></label><p class="field-help">确认后立即生效，请核对账号及权限。</p><div class="inline-actions"><button class="button primary" :disabled="busy">确认保存权限</button><button type="button" class="button outline" :disabled="busy" @click="editing = null">取消</button></div><p v-if="error" role="alert">{{ error }}</p></form></div></section></template>
