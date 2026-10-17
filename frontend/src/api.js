/** 对用户显示可处理的接口错误，同时保留请求追踪号。 */
export class ApiError extends Error {
  /** 保存服务端错误码、状态码和安全详情。 */
  constructor(message, details = {}, status = 0) {
    super(message)
    this.name = 'ApiError'
    Object.assign(this, details, { status })
  }
}

/** 解析统一错误；校验错误只返回字段说明，不显示服务端内部内容。 */
export async function responseError(response) {
  const payload = await response.json().catch(() => ({}))
  const detail = typeof payload.detail === 'object' && !Array.isArray(payload.detail) ? payload.detail : payload
  return new ApiError(detail.message || (response.status === 422 ? '请检查输入字段、时间范围和时区。' : '请求未完成，请稍后重试。'), {
    ...detail, request_id: payload.request_id || detail.request_id || response.headers.get('X-Request-ID'),
  }, response.status)
}

/** 使用 Cookie 调用 JSON API；写请求不自动重放，默认十五秒超时。 */
export async function api(path, { method = 'GET', body, key, timeout = 15000, signal } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  try {
    const response = await fetch(`/api/v1${path}`, {
      method, credentials: 'include', signal: controller.signal,
      headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(key ? { 'Idempotency-Key': key } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    })
    if (!response.ok) throw await responseError(response)
    if (response.status === 204) return null
    return await response.json()
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(error.name === 'AbortError' ? '请求超时或已中断。写入结果可能已保存，请先刷新状态，勿重复提交。' : '网络连接中断，请检查网络并刷新状态。', { code: 'NETWORK_ERROR' })
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
  }
}

/** 增量解析 SSE，支持跨数据块 UTF-8、CRLF、多行 data 与末尾残帧。 */
export function createSSEParser(onEvent) {
  let buffer = ''
  let data = []
  let event = 'message'
  let id = ''
  /** 完成一个事件；注释和没有 data 的心跳不分发。 */
  function dispatch() {
    if (data.length) onEvent({ event, data: data.join('\n'), id })
    data = []
    event = 'message'
  }
  /** 按 SSE 字段规则消费完整一行。 */
  function line(value) {
    if (!value) return dispatch()
    if (value.startsWith(':')) return
    const index = value.indexOf(':')
    const field = index < 0 ? value : value.slice(0, index)
    const content = index < 0 ? '' : value.slice(index + 1).replace(/^ /, '')
    if (field === 'data') data.push(content)
    else if (field === 'event') event = content
    else if (field === 'id' && !content.includes('\0')) id = content
  }
  return {
    /** 保留不完整行；块尾 CR 留到下次判断 CRLF。 */
    push(chunk) {
      buffer += chunk
      let match
      while ((match = /\r\n|\r|\n/.exec(buffer))) {
        if (match[0] === '\r' && match.index === buffer.length - 1) break
        line(buffer.slice(0, match.index))
        buffer = buffer.slice(match.index + match[0].length)
      }
    },
    /** 流结束时消费最后一行和没有空行结尾的最后事件。 */
    finish() {
      if (buffer.endsWith('\r')) buffer = buffer.slice(0, -1)
      if (buffer) line(buffer)
      buffer = ''
      dispatch()
    },
  }
}

/** 发送 POST SSE，并逐事件回调；断线抛错，由调用方读取持久化会话恢复。 */
export async function streamChat(path, body, onEvent, signal) {
  const response = await fetch(`/api/v1${path}`, {
    method: 'POST', credentials: 'include', signal,
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify(body),
  })
  if (!response.ok) throw await responseError(response)
  if (!response.body) throw new ApiError('浏览器无法读取流式响应，请刷新会话。')
  let complete = false
  const parser = createSSEParser(frame => {
    if (frame.data === '[DONE]') { complete = true; return }
    let payload
    try { payload = JSON.parse(frame.data) } catch { throw new ApiError('助手响应格式异常，请重新加载会话。') }
    if (['done', 'complete'].includes(payload.type || frame.event)) complete = true
    onEvent({ ...payload, type: payload.type || frame.event })
  })
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      parser.push(decoder.decode(value, { stream: true }))
    }
    parser.push(decoder.decode())
    parser.finish()
    if (!complete) throw new ApiError('连接已中断，正在读取已保存的会话。')
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}

/** 以明确的 IANA 时区转换墙上时间，拒绝夏令时缺失或重复的时间。 */
export function zonedDateTime(local, timeZone) {
  const parts = local.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/)
  if (!parts) throw new ApiError('请填写完整日期和时间。')
  const formatter = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
  const target = Date.UTC(...[+parts[1], +parts[2] - 1, +parts[3], +parts[4], +parts[5]])
  const candidates = []
  // 覆盖所有民用时区偏移；逐十五分钟扫描也包含半小时和四十五分钟时区。
  for (let offset = -14 * 60; offset <= 14 * 60; offset += 15) {
    const date = new Date(target + offset * 60000)
    const values = Object.fromEntries(formatter.formatToParts(date).map(part => [part.type, part.value]))
    if (`${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}` === local) candidates.push(date.toISOString())
  }
  if (candidates.length !== 1) throw new ApiError('该时间因夏令时不存在或有歧义，请选择其他时间。')
  return candidates[0]
}

/** 外部来源只允许 HTTP(S)，防止存储内容注入脚本链接。 */
export function sourceUrl(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null } catch { return null }
}
