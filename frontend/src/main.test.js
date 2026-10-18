import { describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const captured = vi.hoisted(() => ({ component:null }))

vi.mock('vue', async importOriginal => {
  /** 保留真实响应式状态，隔离测试不需要的浏览器生命周期。 */
  const original = await importOriginal()
  return { ...original, onMounted:vi.fn(), onUnmounted:vi.fn(), watch:vi.fn(),
    createApp:vi.fn(() => ({ use:vi.fn().mockReturnThis(), mount:vi.fn() })) }
})
vi.mock('vue-router', () => ({
  /** 捕获实际页面组件，避免测试依赖浏览器路由。 */
  createRouter:vi.fn(options => { captured.component = options.routes[0].component; return {} }),
  createWebHashHistory:vi.fn(), useRoute:vi.fn(() => ({ name:'history', params:{} })),
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
