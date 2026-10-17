import { createApp, ref, onMounted } from 'vue'
import './style.css'

const api = (path, options = {}) => fetch(`/api/v1${path}`, { credentials: 'include', headers: { 'Content-Type': 'application/json' }, ...options }).then(async response => { const data = await response.json(); if (!response.ok) throw data; return data })

const App = { setup() {
  const loggedIn = ref(false); const email = ref(''); const password = ref(''); const message = ref(''); const answer = ref(''); const query = ref(''); const briefings = ref([]); const error = ref('')
  const refresh = async () => { try { await api('/me'); loggedIn.value = true; const page = await api('/news/briefings'); briefings.value = page.items } catch { loggedIn.value = false } }
  const login = async () => { try { await api('/auth/login', { method: 'POST', body: JSON.stringify({ email: email.value, password: password.value }) }); await refresh() } catch (e) { error.value = e.detail?.message || '登录失败' } }
  const register = async () => { try { await api('/auth/register', { method: 'POST', body: JSON.stringify({ email: email.value, password: password.value }) }); await refresh() } catch (e) { error.value = e.detail?.message || '注册失败' } }
  const chat = async () => { answer.value = ''; const response = await fetch('/api/v1/chat/stream', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: message.value }) }); const reader = response.body.getReader(); const decoder = new TextDecoder(); while (true) { const { value, done } = await reader.read(); if (done) break; for (const line of decoder.decode(value).split('\n')) if (line.startsWith('data: ') && line.slice(6) !== '[DONE]') answer.value += JSON.parse(line.slice(6)).text || '' } }
  onMounted(refresh); return { loggedIn, email, password, message, answer, query, briefings, error, login, register, chat, refresh }
}, template: `<main><header><h1>DailyNews</h1><p>AI 新闻简报工作台</p></header><section v-if="!loggedIn"><h2>登录</h2><input v-model="email" placeholder="邮箱"><input v-model="password" type="password" placeholder="密码"><button @click="login">登录</button><button @click="register">注册</button></section><section v-else><h2>问问助手</h2><textarea v-model="message" placeholder="输入问题或新闻任务"></textarea><button @click="chat">发送</button><pre>{{ answer }}</pre><h2>简报</h2><button @click="refresh">刷新</button><article v-for="item in briefings" :key="item.id"><b>{{ item.title }}</b> · {{ item.status }} · <a :href="'/api/v1/news/briefings/'+item.id+'/html" target="_blank">查看</a></article></section><p class="error">{{ error }}</p></main>` }
createApp(App).mount('#app')
