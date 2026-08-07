<template>
  <div class="chat-view">
    <div class="page-header">
      <h1>智能问答</h1>
      <p class="desc">基于知识库文档内容进行AI问答，支持多轮对话</p>
    </div>

    <div class="chat-layout">
      <!-- 对话区域 -->
      <div class="chat-main">
        <el-card class="chat-card">
          <div class="chat-messages" ref="messagesContainer">
            <el-empty description="输入问题开始对话" :image-size="80" />
          </div>
          <div class="chat-input">
            <el-input
              v-model="message"
              placeholder="输入您的问题..."
              type="textarea"
              :rows="3"
              @keyup.enter.exact="handleSend"
            />
            <el-button
              type="primary"
              class="send-btn"
              @click="handleSend"
            >
              发送
            </el-button>
          </div>
        </el-card>
      </div>

      <!-- 知识库选择 -->
      <div class="chat-sidebar">
        <el-card>
          <template #header><span>当前知识库</span></template>
          <el-empty description="请选择知识库" :image-size="60" />
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const message = ref('')

const handleSend = () => {
  // Phase 2: 调用问答 SSE API
}
</script>

<style scoped>
.chat-view { max-width: 1400px; }
.page-header { margin-bottom: 24px; }
.page-header h1 { font-size: 24px; color: #303133; margin-bottom: 8px; }
.page-header .desc { color: #909399; font-size: 14px; }
.chat-layout { display: flex; gap: 16px; }
.chat-main { flex: 1; }
.chat-sidebar { width: 260px; flex-shrink: 0; }
.chat-card { height: calc(100vh - 200px); display: flex; flex-direction: column; }
.chat-messages { flex: 1; overflow-y: auto; padding: 16px; }
.chat-input { padding: 16px; border-top: 1px solid #e4e7ed; display: flex; gap: 12px; align-items: flex-end; }
.send-btn { height: 40px; }
</style>
