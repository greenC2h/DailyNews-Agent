import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, createSSEParser, streamChat, zonedDateTime, sourceUrl } from './api.js'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('SSE 分帧和断线', () => {
  it('支持逐字符切分 CRLF、多行 data、注释和末尾残帧', () => {
    const events = []
    const parser = createSSEParser(event => events.push(event))
    const input = ': heartbeat\r\nevent: delta\r\nid: 7\r\ndata: {"text":\r\ndata: "你好"}\r\n\r\ndata: [DONE]'
    for (const char of input) parser.push(char)
    parser.finish()
    expect(events).toEqual([{ event:'delta',id:'7',data:'{"text":\n"你好"}' },{ event:'message',id:'7',data:'[DONE]' }])
  })
  it('跨 UTF-8 字节块仍能读取完整中文，接收 DONE', async () => {
    const bytes = new TextEncoder().encode('data: {"type":"delta","text":"新闻简报"}\n\ndata: [DONE]\n\n')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({
      /** 模拟网络将一个汉字拆成多个字节数据块。 */
      start(controller) { for (const byte of bytes) controller.enqueue(Uint8Array.of(byte)); controller.close() },
    }), { headers:{ 'Content-Type':'text/event-stream' } })))
    const events = []
    await streamChat('/chat/stream',{ message:'你好' },event => events.push(event))
    expect(events).toEqual([{ type:'delta',text:'新闻简报' }])
  })
  it('没有完成事件的断流抛错且不重放请求', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('data: {"text":"一半"}\n\n'))
    vi.stubGlobal('fetch',fetcher)
    await expect(streamChat('/chat/stream',{},() => {})).rejects.toThrow('连接已中断')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('拒绝错误 HTTP 状态和不合法 JSON', async () => {
    vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ detail:{ code:'AUTH_REQUIRED',message:'请先登录' },request_id:'r1' }),{ status:401 })).mockResolvedValueOnce(new Response('data: {bad}\n\n')))
    await expect(streamChat('/chat/stream',{},() => {})).rejects.toMatchObject({ status:401,request_id:'r1' })
    await expect(streamChat('/chat/stream',{},() => {})).rejects.toThrow('响应格式异常')
  })
})

describe('JSON API 和安全边界', () => {
  it('传递 Cookie、幂等键与 JSON，不重试失败写请求', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('offline'))
    vi.stubGlobal('fetch',fetcher)
    await expect(api('/tasks',{ method:'POST',body:{ query:'AI' },key:'stable' })).rejects.toMatchObject({ code:'NETWORK_ERROR' })
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ credentials:'include',method:'POST',headers:{ 'Idempotency-Key':'stable','Content-Type':'application/json' },body:'{"query":"AI"}' })
  })
  it('保留错误请求编号，并隐藏校验内部响应', async () => {
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail:[{ input:'secret' }] }),{ status:422,headers:{ 'X-Request-ID':'trace' } })))
    await expect(api('/settings')).rejects.toMatchObject({ request_id:'trace',message:'请检查输入字段、时间范围和时区。' })
  })
  it('外部来源拒绝脚本和相对地址', () => {
    expect(sourceUrl('javascript:alert(1)')).toBeNull()
    expect(sourceUrl('/account')).toBeNull()
    expect(sourceUrl('https://example.com/news')).toBe('https://example.com/news')
  })
})

describe('IANA 时区时间窗口', () => {
  it('按指定时区换算，支持四十五分钟时区', () => {
    expect(zonedDateTime('2026-10-18T08:00','Asia/Shanghai')).toBe('2026-10-18T00:00:00.000Z')
    expect(zonedDateTime('2026-10-18T08:00','Asia/Kathmandu')).toBe('2026-10-18T02:15:00.000Z')
  })
  it('拒绝无效日期、夏令时缺失和重复时刻', () => {
    expect(() => zonedDateTime('2026-02-30T08:00','UTC')).toThrow()
    expect(() => zonedDateTime('2026-03-08T02:30','America/New_York')).toThrow('夏令时')
    expect(() => zonedDateTime('2026-11-01T01:30','America/New_York')).toThrow('夏令时')
  })
})
