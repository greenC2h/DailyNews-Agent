import { describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const captured = vi.hoisted(() => ({ component:null, route:{ name:'history', params:{} } }))

vi.mock('vue', async importOriginal => {
  /** 保留真实响应式状态，隔离测试不需要的浏览器生命周期。 */
  const original = await importOriginal()
  return { ...original, onMounted:vi.fn(), onUnmounted:vi.fn(), watch:vi.fn(),
    createApp:vi.fn(() => ({ use:vi.fn().mockReturnThis(), mount:vi.fn() })) }
})
vi.mock('vue-router', () => ({
  /** 捕获实际页面组件，避免测试依赖浏览器路由。 */
  createRouter:vi.fn(options => { captured.component = options.routes[0].component; return {} }),
  createWebHashHistory:vi.fn(), useRoute:vi.fn(() => captured.route),
  useRouter:vi.fn(() => ({ push:vi.fn() })),
}))
vi.mock('./api.js', async importOriginal => ({ ...await importOriginal(), api:vi.fn() }))

import { api } from './api.js'
import './main.js'

describe('历史专题结果入口', () => {
  /** 验证真实页面 setup 向模板暴露成功专题，使查看与下载按钮可渲染。 */
  it('生成后保留可供模板读取的专题结果，并为模型请求设置足够等待时间', async () => {
    setActivePinia(createPinia())
    const page = captured.component.setup()
    page.historyForm.query = 'AI'
    const result = { id:'briefing-test', status:'succeeded', event_count:4 }
    vi.mocked(api).mockResolvedValueOnce(result)
    await page.createHistoryBriefing()
    expect(page.historyBriefing.value).toEqual(result)
    expect(page.notice.value).toContain('已生成')
    expect(api).toHaveBeenCalledWith('/history/briefings', {
      method:'POST', body:{ query:'AI', limit:20 }, timeout:120000,
    })
  })
})

describe('首页最新简报', () => {
  /** 验证首页跳过无 HTML 的失败记录，并从下一页读取最新成功简报。 */
  it('跨页查找最新成功简报并只读取其正文', async () => {
    setActivePinia(createPinia())
    captured.route.name = 'home'
    const page = captured.component.setup()
    page.store.user = { id:'user-test', timezone:'Asia/Shanghai' }
    const detail = { id:'briefing-success', status:'succeeded', title:'最新简报', events:[] }
    vi.mocked(api)
      .mockResolvedValueOnce({ items:[{ id:'briefing-failed', status:'generation_failed' }], total_pages:2 })
      .mockResolvedValueOnce({ items:[{ id:'briefing-success', status:'succeeded' }], total_pages:2 })
      .mockResolvedValueOnce(detail)
    await page.loadLatestBriefing()
    expect(page.briefing.value).toEqual(detail)
    expect(vi.mocked(api).mock.calls.map(([path]) => path)).toEqual([
      '/news/briefings?page=1', '/news/briefings?page=2', '/news/briefings/briefing-success',
    ])
    expect(page.latestLoading.value).toBe(false)
    captured.route.name = 'history'
  })

  /** 验证没有生成过成功简报时保持清爽空状态且不请求详情。 */
  it('无成功记录时保留空首页', async () => {
    setActivePinia(createPinia())
    captured.route.name = 'home'
    const page = captured.component.setup()
    page.store.user = { id:'user-test', timezone:'Asia/Shanghai' }
    vi.mocked(api).mockResolvedValueOnce({ items:[{ id:'briefing-empty', status:'no_events' }], total_pages:1 })
    await page.loadLatestBriefing()
    expect(page.briefing.value).toBeNull()
    expect(vi.mocked(api)).toHaveBeenCalledTimes(1)
    captured.route.name = 'history'
  })
})
