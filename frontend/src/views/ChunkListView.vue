<template>
  <div class="doc-view">
    <!-- 面包屑 -->
    <div class="page-header">
      <div class="breadcrumb">
        <el-button text type="primary" @click="$router.push('/knowledge-bases')">
          <el-icon><ArrowLeft /></el-icon>
          返回知识库列表
        </el-button>
        <span class="sep">/</span>
        <el-button text type="primary" @click="$router.push(`/knowledge-bases/${kbId}/documents`)">
          {{ kbName || '文档列表' }}
        </el-button>
        <span class="sep">/</span>
        <span class="current">{{ docFileName || '切片管理' }}</span>
      </div>
      <div class="header-row">
        <h1>{{ docFileName || '切片管理' }}</h1>
        <el-button type="warning" @click="showRechunkDialog">
          <el-icon><RefreshRight /></el-icon>
          重新切片
        </el-button>
      </div>
      <p class="desc">
        共 {{ total }} 个切片
        <span v-if="docPageCount !== null"> · {{ docPageCount }} 页</span>
        · 支持查看、编辑、删除单个切片
      </p>
    </div>

    <!-- 表格 -->
    <el-card v-if="chunkList.length > 0">
      <el-table :data="chunkList" stripe style="width: 100%">
        <el-table-column prop="chunk_index" label="序号" width="70" align="center" />
        <el-table-column label="页码" width="70" align="center">
          <template #default="{ row }">
            {{ row.page_number }}
          </template>
        </el-table-column>
        <el-table-column label="字符数" width="90" align="right">
          <template #default="{ row }">
            {{ row.char_count.toLocaleString() }}
          </template>
        </el-table-column>
        <el-table-column label="文本预览" min-width="300">
          <template #default="{ row }">
            <span class="text-preview">{{ row.text_preview }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" align="center" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="handleView(row)">
              查看
            </el-button>
            <el-button text type="primary" size="small" @click="handleEdit(row)">
              编辑
            </el-button>
            <el-popconfirm
              title="确定删除该切片？"
              confirm-button-text="确认"
              cancel-button-text="取消"
              @confirm="handleDelete(row.id)"
            >
              <template #reference>
                <el-button text type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-if="total > pageSize"
        :current-page="currentPage"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="onPageChange"
      />
    </el-card>

    <!-- 空状态 -->
    <el-card v-else>
      <el-empty description="该文档暂无切片" :image-size="100">
        <span style="color: var(--color-text-secondary); font-size: 13px;">
          请先对文档执行解析操作
        </span>
      </el-empty>
    </el-card>

    <!-- 查看详情对话框 -->
    <el-dialog
      v-model="detailVisible"
      title="切片详情"
      width="640px"
      :close-on-click-modal="false"
    >
      <div v-if="detailChunk" class="chunk-detail">
        <div class="detail-meta">
          <span>序号: {{ detailChunk.chunk_index }}</span>
          <span>页码: {{ detailChunk.page_number }}</span>
          <span>字符数: {{ detailChunk.char_count.toLocaleString() }}</span>
        </div>
        <el-input
          type="textarea"
          :rows="10"
          :model-value="detailChunk.text_content"
          readonly
          resize="none"
        />
      </div>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 编辑对话框 -->
    <el-dialog
      v-model="editVisible"
      title="编辑切片"
      width="640px"
      :close-on-click-modal="false"
      @closed="editText = ''"
    >
      <el-input
        type="textarea"
        :rows="12"
        v-model="editText"
        placeholder="请输入新的切片文本..."
        resize="vertical"
      />
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editSubmitting" @click="handleSaveEdit">
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 重切片对话框 -->
    <el-dialog
      v-model="rechunkVisible"
      title="重新切片"
      width="480px"
      :close-on-click-modal="false"
    >
      <el-form label-width="140px">
        <el-form-item label="切片大小 (tokens)">
          <el-input-number
            v-model="rechunkForm.chunk_size"
            :min="128"
            :max="4096"
            :step="64"
          />
          <span class="form-hint">默认 512</span>
        </el-form-item>
        <el-form-item label="切片重叠 (tokens)">
          <el-input-number
            v-model="rechunkForm.chunk_overlap"
            :min="0"
            :max="512"
            :step="16"
          />
          <span class="form-hint">默认 64</span>
        </el-form-item>
      </el-form>
      <p class="rechunk-warning">
        <el-icon><WarningFilled /></el-icon>
        重新切片将清除当前所有切片并重新解析，此操作不可撤销。
      </p>
      <template #footer>
        <el-button @click="rechunkVisible = false">取消</el-button>
        <el-button type="warning" :loading="rechunkSubmitting" @click="handleRechunk">
          确认重新切片
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElNotification } from 'element-plus'
import { getKnowledgeBase } from '@/api/knowledgeBase'
import { getDocument } from '@/api/document'
import {
  listChunks,
  getChunk,
  deleteChunk,
  updateChunk,
  rechunkDocument,
} from '@/api/chunk'

const route = useRoute()
const kbId = route.params.kbId
const docId = route.params.docId

// --- State ---
const kbName = ref('')
const docFileName = ref('')
const docPageCount = ref(null)
const chunkList = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = 20

// Detail dialog
const detailVisible = ref(false)
const detailChunk = ref(null)

// Edit dialog
const editVisible = ref(false)
const editChunkId = ref('')
const editText = ref('')
const editSubmitting = ref(false)

// Rechunk dialog
const rechunkVisible = ref(false)
const rechunkForm = ref({ chunk_size: 512, chunk_overlap: 64 })
const rechunkSubmitting = ref(false)

let pollTimer = null

// --- Methods ---
async function loadPage() {
  // 加载知识库名称
  try {
    const kb = await getKnowledgeBase(kbId)
    kbName.value = kb?.name || ''
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElNotification.error({ title: '加载知识库信息失败', message: msg })
  }

  // 加载文档信息
  try {
    const doc = await getDocument(kbId, docId)
    docFileName.value = doc?.file_name || ''
    docPageCount.value = doc?.page_count ?? null

    // 检查文档是否在 processing 状态（rechunking）
    managePollingForDoc(doc)
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElNotification.error({ title: '加载文档信息失败', message: msg })
  }

  // 加载切片列表
  try {
    const data = await listChunks(kbId, docId, currentPage.value, pageSize)
    chunkList.value = data?.items || []
    total.value = data?.total || 0
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElNotification.error({ title: '加载切片列表失败', message: msg })
  }
}

function managePollingForDoc(doc) {
  if (doc && (doc.status === 'processing' || doc.status === 'queued')) {
    if (!pollTimer) {
      pollTimer = setInterval(loadPage, 3000)
    }
  } else {
    stopPolling()
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function onPageChange(page) {
  currentPage.value = page
  loadPage()
}

async function handleView(row) {
  try {
    detailChunk.value = await getChunk(kbId, docId, row.id)
    detailVisible.value = true
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('加载切片详情失败: ' + msg)
  }
}

async function handleEdit(row) {
  try {
    const chunk = await getChunk(kbId, docId, row.id)
    editChunkId.value = chunk.id
    editText.value = chunk.text_content
    editVisible.value = true
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('加载切片失败: ' + msg)
  }
}

async function handleSaveEdit() {
  editSubmitting.value = true
  try {
    await updateChunk(kbId, docId, editChunkId.value, editText.value)
    ElMessage.success('切片已更新')
    editVisible.value = false
    await loadPage()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('更新失败: ' + msg)
  } finally {
    editSubmitting.value = false
  }
}

async function handleDelete(chunkId) {
  try {
    await deleteChunk(kbId, docId, chunkId)
    ElMessage.success('切片已删除')
    await loadPage()
  } catch {
    ElMessage.error('删除失败，请稍后重试')
  }
}

function showRechunkDialog() {
  rechunkForm.value = { chunk_size: 512, chunk_overlap: 64 }
  rechunkVisible.value = true
}

async function handleRechunk() {
  rechunkSubmitting.value = true
  try {
    await rechunkDocument(
      kbId,
      docId,
      rechunkForm.value.chunk_size,
      rechunkForm.value.chunk_overlap,
    )
    ElMessage.success('重切片已触发，正在后台处理...')
    rechunkVisible.value = false
    await loadPage()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('重切片失败: ' + msg)
  } finally {
    rechunkSubmitting.value = false
  }
}

onMounted(() => loadPage())
onBeforeUnmount(() => stopPolling())
</script>

<style scoped>
.doc-view { max-width: var(--page-max-width, 1400px); }

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-bottom: 12px;
}

.breadcrumb .sep { color: var(--color-border); }

.breadcrumb .current {
  color: var(--color-text-primary);
  font-weight: 500;
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.header-row h1 {
  font-size: 22px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.desc {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.text-preview {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  color: var(--color-text-regular);
  font-size: 13px;
  line-height: 1.5;
}

.chunk-detail .detail-meta {
  display: flex;
  gap: 20px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.rechunk-warning {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #D97706;
  margin-top: 8px;
}
</style>
