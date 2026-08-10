<template>
  <div class="qa-wrap">
    <!-- 会话侧边栏（修复#4） -->
    <aside class="qa-side">
      <el-button type="primary" :icon="Plus" class="new-session-btn" @click="newSession">
        新建会话
      </el-button>
      <div v-loading="sessionsLoading" class="session-list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === sessionId }"
          @click="selectSession(s.id)"
        >
          <div class="session-info">
            <div class="session-title">{{ s.title }}</div>
            <div class="session-preview">{{ s.last_preview }}</div>
          </div>
          <el-icon class="session-del" title="删除会话" @click.stop="handleDeleteSession(s)">
            <Close />
          </el-icon>
        </div>
        <el-empty v-if="!sessionsLoading && !sessions.length" description="暂无历史会话" :image-size="60" />
        <el-button
          v-if="sessions.length"
          size="small"
          plain
          type="danger"
          class="clear-btn"
          @click="handleClearSessions"
        >
          清空全部会话
        </el-button>
      </div>
    </aside>

    <!-- 对话面板 -->
    <div class="qa-panel">
      <div class="qa-head">
        <h3 class="qa-title">AI 智能问答</h3>
        <div class="qa-sub">基于企业资料库检索增强生成，回答附带引用来源，可点击查看原文</div>
      </div>

      <div ref="msgListRef" class="msg-list" v-loading="loading">
        <el-empty
          v-if="!messages.length"
          description="输入问题开始提问，例如：公司 VPN 的接入流程是什么？"
          :image-size="80"
        />
        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="msg-row"
          :class="msg.role"
        >
          <div class="msg-avatar">{{ msg.role === 'user' ? '我' : 'AI' }}</div>
          <div class="msg-body">
            <div class="bubble">{{ msg.content }}</div>
            <div v-if="msg.citations && msg.citations.length" class="citations">
              <div class="citations-title">引用来源（点击查看原文）：</div>
              <div
                v-for="(c, ci) in msg.citations"
                :key="ci"
                class="citation-item"
                @click="goDetail(c.document_id)"
              >
                <el-icon><Document /></el-icon>
                <span class="citation-title">{{ c.title }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="qa-input">
        <el-input
          v-model="question"
          type="textarea"
          :rows="3"
          resize="none"
          placeholder="请输入问题，按 Ctrl+Enter 发送"
          @keydown.ctrl.enter="handleSend"
        />
        <div class="qa-input-footer">
          <span class="hint">Ctrl+Enter 发送</span>
          <el-button type="primary" :loading="loading" @click="handleSend">发送</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Close } from '@element-plus/icons-vue'
import { qaApi } from '@/api/modules'

const router = useRouter()

const messages = ref([])
const question = ref('')
const loading = ref(false)
const sessionId = ref(null)
const sessions = ref([])
const sessionsLoading = ref(false)
const msgListRef = ref(null)

/** 滚动到底部 */
async function scrollToBottom() {
  await nextTick()
  const el = msgListRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function fetchSessions() {
  sessionsLoading.value = true
  try {
    const res = await qaApi.listSessions({ page: 1, page_size: 50 })
    sessions.value = res.items || []
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    sessionsLoading.value = false
  }
}

/** 切换会话：加载历史消息（修复#4） */
async function selectSession(id) {
  if (loading.value) return
  sessionId.value = id
  messages.value = []
  loading.value = true
  try {
    const res = await qaApi.getMessages(id)
    messages.value = (res.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
      citations: m.citations || [],
    }))
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

/** 删除单条会话（F21） */
async function handleDeleteSession(s) {
  try {
    await ElMessageBox.confirm(`删除会话「${s.title}」？`, '删除确认', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await qaApi.deleteSession(s.id)
    if (sessionId.value === s.id) newSession()
    await fetchSessions()
    ElMessage.success('已删除')
  } catch (e) {
    /* 拦截器已提示 */
  }
}

/** 清空全部会话（F21） */
async function handleClearSessions() {
  try {
    await ElMessageBox.confirm('确定清空全部问答会话？此操作不可恢复。', '清空确认', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    const res = await qaApi.clearSessions()
    newSession()
    await fetchSessions()
    ElMessage.success(`已清空 ${res.deleted || 0} 条会话`)
  } catch (e) {
    /* 拦截器已提示 */
  }
}

/** 新建会话：清空当前上下文 */
function newSession() {
  if (loading.value) return
  sessionId.value = null
  messages.value = []
}

async function handleSend() {
  const q = question.value.trim()
  if (!q || loading.value) return
  messages.value.push({ role: 'user', content: q })
  question.value = ''
  scrollToBottom()

  loading.value = true
  try {
    const res = await qaApi.ask({
      question: q,
      session_id: sessionId.value || undefined,
    })
    sessionId.value = res.session_id || null
    messages.value.push({
      role: 'assistant',
      content: res.answer,
      citations: res.citations || [],
    })
    await fetchSessions() // 会话列表刷新（新会话/续接）
  } catch (e) {
    // 失败时补一条错误消息，保持对话连续
    messages.value.push({ role: 'assistant', content: '抱歉，回答生成失败，请稍后重试。', citations: [] })
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

function goDetail(id) {
  router.push(`/documents/${id}`)
}

onMounted(() => {
  fetchSessions()
  scrollToBottom()
})
</script>

<style scoped>
.qa-wrap {
  display: flex;
  gap: var(--sp-4);
  max-width: 1180px;
  margin: 0 auto;
  align-items: stretch;
}

/* 会话侧边栏 */
.qa-side {
  width: 240px;
  flex-shrink: 0;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  max-height: 72vh;
}
.new-session-btn {
  width: 100%;
}
.session-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.session-item {
  padding: 10px 12px;
  border-radius: var(--radius);
  cursor: pointer;
  border: 1px solid transparent;
  display: flex;
  align-items: center;
  gap: 6px;
}
.session-info {
  flex: 1;
  min-width: 0;
}
.session-del {
  color: var(--ink-400);
  font-size: 14px;
  flex-shrink: 0;
}
.session-del:hover {
  color: var(--danger);
}
.clear-btn {
  width: 100%;
  margin-top: 4px;
}
.session-item:hover {
  background: var(--fill-2);
}
.session-item.active {
  background: var(--brand-weak);
  border-color: var(--brand-border);
}
.session-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-900);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-preview {
  font-size: 12px;
  color: var(--ink-400);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 对话面板 */
.qa-panel {
  flex: 1;
  min-width: 0;
  background: var(--card);
  border-radius: var(--radius);
  padding: 20px 24px;
  box-shadow: var(--shadow-sm);
}

.qa-sub {
  margin: -8px 0 16px;
  font-size: 13px;
  color: var(--ink-400);
}

.msg-list {
  height: 52vh;
  overflow-y: auto;
  padding: 8px 4px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--fill-2);
}

.msg-row {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}

.msg-row.user {
  justify-content: flex-end;
}

.msg-row.assistant {
  justify-content: flex-start;
}

.msg-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  flex-shrink: 0;
}
.msg-row.user .msg-avatar {
  background: var(--brand);
  color: #fff;
}
.msg-row.assistant .msg-avatar {
  background: var(--fill-2);
  color: var(--brand);
  border: 1px solid var(--brand-border);
}

.msg-body {
  max-width: 76%;
  min-width: 0;
}

.bubble {
  padding: 10px 14px;
  border-radius: var(--radius-lg);
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.msg-row.user .bubble {
  background: var(--brand);
  color: #fff;
  border-top-right-radius: 2px;
}

.msg-row.assistant .bubble {
  background: var(--card);
  color: var(--ink-900);
  border: 1px solid var(--line);
  border-top-left-radius: 2px;
}

.citations {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--line);
}

.citations-title {
  font-size: 12px;
  color: var(--ink-400);
  margin-bottom: 6px;
}

.citation-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  margin-bottom: 4px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  color: var(--brand);
}

.citation-item:hover {
  background: var(--brand-weak);
}

.citation-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qa-input {
  margin-top: 14px;
}

.qa-input-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}

.hint {
  font-size: 12px;
  color: var(--ink-400);
}
</style>
