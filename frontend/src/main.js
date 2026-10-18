import { createApp, ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { createPinia, defineStore } from 'pinia'
import { createRouter, createWebHashHistory, useRoute, useRouter } from 'vue-router'
import { api, streamChat, zonedDateTime, sourceUrl } from './api.js'
import template from './template.js'
import './style.css'

const terminal = new Set(['succeeded', 'no_events', 'generation_failed', 'cancelled', 'system_failed', 'failed'])
const labels = { queued:'等待执行', running:'执行中', succeeded:'简报已生成', no_events:'没有符合条件的事件', generation_failed:'生成失败', system_failed:'系统失败', cancelled:'已取消', active:'已启用', paused:'已暂停', completed:'已完成', missed:'已错过', pending:'未发送', sending:'发送中', success:'已发送', failed:'失败', pending_confirmation:'发送结果待确认', collecting:'采集新闻', generating:'整理简报', indexing:'保存事件' }
const navigation = [{ path:'/', name:'home', title:'今日简报' }, { path:'/assistant', name:'assistant', title:'新闻策展' }, { path:'/briefings', name:'briefings', title:'我的简报' }, { path:'/tasks', name:'tasks', title:'定时任务' }, { path:'/history', name:'history', title:'历史检索' }, { path:'/settings', name:'settings', title:'账户与订阅' }]

/** 保存跨页面账号和任务摘要，不在本地保存密码或会话 Cookie。 */
const useWorkspace = defineStore('workspace', () => {
  const user = ref(null)
  const generationTasks = ref([])
  return { user, generationTasks }
})

/** 在指定时区显示时间，无效日期返回缺省状态。 */
function formatDate(value, zone) {
  if (!value) return '—'
  try { return new Intl.DateTimeFormat('zh-CN', { timeZone:zone || 'Asia/Shanghai', dateStyle:'medium', timeStyle:'short' }).format(new Date(value)) } catch { return '—' }
}

/** 将服务端时间转换为指定时区表单值。 */
function formDate(value, zone) {
  if (!value) return ''
  const fields = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone:zone, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23' }).formatToParts(new Date(value)).map(part => [part.type, part.value]))
  return `${fields.year}-${fields.month}-${fields.day}T${fields.hour}:${fields.minute}`
}

/** 将逗号或换行分隔的偏好转换为去重列表。 */
function splitList(text) { return [...new Set(text.split(/[,，\n]/).map(value => value.trim()).filter(Boolean))] }

/** 转义模型回复中的 HTML 特殊字符，确保 Markdown 渲染不会执行原始标签。 */
function escapeMarkdownHtml(value) {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

/** 将助手常用 Markdown 语法转换为安全的轻量 HTML，保留换行和列表层级。 */
function markdownToHtml(value) {
  const lines = escapeMarkdownHtml(value).split(/\r?\n/)
  const output = []; let list = ''
  /** 关闭当前列表容器，避免不同列表类型嵌套到同一层。 */
  const closeList = () => { if (list) { output.push(`</${list}>`); list = '' } }
  for (const line of lines) {
    const heading = line.match(/^(#{1,3})\s+(.+)$/); const bullet = line.match(/^\s*[-*]\s+(.+)$/); const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/)
    if (heading) { closeList(); output.push(`<h${heading[1].length}>${heading[2]}</h${heading[1].length}>`); continue }
    if (bullet || ordered) {
      const type = bullet ? 'ul' : 'ol'; if (list !== type) { closeList(); list = type; output.push(`<${type}>`) }
      output.push(`<li>${bullet ? bullet[1] : ordered[1]}</li>`); continue
    }
    closeList(); if (line.trim()) output.push(`<p>${line}</p>`)
  }
  closeList()
  return output.join('').replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>').replace(/\n/g, '<br>')
}

const Workbench = {
  /** 初始化表单、用户状态、页面读取和持久化恢复生命周期。 */
  setup() {
    const route = useRoute(); const router = useRouter(); const store = useWorkspace()
    const initialized = ref(false); const busy = ref(''); const error = ref(null); const notice = ref('')
    const authMode = ref('login'); const auth = reactive({ email:'', password:'', nickname:'' })
    const page = computed(() => route.name)
    const title = computed(() => navigation.find(item => item.name === page.value)?.title || '简报详情')
    const briefings = ref({ items:[], page:1, total:0, total_pages:0 })
    const latestLoading = ref(false)
    const schedules = ref({ items:[], page:1, total:0, total_pages:0 })
    const selectedTask = ref(null); const executions = ref([]); const briefing = ref(null); const deliveries = ref([])
    const instant = reactive({ query:'', start:'', end:'', limit:5 })
    const taskForm = reactive({ id:'', name:'', frequency:'daily', date:'', time:'08:00', timezone:'Asia/Shanghai', query:'', limit:5, start:'', end:'', overrides:{} })
    const editing = ref(false)
    const settings = reactive({ nickname:'', timezone:'Asia/Shanghai', topics:'', keywords:'', language:'zh-CN', daily_push_time:'', max_events:5, summary_max_chars:400, layout:'list', sort:'latest', detail:'' })
    const settingsBaseline = ref({}); const settingsVersion = ref(null)
    const historyForm = reactive({ query:'', start:'', end:'', limit:20 }); const history = ref(null); const historyBriefing = ref(null)
    const threadId = ref(''); const messages = ref([]); const draft = ref(''); const streaming = ref(false); const chatRecovery = ref(false); const interrupt = ref(null)
    const interruptions = ref([]); const resumeAnswers = reactive({}); const approvalChoices = reactive({}); const resources = ref([])
    let chatController; let pollTimer; let polling = false; let disposed = false; let sessionEpoch = 0; let latestRequest = 0

    /** 显示安全的请求错误，会话失效时清空用户私有内存。 */
    function showError(value) {
      error.value = { message:value.message || '操作未完成，请刷新状态后重试。', request_id:value.request_id, code:value.code }
      if (value.status === 401) clearSession()
    }
    /** 统一处理写操作，禁止重复点击，不自动重放不确定的写请求。 */
    async function perform(key, action) {
      if (busy.value) return
      busy.value = key; error.value = null; notice.value = ''
      try { await action() } catch (value) { showError(value) } finally { busy.value = '' }
    }
    /** 退出或失效时清理账号状态和流连接。 */
    function clearSession() {
      sessionEpoch += 1; chatController?.abort()
      store.user = null; store.generationTasks = []; messages.value = []; threadId.value = ''; briefing.value = null; deliveries.value = []; selectedTask.value = null; history.value = null; interrupt.value = null; streaming.value = false
      briefings.value = { items:[], page:1, total:0, total_pages:0 }; schedules.value = { items:[], page:1, total:0, total_pages:0 }; interruptions.value = []; resources.value = []
    }
    /** 读取账号专属的恢复标识，损坏或禁用的存储不阻断页面。 */
    function readLocal(key, fallback) {
      try { return JSON.parse(localStorage.getItem(`dailynews:${store.user.id}:${key}`)) ?? fallback } catch { return fallback }
    }
    /** 仅保存线程与任务标识，真实内容仍通过鉴权接口读取。 */
    function saveLocal(key, value) {
      try { localStorage.setItem(`dailynews:${store.user.id}:${key}`, JSON.stringify(value)) } catch { /* 禁用本地存储不影响服务器持久化。 */ }
    }
    /** 用持久化字段刷新设置表单及版本快照。 */
    function fillSettings(user) {
      Object.assign(settings, { nickname:user.nickname || '', timezone:user.timezone, topics:(user.topics || []).join(', '), keywords:(user.keywords || []).join(', '), language:user.language, daily_push_time:user.daily_push_time || '', max_events:user.style?.max_events ?? 5, summary_max_chars:user.style?.summary_max_chars ?? 400, layout:user.style?.layout || 'list', sort:user.style?.sort || 'latest', detail:user.style?.detail || '' })
      settingsBaseline.value = JSON.parse(JSON.stringify(settings)); settingsVersion.value = user.settings_version
    }
    /** 登录后按当前账号恢复线程和在途任务。 */
    async function enterSession(user) {
      store.user = user; fillSettings(user)
      threadId.value = readLocal('thread', '') || crypto.randomUUID(); saveLocal('thread', threadId.value)
      const ids = readLocal('tasks', [])
      store.generationTasks = Array.isArray(ids) ? ids.filter(id => typeof id === 'string').slice(-30).map(id => ({ id, status:'queued', allowed_actions:[] })) : []
      if (route.name === 'login') await router.replace('/')
      await loadPage(); await pollTasks()
    }
    /** 通过 Cookie 恢复账号，认证失败显示登录，其他故障显示原因。 */
    async function restoreSession() {
      try { await enterSession(await api('/me')) } catch (value) { if (value.status !== 401) showError(value) } finally { initialized.value = true }
    }
    /** 提交登录或注册，成功后清除密码输入。 */
    async function authenticate() { await perform('auth', async () => { const user = await api(`/auth/${authMode.value}`, { method:'POST', body:{ ...auth } }); auth.password = ''; await enterSession(user) }) }
    /** 注销服务器会话并回到登录页。 */
    async function logout() { await perform('logout', async () => { await api('/auth/logout', { method:'POST' }); clearSession(); await router.replace('/login') }) }
    /** 加载指定页简报。 */
    async function loadBriefings(number = 1) { briefings.value = await api(`/news/briefings?page=${number}`) }
    /** 按创建时间跨页寻找最新成功简报，避免无正文记录挡住阅读；丢弃离页或换账号后的响应。 */
    async function loadLatestBriefing() {
      const request = ++latestRequest; const epoch = sessionEpoch
      latestLoading.value = true; briefing.value = null; deliveries.value = []
      try {
        let number = 1
        while (true) {
          const result = await api(`/news/briefings?page=${number}`)
          if (request !== latestRequest || epoch !== sessionEpoch || page.value !== 'home') return
          const latest = result.items?.find(item => item.status === 'succeeded')
          if (latest) {
            const detail = await api(`/news/briefings/${encodeURIComponent(latest.id)}`)
            if (request === latestRequest && epoch === sessionEpoch && page.value === 'home') briefing.value = detail
            return
          }
          if (number >= (result.total_pages || 1)) return
          number += 1
        }
      } finally { if (request === latestRequest) latestLoading.value = false }
    }
    /** 加载分页任务，兼容旧版数组响应。 */
    async function loadSchedules(number = 1) {
      const data = await api(`/tasks?page=${number}`)
      schedules.value = Array.isArray(data) ? { items:data, page:1, total:data.length, total_pages:1 } : data
    }
    /** 加载当前路由数据，进入设置时读取对话修改后的最新偏好。 */
    async function loadPage() {
      if (!store.user) return
      error.value = null; notice.value = ''
      try {
        if (page.value === 'home') await loadLatestBriefing()
        if (page.value === 'briefings') await loadBriefings()
        if (page.value === 'tasks') await loadSchedules()
        if (page.value === 'settings') { store.user = await api('/me'); fillSettings(store.user) }
        if (page.value === 'assistant') await reloadConversation()
        if (page.value === 'briefing') await loadBriefing(route.params.id)
      } catch (value) { showError(value) }
    }
    /** 用明确时间窗口创建即时任务，保存服务端任务 ID 供刷新恢复。 */
    async function generate() {
      await perform('generate', async () => {
        const start = zonedDateTime(instant.start, store.user.timezone); const end = zonedDateTime(instant.end, store.user.timezone)
        if (start >= end) throw new Error('开始时间必须早于结束时间。')
        const task = await api('/news/briefings', { method:'POST', key:crypto.randomUUID(), body:{ query:instant.query, limit:instant.limit, start, end } })
        store.generationTasks.unshift({ ...task, id:task.id || task.task_id, query:instant.query }); saveLocal('tasks', store.generationTasks.map(item => item.id))
        notice.value = '任务已保存，等待后台处理。'; await pollTasks()
      })
    }
    /** 根据持久化状态轮询；终态停止，断线不重新创建任务。 */
    async function pollTasks() {
      if (!store.user || polling) return
      polling = true; const epoch = sessionEpoch
      try {
        for (const task of store.generationTasks.filter(item => !terminal.has(item.status))) {
          try {
            const fresh = await api(`/news/tasks/${task.id}`)
            if (epoch !== sessionEpoch || disposed) return
            Object.assign(task, fresh, { connection_error:false })
            if (terminal.has(task.status) && page.value === 'home') await loadLatestBriefing()
            if (terminal.has(task.status) && page.value === 'briefings') await loadBriefings(briefings.value.page)
          } catch (value) {
            if (epoch !== sessionEpoch) return
            task.connection_error = true
            if ([403,404].includes(value.status)) { task.status = 'system_failed'; task.error_code = '任务不可访问' }
            if (value.status === 401) { showError(value); return }
          }
        }
      } finally { polling = false }
    }
    /** 按服务端允许操作取消即时任务，随后继续读取终态。 */
    async function cancelGeneration(task) { await perform('cancel', async () => { Object.assign(task, await api(`/news/tasks/${task.id}/cancel`, { method:'POST' })); await pollTasks() }) }
    /** 用户明确重试原任务，复用服务端保存的查询和时间窗口。 */
    async function retryGeneration(task) { await perform('retry', async () => { Object.assign(task, await api(`/news/tasks/${task.id}/retry`, { method:'POST' })); await pollTasks() }) }
    /** 读取简报和邮件历史，HTML 由隔离 iframe 独立加载。 */
    async function loadBriefing(id) {
      briefing.value = null; deliveries.value = []
      briefing.value = await api(`/news/briefings/${encodeURIComponent(id)}`)
      const result = await api(`/briefings/${encodeURIComponent(id)}/deliveries`); deliveries.value = result.items || result
    }
    /** 执行邮件发送、重试或确认；无论结果如何先读取持久化记录。 */
    async function mailAction(action, delivery, delivered) {
      await perform('mail', async () => {
        try {
          const path = action === 'send' ? `/briefings/${briefing.value.id}/send` : `/deliveries/${delivery.id || delivery.delivery_id}/${action}${action === 'confirm' ? `?delivered=${delivered}` : ''}`
          await api(path, { method:'POST', timeout:30000 }); notice.value = '邮件状态已更新。'
        } finally { const result = await api(`/briefings/${briefing.value.id}/deliveries`); deliveries.value = result.items || result }
      })
    }
    /** 初始化新任务表单。 */
    function newTask() { Object.assign(taskForm, { id:'', name:'', frequency:'daily', date:'', time:'08:00', timezone:store.user.timezone, query:'', limit:5, start:'', end:'', overrides:{} }); editing.value = true }
    /** 读取任务详情和最近执行，不从列表推测操作。 */
    async function inspectTask(task) { await perform('inspect', async () => { selectedTask.value = await api(`/tasks/${task.id}`); const result = await api(`/tasks/${task.id}/executions`); executions.value = result.items || result }) }
    /** 将任务原始时区和配置载入编辑表单。 */
    function editTask() {
      const task = selectedTask.value; const local = formDate(task.run_at, task.timezone)
      Object.assign(taskForm, { id:task.id, name:task.name, frequency:task.frequency, timezone:task.timezone, date:local.slice(0,10), time:local.slice(11), query:task.query, limit:task.limit, start:formDate(task.window_start,task.timezone), end:formDate(task.window_end,task.timezone), overrides:task.overrides || {} }); editing.value = true
    }
    /** 保存每日或一次性任务，一次性任务必须指定新闻窗口。 */
    async function saveTask() {
      await perform('task', async () => {
        const today = formDate(new Date().toISOString(),taskForm.timezone).slice(0,10)
        const payload = { name:taskForm.name || null, frequency:taskForm.frequency, timezone:taskForm.timezone, query:taskForm.query, limit:taskForm.limit, overrides:taskForm.overrides, run_at:zonedDateTime(`${taskForm.frequency === 'daily' ? today : taskForm.date}T${taskForm.time}`,taskForm.timezone) }
        if (taskForm.frequency === 'once') {
          payload.window_start = zonedDateTime(taskForm.start,taskForm.timezone); payload.window_end = zonedDateTime(taskForm.end,taskForm.timezone)
          if (payload.window_start >= payload.window_end) throw new Error('新闻开始时间必须早于结束时间。')
        }
        await api(`/tasks${taskForm.id ? `/${taskForm.id}` : ''}`, { method:taskForm.id ? 'PATCH' : 'POST', key:crypto.randomUUID(), body:payload })
        editing.value = false; selectedTask.value = null; await loadSchedules(); notice.value = '定时任务已保存。'
      })
    }
    /** 执行后端允许的暂停、恢复或取消，刷新任务状态。 */
    async function scheduleAction(task, action) { await perform('schedule-action', async () => { await api(`/tasks/${task.id}/${action}`, { method:'POST' }); selectedTask.value = null; await loadSchedules(); notice.value = '任务状态已更新。' }) }
    /** 字段级提交改动并携带版本，避免覆盖对话同期修改。 */
    async function saveSettings() {
      await perform('settings', async () => {
        const body = { version:settingsVersion.value }
        for (const key of ['nickname','timezone','language','daily_push_time']) if (settings[key] !== settingsBaseline.value[key]) body[key] = key === 'daily_push_time' ? settings[key] || null : settings[key]
        for (const key of ['topics','keywords']) if (settings[key] !== settingsBaseline.value[key]) body[key] = splitList(settings[key])
        const styleFields = ['max_events','summary_max_chars','layout','sort','detail']
        if (styleFields.some(key => settings[key] !== settingsBaseline.value[key])) body.style = { ...store.user.style, ...Object.fromEntries(styleFields.map(key => [key,settings[key]])) }
        store.user = await api('/settings', { method:'PATCH', body }); fillSettings(store.user); notice.value = '账户与订阅设置已保存。'
      })
    }
    /** 带可选时间范围检索当前用户已保存事件。 */
    async function searchHistory() {
      await perform('history', async () => {
        const params = new URLSearchParams({ query:historyForm.query, limit:historyForm.limit })
        if (historyForm.start || historyForm.end) {
          if (!historyForm.start || !historyForm.end) throw new Error('请同时填写开始和结束时间。')
          const start = zonedDateTime(historyForm.start,store.user.timezone); const end = zonedDateTime(historyForm.end,store.user.timezone)
          if (start >= end) throw new Error('开始时间必须早于结束时间。')
          params.set('start',start); params.set('end',end)
        }
        history.value = await api(`/history?${params}`)
      })
    }
    /** 仅根据已检索历史事件生成专题简报，不触发新闻采集或邮件发送。 */
    async function createHistoryBriefing() {
      await perform('history-briefing', async () => {
        const body = { query:historyForm.query, limit:Math.min(historyForm.limit,20) }
        if (historyForm.start && historyForm.end) {
          body.start = zonedDateTime(historyForm.start,store.user.timezone); body.end = zonedDateTime(historyForm.end,store.user.timezone)
        }
        historyBriefing.value = await api('/history/briefings', { method:'POST', body, timeout:120000 })
        if (historyBriefing.value.id && historyBriefing.value.status === 'succeeded') notice.value = '历史专题简报已生成，可在下方查看或下载。'
      })
    }
    /** 从服务端恢复最近消息和持久化中断，不重新发送消息。 */
    async function reloadConversation() {
      const result = await api(`/chat/${encodeURIComponent(threadId.value)}`)
      messages.value = result.messages || []; interruptions.value = result.interrupts || (result.interrupt ? [result.interrupt] : []); interrupt.value = interruptions.value[0] || null; resources.value = result.resources || []; chatRecovery.value = false
    }
    /** 新建独立会话标识；已有线程继续保留在服务端，不恢复或重放旧审批。 */
    function newConversation() {
      if (streaming.value) return
      threadId.value = crypto.randomUUID(); saveLocal('thread',threadId.value)
      messages.value = []; interruptions.value = []; interrupt.value = null; resources.value = []; draft.value = ''; chatRecovery.value = false; error.value = null
    }
    /** 用户显式重载会话，并同步对话可能修改的订阅。 */
    async function recoverChat() { await perform('recover', async () => { await reloadConversation(); store.user = await api('/me') }) }
    /** 流式发送消息；断线查询持久化会话，禁止自动重放。 */
    async function sendChat() {
      if (streaming.value || !draft.value.trim() || chatRecovery.value || interruptions.value.length) return
      const message = draft.value.trim(); draft.value = ''
      messages.value.push({ role:'user',content:message })
      await runConversation('/chat/stream', { message,thread_id:threadId.value })
    }
    /** 提交当前补问答复或逐项明确审批；未选择的命令绝不默认批准。 */
    async function resumeChat(item) {
      if (streaming.value || chatRecovery.value) return
      const value = item.value || item
      let resume
      if (value.type === 'question') {
        resume = (resumeAnswers[item.id] || '').trim()
        if (!resume) return
      } else if (value.action_requests?.length) {
        const decisions = value.action_requests.map((action,index) => ({ type:approvalChoices[`${item.id}:${index}`] }))
        if (decisions.some(decision => !['approve','reject'].includes(decision.type))) { showError(new Error('请为每条命令选择批准或拒绝。')); return }
        resume = { decisions }
      } else { showError(new Error('此中断类型暂不支持，请重新加载会话或新建会话。')); return }
      if (interruptions.value.length > 1) resume = { [item.id]:resume }
      await runConversation(`/chat/${encodeURIComponent(threadId.value)}/resume`, { resume })
    }
    /** 统一消费新消息和恢复流；失败后仅查询持久状态，不重放有副作用的请求。 */
    async function runConversation(path, body) {
      error.value = null; streaming.value = true; messages.value.push({ role:'assistant',content:'' })
      const answer = messages.value[messages.value.length-1]; chatController = new AbortController()
      try {
        await streamChat(path, body, event => {
          if (event.thread_id) { threadId.value = event.thread_id; saveLocal('thread',threadId.value) }
          if (['delta','message','text'].includes(event.type)) answer.content += event.text || ''
          if (event.resources) resources.value = event.resources
          if (event.type === 'interrupt') { interruptions.value = [...interruptions.value.filter(item => item.id !== event.id),event]; interrupt.value = event }
          if (event.type === 'error') throw new Error(event.message || '助手暂时不可用，请重新加载会话。')
        },chatController.signal)
        await reloadConversation(); store.user = await api('/me')
      } catch (value) {
        if (!disposed && store.user) {
          showError(value); chatRecovery.value = true
          try { await reloadConversation(); chatRecovery.value = true } catch { /* 等网络恢复后由用户重新读取。 */ }
        }
      } finally { streaming.value = false }
    }
    /** 周期性更新任务与当前简报邮件状态，不重放副作用。 */
    async function tick() {
      if (!store.user || disposed) return
      await pollTasks()
      if (page.value === 'briefing' && deliveries.value.some(item => ['pending','sending','pending_confirmation'].includes(item.status))) {
        try { const result = await api(`/briefings/${briefing.value.id}/deliveries`); deliveries.value = result.items || result } catch (value) { if (value.status === 401) showError(value) }
      }
    }
    /** 根据服务端返回的允许操作控制按钮。 */
    function allowed(resource, action) { return resource?.allowed_actions?.includes(action) || false }
    watch(() => route.fullPath,loadPage)
    onMounted(async () => { await restoreSession(); pollTimer = setInterval(tick,4000) })
    onUnmounted(() => { disposed = true; clearInterval(pollTimer); chatController?.abort() })
    return { navigation,store,initialized,busy,error,notice,authMode,auth,page,title,briefings,latestLoading,schedules,selectedTask,executions,briefing,deliveries,instant,taskForm,editing,settings,historyForm,history,historyBriefing,threadId,messages,draft,streaming,chatRecovery,interrupt,interruptions,resumeAnswers,approvalChoices,resources,newConversation,resumeChat,authenticate,logout,generate,cancelGeneration,retryGeneration,loadPage,loadBriefings,loadLatestBriefing,loadSchedules,loadBriefing,mailAction,newTask,inspectTask,editTask,saveTask,scheduleAction,saveSettings,searchHistory,createHistoryBriefing,sendChat,recoverChat,formatDate,markdownToHtml,sourceUrl,labels,allowed,showError }
  }, template,
}
const router = createRouter({ history:createWebHashHistory(), routes:[...navigation.map(item => ({ path:item.path,name:item.name,component:Workbench })),{ path:'/briefings/:id',name:'briefing',component:Workbench },{ path:'/login',name:'login',component:Workbench },{ path:'/:pathMatch(.*)*',redirect:'/' }] })
createApp({ template:'<RouterView />' }).use(createPinia()).use(router).mount('#app')
