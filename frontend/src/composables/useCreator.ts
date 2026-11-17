import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { api, ApiError, ensureSession, errorText, getSessionScope } from '../api/client'
import { buildPatch, draftFromTask, emptyFields, stageLabels } from '../api/types'
import type { Draft, FieldKey, Fields, Job, Task, Template, TemplateSummary } from '../api/types'
import type { ExportFormat, ExportScale } from '../api/extensions'
import { createSaveQueue } from './saveQueue'
import { readDraft, readJob, removeDraft, removeJob, writeDraft, writeJob } from './draftStorage'

const completeText = '做一张清新的校园读书会海报。标题：秋日读书会。时间：2026年11月20日14:00。地点：图书馆一楼交流区。主办方：校园阅读社。正文：带上一本喜欢的书，分享你的阅读故事。'
const missingText = '做一张清新的校园读书会海报。标题：秋日读书会。正文：带上一本喜欢的书，分享你的阅读故事。'
const freshDraft = (): Draft => ({ rawText: '', scenario: 'poster', style: null, templateId: null, assetIds: [], fields: emptyFields() })
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
const processing = (value: Task) => value.phase === 'rendering' || !!value.activeJob

export function useCreator() {
  const router = useRouter()
  const route = useRoute()
  const task = ref<Task | null>(null)
  const draft = reactive<Draft>(freshDraft())
  const baseline = ref<Draft>(freshDraft())
  const templates = ref<TemplateSummary[]>([])
  const template = ref<Template | null>(null)
  const loading = ref(true)
  const operation = ref('')
  const error = ref('')
  const requestId = ref('')
  const conflict = ref(false)
  const sessionLost = ref(false)
  const crossSessionRecovery = ref(false)
  const localStorageError = ref(false)
  const saving = ref(false)
  const saveError = ref('')
  const recovered = ref<Draft | null>(null)
  const checked = ref<string[]>([])
  const job = ref<Job | null>(null)
  const restoringJob = ref(false)
  const waitLong = ref(false)
  const step = ref(1)
  const styleFilter = ref('all')
  const recommendedIds = ref<string[]>([])
  const beforeFields = ref<Fields | null>(null)
  const proposedFields = ref<Fields | null>(null)
  const exportScale = ref<ExportScale>(1)
  let scope = ''
  let hydrate = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let pollAbort = new AbortController()
  let disposed = false
  let templateSequence = 0
  let createRequest: Promise<void> | undefined
  let initialized = false
  let recoverySource: string | undefined
  const busy = computed(() => !!operation.value || loading.value || restoringJob.value)
  const dirty = computed(() => !equal(draft, baseline.value))
  const visibleTemplates = computed(() => templates.value.filter(item => item.scenario === draft.scenario &&
    (styleFilter.value === 'all' || item.style === styleFilter.value)).sort((a, b) =>
    Number(recommendedIds.value.includes(b.id)) - Number(recommendedIds.value.includes(a.id))))
  const currentPreview = computed(() => !!task.value?.previewAssetId && task.value.previewRevision === task.value.revision && !dirty.value && !job.value && !restoringJob.value)
  const canConfirm = computed(() => !!task.value?.analysis && !dirty.value && !sessionLost.value && !task.value.analysis.missingFields.length &&
    !!task.value.templateId && task.value.analysis.facts.every(fact => checked.value.includes(fact.id)))
  const saveLabel = computed(() => sessionLost.value ? '会话已失效，文字已保留' : conflict.value ? '版本冲突，请处理' :
    localStorageError.value ? '浏览器暂存失败，请及时保存' : saveError.value ? '服务保存失败，本地副本已保留' : saving.value ? '正在保存…' :
    dirty.value ? task.value ? '有修改待同步，本地已暂存' : '已暂存到此浏览器，尚未保存到服务' : task.value ? '已保存到本机服务' : '新作品尚未保存')
  const progressLabel = computed(() => job.value ? stageLabels[job.value.stage] : restoringJob.value ? '正在恢复任务状态' : '')
  const recoveryText = computed(() => recovered.value ? recovered.value.rawText + '\n\n' + Object.entries(recovered.value.fields).map(([key, value]) => key + ': ' + value).join('\n') : '')

  function persist() {
    if (scope) localStorageError.value = !writeDraft(scope, task.value?.id || null, clone(draft), task.value?.revision || null, beforeFields.value || undefined)
  }
  function showError(cause: unknown) {
    error.value = errorText(cause)
    requestId.value = cause instanceof ApiError ? cause.requestId : ''
    if (cause instanceof ApiError && cause.status === 401) {
      persist(); sessionLost.value = true; recovered.value = clone(draft)
      error.value = '会话已失效。当前文字已保留；重新连接后，可明确选择将文字复制到新会话。'
    }
    if (cause instanceof ApiError && cause.code === 'REVISION_CONFLICT') {
      conflict.value = true; recovered.value = clone(draft); persist()
      error.value = '这份作品已在其他页面更新。你的文字已保留，请复制内容或重新载入服务器版本。'
    }
  }
  async function detail(id: string | null) {
    const sequence = ++templateSequence
    if (!id) { template.value = null; return }
    const result = await api.template(id)
    if (sequence === templateSequence) template.value = result
  }
  function adopt(result: Task) {
    hydrate = true; task.value = result
    Object.assign(draft, draftFromTask(result)); baseline.value = draftFromTask(result)
    checked.value = result.confirmedRevision === result.revision ? result.analysis?.facts.map(fact => fact.id) || [] : []
    if (result.analysis) recommendedIds.value = [...result.analysis.recommendedTemplateIds]
    hydrate = false
  }
  async function saveNow() {
    if (!task.value || conflict.value || sessionLost.value) return
    if (!draft.rawText.trim()) throw new Error('请填写创作内容，再保存草稿。')
    const snapshot = clone(draft)
    const patch = buildPatch(snapshot, baseline.value, task.value.revision)
    if (!patch) return
    saving.value = true; saveError.value = ''
    try {
      const response = await api.patch(task.value.id, patch)
      task.value = response; baseline.value = snapshot; persist()
    } catch (cause) { saveError.value = errorText(cause); showError(cause); throw cause }
    finally { saving.value = false }
  }
  const queue = createSaveQueue(saveNow)
  async function flush() {
    if (timer) clearTimeout(timer)
    if (conflict.value) throw new Error('请先处理版本冲突。')
    if (sessionLost.value) throw new Error('请先重新连接会话，文字副本已保留。')
    await queue.flush()
  }
  watch(draft, () => {
    if (hydrate) return
    checked.value = []; persist(); queue.mark()
    if (timer) clearTimeout(timer)
    if (task.value && !conflict.value && !sessionLost.value) timer = setTimeout(() => { void flush().catch(() => {}) }, 800)
  }, { deep: true, flush: 'sync' })

  async function initialize(id?: string) {
    loading.value = true; error.value = ''; conflict.value = false; saveError.value = ''; recovered.value = null
    sessionLost.value = false; crossSessionRecovery.value = false
    recoverySource = undefined
    pollAbort.abort(); pollAbort = new AbortController(); job.value = null; restoringJob.value = false; operation.value = ''
    if (timer) clearTimeout(timer)
    beforeFields.value = null; proposedFields.value = null; recommendedIds.value = []
    try {
      await ensureSession(); scope = getSessionScope()
      templates.value = (await api.templates()).items
      if (id) {
        const result = await api.task(id)
        adopt(result); await detail(result.templateId)
        step.value = result.previewAssetId && result.previewRevision === result.revision ? 3 : result.analysis && result.templateId ? 2 : 1
        const local = readDraft(scope, id, baseline.value)
        const comparison = local || readDraft(scope, id)
        beforeFields.value = comparison?.beforeFields || null
        if (local) {
          if (!equal(local.draft, baseline.value)) { recovered.value = local.draft; recoverySource = local.storageKey }
        }
        if (result.analysis) proposedFields.value = clone(result.fields)
        const active = result.activeJob?.id || readJob(scope, id)
        if (active || processing(result)) void resume(active || undefined)
      } else {
        hydrate = true; task.value = null; Object.assign(draft, freshDraft()); baseline.value = freshDraft(); hydrate = false
        template.value = null; step.value = 1
        const local = readDraft(scope, null, freshDraft())
        if (local && (local.draft.rawText || Object.values(local.draft.fields).some(Boolean))) { recovered.value = local.draft; recoverySource = local.storageKey }
      }
    } catch (cause) { showError(cause) }
    finally { loading.value = false }
  }
  async function reconnect() {
    const retained = clone(draft)
    const oldScope = scope
    loading.value = true; error.value = ''
    try {
      await ensureSession(true)
      const nextScope = getSessionScope()
      if (oldScope && oldScope !== nextScope) {
        scope = nextScope; task.value = null; hydrate = true
        Object.assign(draft, freshDraft()); baseline.value = freshDraft(); hydrate = false
        template.value = null; checked.value = []; job.value = null; beforeFields.value = null; proposedFields.value = null
        recovered.value = retained; crossSessionRecovery.value = true; step.value = 1
        sessionLost.value = false; conflict.value = false; saveError.value = ''
        templates.value = (await api.templates()).items
        await router.replace({ name: 'Create' })
      } else await initialize(task.value?.id)
    } catch (cause) { showError(cause) }
    finally { loading.value = false }
  }
  async function ensureTask() {
    if (task.value) return
    if (!createRequest) createRequest = (async () => {
      if (!draft.rawText.trim()) throw new Error('先写下想法，或点击下方示例开始。')
      const snapshot = clone(draft)
      const created = await api.create({ scenario: snapshot.scenario, style: snapshot.style, rawText: snapshot.rawText, assetIds: snapshot.assetIds })
      task.value = created; baseline.value = draftFromTask(created)
      persist(); queue.mark(); await flush()
      removeDraft(scope, null)
      await router.replace({ name: 'Create', params: { taskId: created.id } })
    })().finally(() => { createRequest = undefined })
    await createRequest
  }
  async function perform(label: string, action: () => Promise<void>) {
    if (busy.value || sessionLost.value) return
    operation.value = label; error.value = ''; requestId.value = ''
    try { await action() } catch (cause) { showError(cause) }
    finally { operation.value = '' }
  }
  function sleep(signal: AbortSignal) {
    return new Promise<void>(resolve => {
      const cancel = () => { clearTimeout(handle); resolve() }
      const handle = window.setTimeout(() => { signal.removeEventListener('abort', cancel); resolve() }, document.hidden ? 3000 : 1000)
      signal.addEventListener('abort', cancel, { once: true })
    })
  }
  async function poll(id?: string) {
    const started = Date.now()
    const signal = pollAbort.signal
    const taskId = task.value?.id
    if (!taskId) return
    restoringJob.value = true
    try {
      while (!disposed && !signal.aborted && task.value?.id === taskId) {
        if (!id) {
          const refreshed = await api.task(taskId)
          if (signal.aborted) return
          if (refreshed.activeJob) { id = refreshed.activeJob.id; job.value = refreshed.activeJob; writeJob(scope, taskId, id) }
          else if (!processing(refreshed)) {
            adopt(refreshed); persist(); step.value = refreshed.previewAssetId ? 3 : refreshed.templateId && refreshed.analysis ? 2 : 1
            return
          }
        }
        if (id) {
          let result: Job
          try { result = await api.job(id, signal) }
          catch (cause) {
            if (cause instanceof ApiError && cause.status === 404) { removeJob(scope, taskId); id = undefined; job.value = null; continue }
            throw cause
          }
          if (signal.aborted) return
          job.value = result
          if (['succeeded', 'failed', 'superseded'].includes(result.status)) {
            const resultTask = await api.task(taskId)
            if (signal.aborted) return
            adopt(resultTask); persist()
            removeJob(scope, taskId); job.value = null
            if (result.status === 'failed') throw new ApiError(503, result.error?.code || 'JOB_FAILED', result.error?.message || '处理失败，草稿已保留，请重试。', result.error?.details)
            if (result.status === 'superseded') throw new Error('内容已有新版本，本次结果未应用。请确认最新内容后重试。')
            if (result.kind === 'analyze') proposedFields.value = clone(resultTask.fields)
            step.value = result.kind === 'render' ? 3 : resultTask.templateId ? 2 : 1
            return
          }
        }
        waitLong.value = Date.now() - started > 150000
        await sleep(signal)
      }
    } finally { if (!signal.aborted) restoringJob.value = false }
  }
  async function resume(id?: string) {
    const signal = pollAbort.signal
    operation.value = '正在恢复任务'
    try { await poll(id) } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === 'AbortError')) showError(cause)
    } finally { if (!signal.aborted) operation.value = '' }
  }
  async function run(kind: 'analyze' | 'render') {
    if (job.value || (task.value && processing(task.value))) { await poll(job.value?.id); return }
    await ensureTask(); await flush()
    const result = await api.start(task.value!.id, kind, task.value!.revision, crypto.randomUUID())
    writeJob(scope, task.value!.id, result.id)
    job.value = result; waitLong.value = false
    await poll(result.id)
  }
  function analyze() {
    return perform(draft.templateId ? '正在处理内容并检查信息' : '正在分析需求并推荐模板', async () => {
      beforeFields.value = clone(draft.fields); persist(); await run('analyze')
    })
  }
  function keepField(key: FieldKey, source: 'before' | 'proposed') {
    const value = source === 'before' ? beforeFields.value : proposedFields.value
    if (value) draft.fields[key] = value[key]
  }
  function confirmAndRender() {
    return perform('正在生成作品', async () => {
      await flush()
      if (!canConfirm.value) throw new Error('请先补齐信息、重新分析，并确认全部重要事实。')
      const result = await api.confirm(task.value!.id, { expectedRevision: task.value!.revision, confirmedFactIds: checked.value })
      adopt(result); await run('render')
    })
  }
  function save() { return perform('正在保存草稿', async () => { await ensureTask(); await flush() }) }
  function retryJob() { return perform('正在恢复任务', () => poll(job.value?.id)) }
  async function chooseTemplate(item: TemplateSummary) {
    draft.templateId = item.id; draft.scenario = item.scenario; draft.style = item.style
    try { await detail(item.id) } catch (cause) { showError(cause) }
  }
  function sample(complete: boolean) {
    if (task.value) return
    draft.rawText = complete ? completeText : missingText; draft.fields = emptyFields(); step.value = 1
  }
  function changeScenario(scenario: Task['scenario']) {
    if (draft.scenario === scenario) return
    draft.scenario = scenario; draft.templateId = null; draft.style = null; styleFilter.value = 'all'
    recommendedIds.value = []; template.value = null; templateSequence++
  }
  function restore() {
    if (!crossSessionRecovery.value && recoverySource) removeDraft(scope, task.value?.id || null, recoverySource)
    if (recovered.value) Object.assign(draft, clone(recovered.value), crossSessionRecovery.value ? { assetIds: [] } : {})
    recovered.value = null; crossSessionRecovery.value = false; step.value = 1; persist()
    void detail(draft.templateId).catch(showError)
  }
  function discardRecovery() { recovered.value = null; if (!crossSessionRecovery.value) removeDraft(scope, task.value?.id || null, recoverySource); recoverySource = undefined; crossSessionRecovery.value = false }
  function reloadServer() { persist(); return initialize(task.value?.id) }
  function upload(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    if (!file) return
    void perform('正在上传图片', async () => {
      if (!['image/jpeg', 'image/png'].includes(file.type)) throw new Error('请选择 JPG 或 PNG 图片。')
      if (file.size > 10 * 1024 * 1024) throw new Error('图片大小不能超过 10 MiB。')
      const asset = await api.upload(file)
      draft.assetIds = [asset.id]
      if (task.value) await flush()
    }).finally(() => { input.value = '' })
  }
  function removeImage() { draft.assetIds = [] }
  function download(format: ExportFormat) {
    return perform('正在准备下载', async () => {
      await flush()
      if (!currentPreview.value) throw new Error('请先生成当前版本的预览。')
      await api.download(task.value!.id, task.value!.revision, format, task.value!.fields.title, exportScale.value)
    })
  }
  function fieldRequired(key: FieldKey) {
    const meta = template.value?.fields[key]
    return meta && (meta.required || meta.requiredFor.includes(task.value?.analysis?.intentType || 'event'))
  }
  async function prepareLayout() { await flush(); if (!task.value) throw new Error('请先保存草稿。'); return task.value }
  function applyLayout(result: import('../api/extensions').LayoutResult) { adopt(result.task); template.value = result.document; persist(); step.value = 2 }
  const stop = watch(() => route.params.taskId, id => {
    const value = typeof id === 'string' ? id : undefined
    if (initialized && (task.value?.id || undefined) === value) return
    initialized = true; void initialize(value)
  }, { immediate: true })
  onBeforeRouteLeave(async () => {
    persist()
    if (task.value && dirty.value && !conflict.value && !sessionLost.value) { try { await flush() } catch { /* durable local draft retained */ } }
  })
  const leave = (event: BeforeUnloadEvent) => { persist(); if (dirty.value && localStorageError.value) { event.preventDefault(); event.returnValue = '' } }
  window.addEventListener('beforeunload', leave)
  onBeforeUnmount(() => { disposed = true; stop(); pollAbort.abort(); if (timer) clearTimeout(timer); window.removeEventListener('beforeunload', leave) })
  return { task, draft, templates, visibleTemplates, template, loading, busy, operation, error, requestId, conflict,
    saving, saveError, dirty, recovered, recoveryText, checked, job, waitLong, step, currentPreview, canConfirm,
    saveLabel, progressLabel, initialize, analyze, confirmAndRender, save, chooseTemplate, sample,
    restore, discardRecovery, reloadServer, upload, removeImage, download, fieldRequired, changeScenario, retryJob,
    sessionLost, reconnect, crossSessionRecovery, restoringJob, styleFilter, recommendedIds, beforeFields, proposedFields, keepField, exportScale, prepareLayout, applyLayout }
}
