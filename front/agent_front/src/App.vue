<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'

type StreamEvent = {
  type: 'status' | 'phase' | 'route' | 'final' | 'error'
  message?: string
  final?: string
  node?: string
}

type ChatMessage = {
  id: string
  role: 'user' | 'assistant' | 'status'
  content: string
}

type AuthUser = {
  id: string
  username: string
  tenant_id: string
  email?: string | null
  display_name?: string | null
}

type AuthResponse = {
  token: string
  user: AuthUser
}

const TOKEN_KEY = 'deepresearch_auth_token'
const USER_KEY = 'deepresearch_auth_user'
const THREAD_KEY = 'deepresearch_thread_id'

const createThreadId = () => {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return `thread-${crypto.randomUUID()}`
  }
  return `thread-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const savedToken = localStorage.getItem(TOKEN_KEY) || ''
const savedUser = localStorage.getItem(USER_KEY)

const authToken = ref(savedToken)
const currentUser = ref<AuthUser | null>(savedUser ? JSON.parse(savedUser) : null)
const authMode = ref<'login' | 'register'>('login')
const authLoading = ref(false)
const authError = ref('')
const authForm = ref({
  username: '',
  password: '',
  email: '',
  display_name: '',
})

const threadId = ref(localStorage.getItem(THREAD_KEY) || createThreadId())
const tenantId = computed(() => currentUser.value?.tenant_id || 'default_tenant')
const userId = computed(() => currentUser.value?.id || '')
const displayName = computed(() => currentUser.value?.display_name || currentUser.value?.username || '用户')
const isAuthenticated = computed(() => Boolean(authToken.value && currentUser.value))

const query = ref('')
const loading = ref(false)
const errorMessage = ref('')
const messageListRef = ref<HTMLElement | null>(null)
const composerRef = ref<HTMLTextAreaElement | null>(null)
const progressLogs = ref<string[]>([])

const starterPrompts = [
  {
    title: '深度调研',
    prompt: '请调研“企业知识库 Agent 平台”市场，按市场规模、主要竞品、收费模式三部分输出，并在每部分附上可追溯来源链接。',
  },
  {
    title: '方案对比',
    prompt: '我们要做大模型研究助手，请对比“纯大模型直答”“RAG + Agent”“多 Agent 协作”三种方案，给出优缺点、适用场景与推荐结论。',
  },
  {
    title: '知识问答',
    prompt: '请解释这个项目里“意图分流”的作用，以及简单问题和复杂问题分别会走哪条链路。',
  },
]

const welcomeText = () =>
  `你好，${displayName.value}。你可以直接提问，我会根据当前会话 ${threadId.value} 做记忆隔离。`

const messages = ref<ChatMessage[]>([
  {
    id: `m-${Date.now()}`,
    role: 'assistant',
    content: welcomeText(),
  },
])

const escapeHtml = (value: string): string =>
  value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')

const markdownToHtml = (markdown: string): string => {
  const codeBlocks: string[] = []
  let text = markdown.replace(/```([\s\S]*?)```/g, (_, block) => {
    const index = codeBlocks.length
    codeBlocks.push(`<pre><code>${escapeHtml(String(block).trim())}</code></pre>`)
    return `@@CODE_BLOCK_${index}@@`
  })
  const lines = text.split('\n')
  const out: string[] = []
  let inList = false
  const closeList = () => {
    if (inList) {
      out.push('</ul>')
      inList = false
    }
  }
  for (const rawLine of lines) {
    const line = rawLine.trim()
    if (!line) {
      closeList()
      continue
    }
    if (line.startsWith('# ')) {
      closeList()
      out.push(`<h1>${escapeHtml(line.slice(2))}</h1>`)
      continue
    }
    if (line.startsWith('## ')) {
      closeList()
      out.push(`<h2>${escapeHtml(line.slice(3))}</h2>`)
      continue
    }
    if (line.startsWith('### ')) {
      closeList()
      out.push(`<h3>${escapeHtml(line.slice(4))}</h3>`)
      continue
    }
    if (line.startsWith('- ') || line.startsWith('* ')) {
      if (!inList) {
        out.push('<ul>')
        inList = true
      }
      out.push(`<li>${escapeHtml(line.slice(2))}</li>`)
      continue
    }
    closeList()
    out.push(`<p>${escapeHtml(line)}</p>`)
  }
  closeList()
  let html = out.join('')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  html = html.replace(/\[([^[\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
  html = html.replace(/@@CODE_BLOCK_(\d+)@@/g, (_, idx) => codeBlocks[Number(idx)] || '')
  return html
}

const renderMessageHtml = (message: ChatMessage) => markdownToHtml(message.content || '')

const scrollToBottom = async () => {
  await nextTick()
  const el = messageListRef.value
  if (el) {
    el.scrollTop = el.scrollHeight
  }
}

const resetMessages = () => {
  messages.value = [
    {
      id: `m-${Date.now()}`,
      role: 'assistant',
      content: welcomeText(),
    },
  ]
}

const persistAuth = (auth: AuthResponse) => {
  authToken.value = auth.token
  currentUser.value = auth.user
  localStorage.setItem(TOKEN_KEY, auth.token)
  localStorage.setItem(USER_KEY, JSON.stringify(auth.user))
  localStorage.setItem(THREAD_KEY, threadId.value)
  resetMessages()
}

const clearAuth = () => {
  authToken.value = ''
  currentUser.value = null
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  messages.value = []
}

const authHeaders = () => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${authToken.value}`,
})

const submitAuth = async () => {
  if (authLoading.value) return
  authLoading.value = true
  authError.value = ''
  try {
    const username = authForm.value.username.trim()
    const password = authForm.value.password
    const endpoint = authMode.value === 'login' ? '/api/v1/auth/login' : '/api/v1/auth/register'
    const body =
      authMode.value === 'login'
        ? { username, password }
        : {
            username,
            password,
            email: authForm.value.email.trim() || null,
            display_name: authForm.value.display_name.trim() || null,
            tenant_id: 'default_tenant',
          }
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const text = await response.text()
      throw new Error(text || `登录失败: ${response.status}`)
    }
    const data = (await response.json()) as AuthResponse
    persistAuth(data)
    authForm.value.password = ''
  } catch (error) {
    authError.value = error instanceof Error ? error.message : '认证失败'
  } finally {
    authLoading.value = false
  }
}

const restoreSession = async () => {
  if (!authToken.value) return
  try {
    const response = await fetch('/api/v1/auth/me', {
      headers: { Authorization: `Bearer ${authToken.value}` },
    })
    if (!response.ok) {
      clearAuth()
      return
    }
    const user = (await response.json()) as AuthUser
    currentUser.value = user
    localStorage.setItem(USER_KEY, JSON.stringify(user))
    resetMessages()
  } catch {
    clearAuth()
  }
}

const logout = async () => {
  const token = authToken.value
  clearAuth()
  if (!token) return
  try {
    await fetch('/api/v1/auth/logout', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    })
  } catch {
    // Local logout should still work even if the network request fails.
  }
}

const createNewChat = () => {
  threadId.value = createThreadId()
  localStorage.setItem(THREAD_KEY, threadId.value)
  resetMessages()
  progressLogs.value = []
  errorMessage.value = ''
  query.value = ''
}

const usePrompt = async (prompt: string) => {
  query.value = prompt
  errorMessage.value = ''
  await nextTick()
  composerRef.value?.focus()
}

const useStarterPrompt = (index: number) => {
  const item = starterPrompts[index]
  if (item) {
    usePrompt(item.prompt)
  }
}

const pushProgress = (message: string) => {
  const msg = message.trim()
  if (!msg) return
  const last = progressLogs.value[progressLogs.value.length - 1]
  if (last === msg) return
  progressLogs.value.push(msg)
  if (progressLogs.value.length > 6) {
    progressLogs.value = progressLogs.value.slice(-6)
  }
}

const runResearch = async () => {
  const userText = query.value.trim()
  if (!userText || loading.value) return
  if (!isAuthenticated.value) {
    authError.value = '请先登录'
    return
  }
  loading.value = true
  errorMessage.value = ''
  progressLogs.value = []
  query.value = ''
  messages.value.push({ id: `u-${Date.now()}`, role: 'user', content: userText })
  const statusId = `s-${Date.now()}`
  messages.value.push({ id: statusId, role: 'status', content: '正在初始化执行链路...' })
  const renderStatusText = () => {
    const statusMessage = messages.value.find((item) => item.id === statusId)
    if (!statusMessage) return
    const latest = progressLogs.value.slice(-8)
    statusMessage.content = ['正在处理...', ...latest].map((line) => `- ${line}`).join('\n')
  }
  renderStatusText()
  await scrollToBottom()
  try {
    const response = await fetch('/api/v1/research/stream', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        query: userText,
        user_id: userId.value,
        thread_id: threadId.value,
        tenant_id: tenantId.value,
      }),
    })
    if (response.status === 401) {
      clearAuth()
      throw new Error('登录已过期，请重新登录')
    }
    if (!response.ok) {
      const text = await response.text()
      throw new Error(text || `请求失败: ${response.status}`)
    }
    if (!response.body) {
      throw new Error('流式响应不可用')
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        if (!part.startsWith('data: ')) continue
        const jsonText = part.slice(6).trim()
        if (!jsonText) continue
        const event = JSON.parse(jsonText) as StreamEvent
        if (event.type === 'status' || event.type === 'phase' || event.type === 'route') {
          const prefix = event.type === 'phase' && event.node ? `[${event.node}] ` : ''
          pushProgress(`${prefix}${event.message || ''}`)
          renderStatusText()
        }
        if (event.type === 'final') {
          messages.value = messages.value.filter((item) => item.id !== statusId)
          messages.value.push({
            id: `a-${Date.now()}`,
            role: 'assistant',
            content: event.final || '已完成，但未返回正文。',
          })
        }
        if (event.type === 'error') {
          throw new Error(event.message || '服务端执行异常')
        }
      }
      await scrollToBottom()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '请求失败'
    messages.value = messages.value.filter((item) => item.id !== statusId)
    messages.value.push({
      id: `e-${Date.now()}`,
      role: 'assistant',
      content: `请求失败：${errorMessage.value}`,
    })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

onMounted(() => {
  restoreSession()
})
</script>

<template>
  <div v-if="!isAuthenticated" class="auth-shell">
    <section class="auth-panel">
      <div class="auth-copy">
        <p class="brand-badge">AI Copilot</p>
        <h1>DeepResearch</h1>
        <p>登录后系统会用真实用户 ID 和会话 ID 做记忆隔离，避免不同用户的历史信息互相串扰。</p>
      </div>

      <form class="auth-form" @submit.prevent="submitAuth">
        <div class="auth-tabs">
          <button type="button" :class="{ active: authMode === 'login' }" @click="authMode = 'login'">登录</button>
          <button type="button" :class="{ active: authMode === 'register' }" @click="authMode = 'register'">注册</button>
        </div>

        <label>
          用户名
          <input v-model="authForm.username" autocomplete="username" required minlength="3" />
        </label>
        <label>
          密码
          <input v-model="authForm.password" type="password" autocomplete="current-password" required minlength="6" />
        </label>
        <template v-if="authMode === 'register'">
          <label>
            昵称
            <input v-model="authForm.display_name" autocomplete="name" placeholder="可选" />
          </label>
          <label>
            邮箱
            <input v-model="authForm.email" type="email" autocomplete="email" placeholder="可选" />
          </label>
        </template>

        <p v-if="authError" class="auth-error">{{ authError }}</p>
        <button class="auth-submit" type="submit" :disabled="authLoading">
          {{ authLoading ? '处理中...' : authMode === 'login' ? '登录' : '创建账号' }}
        </button>
      </form>
    </section>
  </div>

  <div v-else class="chat-shell">
    <aside class="chat-sidebar">
      <div class="sidebar-brand">
        <p class="brand-badge">AI Copilot</p>
        <h1>DeepResearch</h1>
        <p class="brand-desc">多智能体研究工作台，支持快速回答与深度调研。</p>
      </div>

      <div class="user-card">
        <div>
          <span>当前用户</span>
          <strong>{{ displayName }}</strong>
        </div>
        <button @click="logout">退出</button>
      </div>

      <div class="sidebar-head">
        <button class="new-chat-btn" @click="createNewChat">新建会话</button>
      </div>

      <div class="quick-entry">
        <p class="section-title">推荐起手问题</p>
        <button
          v-for="item in starterPrompts"
          :key="item.title"
          class="quick-entry-btn"
          @click="usePrompt(item.prompt)"
        >
          {{ item.title }}
        </button>
      </div>

      <div class="identity-panel">
        <p><span>User ID</span>{{ userId }}</p>
        <p><span>Thread ID</span>{{ threadId }}</p>
        <p><span>Tenant ID</span>{{ tenantId }}</p>
      </div>
      <p class="hint-text">记忆隔离键：{{ userId }} / {{ threadId }}</p>
    </aside>

    <main class="chat-main">
      <header class="main-header">
        <div>
          <h2>DeepResearch Enterprise Workspace</h2>
          <p>面向业务团队的企业级智能研究台，支持从问题定义到结论落地的完整链路。</p>
        </div>
        <div class="header-tags">
          <span>Evidence-Driven</span>
          <span>Structured Output</span>
          <span>Memory-Powered</span>
        </div>
      </header>

      <div ref="messageListRef" class="message-list">
        <section v-if="messages.length <= 1" class="onboarding-panel">
          <div class="hero-panel">
            <p class="hero-badge">商业研究 / 策略分析 / 知识问答</p>
            <h3>先讲清目标，再交给 DeepResearch 自动推进</h3>
            <p class="hero-desc">
              推荐提问结构：目标 + 背景约束 + 期望输出。系统会自动选择快速回答或深度研究链路。
            </p>
            <div class="hero-actions">
              <button class="hero-btn primary" @click="useStarterPrompt(0)">快速开始调研</button>
              <button class="hero-btn" @click="useStarterPrompt(1)">查看方案对比</button>
            </div>
          </div>
        </section>

        <div
          v-for="message in messages"
          :key="message.id"
          class="message-row"
          :class="`role-${message.role}`"
        >
          <div class="avatar">{{ message.role === 'user' ? '你' : message.role === 'status' ? '...' : 'AI' }}</div>
          <div class="bubble markdown-body" v-html="renderMessageHtml(message)"></div>
        </div>
      </div>

      <div class="composer">
        <textarea
          v-model="query"
          ref="composerRef"
          class="composer-input"
          :disabled="loading"
          placeholder="输入你的问题，回车发送（Shift + Enter 换行）"
          @keydown.enter.exact.prevent="runResearch"
        />
        <button class="send-btn" :disabled="loading || !query.trim()" @click="runResearch">
          {{ loading ? '处理中...' : '发送' }}
        </button>
      </div>
      <p v-if="errorMessage" class="error">{{ errorMessage }}</p>
    </main>
  </div>
</template>
