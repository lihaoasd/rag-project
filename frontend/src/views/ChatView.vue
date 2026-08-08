<template>
  <div class="chat-view">
    <div class="page-header">
      <h1>智能问答</h1>
      <p class="desc">基于知识库文档内容进行 AI 问答，支持多轮对话和流式输出</p>
    </div>

    <div class="chat-layout">
      <!-- 对话区 -->
      <div class="chat-main">
        <el-card class="chat-card" :body-style="{ padding: 0, height: '100%', display: 'flex', flexDirection: 'column' }">
          <!-- 消息列表 -->
          <div ref="messagesContainer" class="chat-messages">
            <div v-if="messages.length === 0" class="chat-placeholder">
              <el-empty
                :description="kbId ? '输入问题开始对话' : '请先在右侧选择一个知识库'"
                :image-size="80"
              />
            </div>

            <div
              v-for="(msg, idx) in messages"
              :key="idx"
              :class="['message', msg.role]"
            >
              <div class="message-avatar">
                <el-icon v-if="msg.role === 'user'" :size="18"><User /></el-icon>
                <el-icon v-else :size="18"><Cpu /></el-icon>
              </div>
              <div class="message-body">
                <div class="message-role">{{ msg.role === 'user' ? '你' : 'AI 助手' }}</div>
                <div class="message-content">{{ msg.content }}</div>
                <span v-if="msg.role === 'assistant' && idx === messages.length - 1 && streaming" class="cursor-blink">|</span>
                <!-- 来源引用 -->
                <div v-if="msg.sources && msg.sources.length > 0" class="message-sources">
                  <div class="sources-title">引用来源</div>
                  <div v-for="(src, si) in msg.sources" :key="si" class="source-item">
                    <el-icon><Link /></el-icon>
                    <span>{{ src.file || src.file_name }}</span>
                    <span v-if="src.page">第 {{ src.page }} 页</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 输入区 -->
          <div class="chat-input-area">
            <el-input
              v-model="inputText"
              placeholder="输入您的问题..."
              type="textarea"
              :rows="2"
              :disabled="!kbId"
              resize="none"
              @keyup.enter.exact="handleSend"
            />
            <el-button
              type="primary"
              :disabled="!kbId || !inputText.trim() || streaming"
              :loading="streaming"
              @click="handleSend"
            >
              <template #default>
                <el-icon><Promotion /></el-icon>
                发送
              </template>
            </el-button>
          </div>
        </el-card>
      </div>

      <!-- 侧边信息 -->
      <div class="chat-sidebar">
        <!-- 知识库选择 -->
        <el-card>
          <template #header><span class="sidebar-title">知识库</span></template>
          <div v-if="kbList.length > 0" class="kb-radio-list">
            <el-radio-group v-model="kbId" @change="handleKbChange">
              <div v-for="kb in kbList" :key="kb.id" class="kb-radio-item">
                <el-radio :value="kb.id" :disabled="streaming">
                  {{ kb.name }}
                </el-radio>
                <span class="kb-doc-count">{{ kb.document_count || 0 }} 篇</span>
              </div>
            </el-radio-group>
          </div>
          <el-empty v-else description="暂无知识库" :image-size="40" />
        </el-card>

        <!-- 选中知识库信息 -->
        <el-card v-if="selectedKb" style="margin-top: 12px;">
          <template #header><span class="sidebar-title">知识库信息</span></template>
          <div class="kb-info-list">
            <div class="kb-info-item">
              <span class="label">名称</span>
              <span class="value">{{ selectedKb.name }}</span>
            </div>
            <div class="kb-info-item">
              <span class="label">文档数</span>
              <span class="value">{{ selectedKb.document_count || 0 }}</span>
            </div>
            <div class="kb-info-item">
              <span class="label">创建时间</span>
              <span class="value">{{ formatDate(selectedKb.created_at) }}</span>
            </div>
            <div class="kb-info-item" v-if="selectedKb.description">
              <span class="label">描述</span>
              <span class="value">{{ selectedKb.description }}</span>
            </div>
          </div>
        </el-card>

        <!-- 操作 -->
        <el-card style="margin-top: 12px;">
          <el-button
            type="danger"
            plain
            style="width: 100%"
            :disabled="messages.length === 0"
            @click="clearMessages"
          >
            <el-icon><Delete /></el-icon>
            清空对话
          </el-button>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listKnowledgeBases } from '@/api/knowledgeBase'

// --- State ---
const kbList = ref([])
const kbId = ref(null)
const messages = ref([])
const inputText = ref('')
const streaming = ref(false)
const messagesContainer = ref(null)

// --- Computed ---
const selectedKb = computed(() => {
  return kbList.value.find(k => k.id === kbId.value) || null
})

// --- Methods ---
function formatDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleDateString('zh-CN')
}

function handleKbChange() {
  messages.value = []
}

function clearMessages() {
  messages.value = []
  ElMessage.success('对话已清空')
}

async function scrollToBottom() {
  await nextTick()
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || !kbId.value || streaming.value) return

  // 添加用户消息
  messages.value.push({ role: 'user', content: text })
  inputText.value = ''
  await scrollToBottom()

  // 模拟流式输出（Phase 2 接入真实 SSE 后替换此处）
  streaming.value = true
  const aiMsg = { role: 'assistant', content: '', sources: [] }
  messages.value.push(aiMsg)

  const mockReply = '这是 AI 助手的模拟回复。\n\n当前 Phase 2 RAG 核心链路尚未完成，问答 API 返回 placeholder 数据。\n\n接入真实 SSE 流后，此处的文本将逐字输出，并在回答完成后附上引用来源。\n\n请先完成文档上传、解析和索引后再使用本功能。'

  let i = 0
  const interval = setInterval(async () => {
    if (i < mockReply.length) {
      aiMsg.content += mockReply[i]
      i++
      await scrollToBottom()
    } else {
      clearInterval(interval)
      // 模拟引用来源
      aiMsg.sources = [
        { file: '示例招标书.pdf', page: 12, text: '...投标保证金金额为人民币伍拾万元整...' },
        { file: '示例投标书.docx', page: 5, text: '...项目总预算为人民币贰佰万元整...' },
      ]
      streaming.value = false
      await scrollToBottom()
    }
  }, 30)
}

onMounted(async () => {
  try {
    const data = await listKnowledgeBases()
    kbList.value = data || []
  } catch {
    kbList.value = []
  }
})
</script>

<style scoped>
.chat-view { max-width: var(--page-max-width, 1400px); }

.chat-layout {
  display: flex;
  gap: 16px;
  height: calc(100vh - 200px);
}

.chat-main {
  flex: 1;
  min-width: 0;
  display: flex;
}

.chat-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.chat-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

/* 消息气泡 */
.message {
  display: flex;
  gap: 12px;
  max-width: 80%;
}

.message.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message.assistant {
  align-self: flex-start;
}

.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.message.user .message-avatar {
  background: var(--color-primary);
  color: #fff;
}

.message.assistant .message-avatar {
  background: var(--color-primary-bg);
  color: var(--color-primary);
}

.message-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.message-role {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-bottom: 2px;
}

.message.user .message-role { text-align: right; }

.message-content {
  padding: 12px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.message.user .message-content {
  background: var(--color-primary);
  color: #fff;
  border-bottom-right-radius: 2px;
}

.message.assistant .message-content {
  background: #F1F5F9;
  color: var(--color-text-primary);
  border-bottom-left-radius: 4px;
}

.cursor-blink {
  color: var(--color-primary);
  font-weight: 400;
  animation: blink 1s infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* 来源引用 */
.message-sources {
  margin-top: 8px;
  padding: 10px 14px;
  background: #FFFBEB;
  border-radius: var(--radius-sm);
  border: 1px solid #FDE68A;
  font-size: 12px;
}

.sources-title {
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 6px;
}

.source-item {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--color-text-regular);
  line-height: 1.8;
}

/* 输入区 */
.chat-input-area {
  display: flex;
  gap: 12px;
  padding: 16px 24px;
  border-top: 1px solid var(--color-border);
  background: #fff;
}

.chat-input-area .el-input { flex: 1; }

.chat-input-area .el-button {
  align-self: flex-end;
  height: 40px;
}

/* 侧边栏 */
.chat-sidebar {
  width: 260px;
  flex-shrink: 0;
  overflow-y: auto;
}

.sidebar-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.kb-radio-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.kb-radio-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
}

.kb-doc-count {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.kb-info-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.kb-info-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.kb-info-item .label {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.kb-info-item .value {
  font-size: 13px;
  color: var(--color-text-regular);
  word-break: break-all;
}
</style>
