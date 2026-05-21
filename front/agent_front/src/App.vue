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

type StoredConversation = {
  threadId: string
  title: string
  updatedAt: number
  messages: ChatMessage[]
  useLocalKb: boolean
  uploadStatus: string
  running?: boolean
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
const CONVERSATION_KEY_PREFIX = 'deepresearch_conversations'

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
const useLocalKb = ref(false)
const selectedKnowledgeFile = ref<File | null>(null)
const uploadingKnowledge = ref(false)
const uploadStatus = ref('')

const query = ref('')
const runningThreads = ref<Record<string, boolean>>({})
const errorMessage = ref('')
const messageListRef = ref<HTMLElement | null>(null)
const composerRef = ref<HTMLTextAreaElement | null>(null)
const progressLogs = ref<string[]>([])
const conversations = ref<StoredConversation[]>([])
const currentLoading = computed(() => Boolean(runningThreads.value[threadId.value]))
const mobileSidebarOpen = ref(false)

const closeMobileSidebar = () => {
  mobileSidebarOpen.value = false
}

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
  `你好，${displayName.value}。我是深度研究型智能体，可以帮你做资料检索、文档分析和结构化报告。`

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

const conversationStorageKey = () => `${CONVERSATION_KEY_PREFIX}_${userId.value || 'anonymous'}`

const isThreadRunning = (targetThreadId: string) => Boolean(runningThreads.value[targetThreadId])

const setThreadRunning = (targetThreadId: string, running: boolean) => {
  const next = { ...runningThreads.value }
  if (running) {
    next[targetThreadId] = true
  } else {
    delete next[targetThreadId]
  }
  runningThreads.value = next
}

const saveConversations = () => {
  if (!userId.value) return
  localStorage.setItem(conversationStorageKey(), JSON.stringify(conversations.value.slice(0, 30)))
}

const loadConversations = () => {
  if (!userId.value) {
    conversations.value = []
    return
  }
  try {
    const raw = localStorage.getItem(conversationStorageKey())
    const parsed = raw ? (JSON.parse(raw) as StoredConversation[]) : []
    conversations.value = parsed
      .filter((item) => item.threadId && Array.isArray(item.messages))
      .map((item) => ({
        ...item,
        running: false,
        messages: item.messages.filter((message) => message.role !== 'status'),
      }))
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, 30)
  } catch {
    conversations.value = []
  }
}

const titleFromMessages = (items: ChatMessage[]) => {
  const firstUserMessage = items.find((item) => item.role === 'user')?.content.trim()
  if (firstUserMessage) {
    return firstUserMessage.length > 28 ? `${firstUserMessage.slice(0, 28)}...` : firstUserMessage
  }
  return '新会话'
}

const upsertCurrentConversation = () => {
  if (!isAuthenticated.value || !threadId.value) return
  const cleanMessages = isThreadRunning(threadId.value) ? messages.value : messages.value.filter((item) => item.role !== 'status')
  if (!cleanMessages.length) return
  const item: StoredConversation = {
    threadId: threadId.value,
    title: titleFromMessages(cleanMessages),
    updatedAt: Date.now(),
    messages: cleanMessages,
    useLocalKb: useLocalKb.value,
    uploadStatus: uploadStatus.value,
    running: isThreadRunning(threadId.value),
  }
  conversations.value = [item, ...conversations.value.filter((conv) => conv.threadId !== item.threadId)].slice(0, 30)
  saveConversations()
}

const updateConversationMessages = (
  targetThreadId: string,
  updater: (items: ChatMessage[]) => ChatMessage[],
  options: Partial<Pick<StoredConversation, 'useLocalKb' | 'uploadStatus' | 'running'>> = {},
) => {
  const existing = conversations.value.find((item) => item.threadId === targetThreadId)
  const baseMessages = targetThreadId === threadId.value ? messages.value : existing?.messages || []
  const nextMessages = updater([...baseMessages])
  const item: StoredConversation = {
    threadId: targetThreadId,
    title: titleFromMessages(nextMessages.filter((message) => message.role !== 'status')),
    updatedAt: Date.now(),
    messages: nextMessages,
    useLocalKb: options.useLocalKb ?? existing?.useLocalKb ?? (targetThreadId === threadId.value ? useLocalKb.value : false),
    uploadStatus: options.uploadStatus ?? existing?.uploadStatus ?? (targetThreadId === threadId.value ? uploadStatus.value : ''),
    running: options.running ?? isThreadRunning(targetThreadId),
  }
  conversations.value = [item, ...conversations.value.filter((conv) => conv.threadId !== targetThreadId)].slice(0, 30)
  saveConversations()
  if (targetThreadId === threadId.value) {
    messages.value = nextMessages
  }
}

const applyConversation = async (conversation: StoredConversation) => {
  threadId.value = conversation.threadId
  localStorage.setItem(THREAD_KEY, conversation.threadId)
  messages.value = conversation.messages.length
    ? conversation.messages
    : [{ id: `m-${Date.now()}`, role: 'assistant', content: welcomeText() }]
  useLocalKb.value = conversation.useLocalKb
  uploadStatus.value = conversation.uploadStatus || ''
  selectedKnowledgeFile.value = null
  progressLogs.value = []
  errorMessage.value = ''
  query.value = ''
  await scrollToBottom()
}

const switchConversation = async (conversation: StoredConversation) => {
  if (conversation.threadId === threadId.value) return
  upsertCurrentConversation()
  await applyConversation(conversation)
  closeMobileSidebar()
}

const openLatestConversationOrReset = () => {
  loadConversations()
  const current = conversations.value.find((item) => item.threadId === threadId.value) || conversations.value[0]
  if (current) {
    applyConversation(current)
    return
  }
  threadId.value = createThreadId()
  localStorage.setItem(THREAD_KEY, threadId.value)
  resetMessages()
}

const deleteConversation = async (conversation: StoredConversation, event: Event) => {
  event.stopPropagation()
  conversations.value = conversations.value.filter((item) => item.threadId !== conversation.threadId)
  saveConversations()
  if (conversation.threadId !== threadId.value) return
  const nextConversation = conversations.value[0]
  if (nextConversation) {
    await applyConversation(nextConversation)
    return
  }
  threadId.value = createThreadId()
  localStorage.setItem(THREAD_KEY, threadId.value)
  useLocalKb.value = false
  selectedKnowledgeFile.value = null
  uploadStatus.value = ''
  resetMessages()
}

const formatConversationTime = (timestamp: number) => {
  const date = new Date(timestamp)
  const now = new Date()
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }
  return date.toLocaleDateString([], { month: '2-digit', day: '2-digit' })
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
  openLatestConversationOrReset()
}

const clearAuth = () => {
  authToken.value = ''
  currentUser.value = null
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  conversations.value = []
  messages.value = []
}

const authHeaders = () => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${authToken.value}`,
})

const bearerHeaders = () => ({
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
    openLatestConversationOrReset()
  } catch {
    clearAuth()
  }
}

const logout = async () => {
  const token = authToken.value
  clearAuth()
  closeMobileSidebar()
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
  upsertCurrentConversation()
  threadId.value = createThreadId()
  localStorage.setItem(THREAD_KEY, threadId.value)
  useLocalKb.value = false
  selectedKnowledgeFile.value = null
  uploadStatus.value = ''
  resetMessages()
  progressLogs.value = []
  errorMessage.value = ''
  query.value = ''
  closeMobileSidebar()
}

const onKnowledgeFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  selectedKnowledgeFile.value = input.files?.[0] || null
  uploadStatus.value = selectedKnowledgeFile.value ? `已选择：${selectedKnowledgeFile.value.name}` : ''
}

const uploadKnowledgeFile = async () => {
  if (!selectedKnowledgeFile.value || uploadingKnowledge.value) return
  uploadingKnowledge.value = true
  uploadStatus.value = '正在上传并写入向量库...'
  try {
    const form = new FormData()
    form.append('file', selectedKnowledgeFile.value)
    form.append('thread_id', threadId.value)
    const response = await fetch('/api/v1/kb/upload', {
      method: 'POST',
      headers: bearerHeaders(),
      body: form,
    })
    if (response.status === 401) {
      clearAuth()
      throw new Error('登录已过期，请重新登录')
    }
    if (!response.ok) {
      const text = await response.text()
      throw new Error(text || `上传失败: ${response.status}`)
    }
    const result = (await response.json()) as { filename: string; chunks: number; message: string }
    useLocalKb.value = true
    uploadStatus.value = `${result.filename} 已入库，共 ${result.chunks} 个片段`
    upsertCurrentConversation()
  } catch (error) {
    uploadStatus.value = error instanceof Error ? error.message : '上传失败'
  } finally {
    uploadingKnowledge.value = false
  }
}

const usePrompt = async (prompt: string) => {
  query.value = prompt
  errorMessage.value = ''
  closeMobileSidebar()
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
  if (!userText || currentLoading.value) return
  if (!isAuthenticated.value) {
    authError.value = '请先登录'
    return
  }
  const requestThreadId = threadId.value
  const requestUseLocalKb = useLocalKb.value
  errorMessage.value = ''
  progressLogs.value = []
  query.value = ''
  const statusId = `s-${Date.now()}`
  setThreadRunning(requestThreadId, true)
  updateConversationMessages(
    requestThreadId,
    (items) => [
      ...items,
      { id: `u-${Date.now()}`, role: 'user', content: userText },
      { id: statusId, role: 'status', content: '正在初始化执行链路...' },
    ],
    { useLocalKb: requestUseLocalKb, uploadStatus: uploadStatus.value, running: true },
  )
  const requestProgressLogs: string[] = []
  const renderStatusText = () => {
    const latest = requestProgressLogs.slice(-8)
    const content = ['正在处理...', ...latest].map((line) => `- ${line}`).join('\n')
    updateConversationMessages(
      requestThreadId,
      (items) => items.map((item) => (item.id === statusId ? { ...item, content } : item)),
      { useLocalKb: requestUseLocalKb, uploadStatus: uploadStatus.value, running: true },
    )
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
        thread_id: requestThreadId,
        tenant_id: tenantId.value,
        use_local_kb: requestUseLocalKb,
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
          const msg = `${prefix}${event.message || ''}`.trim()
          if (msg && requestProgressLogs[requestProgressLogs.length - 1] !== msg) {
            requestProgressLogs.push(msg)
          }
          if (requestThreadId === threadId.value) {
            pushProgress(msg)
          }
          renderStatusText()
        }
        if (event.type === 'final') {
          setThreadRunning(requestThreadId, false)
          updateConversationMessages(
            requestThreadId,
            (items) => [
              ...items.filter((item) => item.id !== statusId),
              {
                id: `a-${Date.now()}`,
                role: 'assistant',
                content: event.final || '已完成，但未返回正文。',
              },
            ],
            { useLocalKb: requestUseLocalKb, uploadStatus: uploadStatus.value, running: false },
          )
        }
        if (event.type === 'error') {
          throw new Error(event.message || '服务端执行异常')
        }
      }
      await scrollToBottom()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '请求失败'
    updateConversationMessages(
      requestThreadId,
      (items) => [
        ...items.filter((item) => item.id !== statusId),
        {
          id: `e-${Date.now()}`,
          role: 'assistant',
          content: `请求失败：${errorMessage.value}`,
        },
      ],
      { useLocalKb: requestUseLocalKb, uploadStatus: uploadStatus.value, running: false },
    )
  } finally {
    setThreadRunning(requestThreadId, false)
    updateConversationMessages(
      requestThreadId,
      (items) => items.filter((item) => item.id !== statusId),
      { useLocalKb: requestUseLocalKb, uploadStatus: uploadStatus.value, running: false },
    )
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
        <p class="brand-badge">Deep Research</p>
        <h1>深度研究型智能体</h1>
        <p>面向复杂问题的多智能体研究工作台，支持联网检索、本地文档分析与结构化报告生成。</p>
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

  <div v-else class="chat-shell" :class="{ 'sidebar-open': mobileSidebarOpen }">
    <button class="mobile-menu-btn" type="button" @click="mobileSidebarOpen = true">菜单</button>
    <div v-if="mobileSidebarOpen" class="mobile-sidebar-backdrop" @click="closeMobileSidebar"></div>
    <aside class="chat-sidebar" @click.stop>
      <button class="mobile-sidebar-close" type="button" @click="closeMobileSidebar">×</button>
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

      <div class="conversation-list">
        <p class="section-title">会话记录</p>
        <div
          v-for="item in conversations"
          :key="item.threadId"
          class="conversation-item"
          :class="{ active: item.threadId === threadId, running: isThreadRunning(item.threadId) }"
          role="button"
          tabindex="0"
          @click="switchConversation(item)"
          @keydown.enter.prevent="switchConversation(item)"
        >
          <div>
            <strong>{{ item.title }}</strong>
            <span>{{ isThreadRunning(item.threadId) ? '生成中' : formatConversationTime(item.updatedAt) }}</span>
          </div>
          <button class="conversation-delete" title="删除会话" @click="deleteConversation(item, $event)">×</button>
        </div>
        <p v-if="!conversations.length" class="empty-history">暂无历史会话</p>
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

      <div class="kb-panel">
        <div class="kb-panel-head">
          <p class="section-title">本地知识库</p>
          <label class="switch-row">
            <input v-model="useLocalKb" type="checkbox" />
            <span>使用</span>
          </label>
        </div>
        <input class="file-input" type="file" accept=".txt,.md,.csv,.json,.log,.pdf" @change="onKnowledgeFileChange" />
        <button class="upload-btn" :disabled="!selectedKnowledgeFile || uploadingKnowledge" @click="uploadKnowledgeFile">
          {{ uploadingKnowledge ? '入库中...' : '上传文档' }}
        </button>
        <p v-if="uploadStatus" class="upload-status">{{ uploadStatus }}</p>
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
          :disabled="currentLoading"
          placeholder="输入你的问题，回车发送（Shift + Enter 换行）"
          @keydown.enter.exact.prevent="runResearch"
        />
        <button class="send-btn" :disabled="currentLoading || !query.trim()" @click="runResearch">
          {{ currentLoading ? '处理中...' : '发送' }}
        </button>
      </div>
      <p v-if="errorMessage" class="error">{{ errorMessage }}</p>
    </main>
  </div>
</template>
