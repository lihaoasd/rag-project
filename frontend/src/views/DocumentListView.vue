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
        <span class="current">{{ kbName || '文档管理' }}</span>
      </div>
      <div class="header-row">
        <h1>{{ kbName || '文档管理' }}</h1>
        <div class="header-actions">
          <el-button @click="$router.push(`/knowledge-bases/${kbId}/graph`)">
            <el-icon><Share /></el-icon>
            查看图谱
          </el-button>
          <el-button type="primary" @click="showUploadDialog">
            <el-icon><Upload /></el-icon>
            上传文档
          </el-button>
        </div>
      </div>
      <p class="desc">支持的格式：PDF（.pdf）、Word（.docx / .doc），单文件上限 500MB</p>
    </div>

    <!-- 表格 -->
    <el-card v-if="docList.length > 0">
      <el-table :data="docList" stripe style="width: 100%">
        <el-table-column prop="file_name" label="文件名" min-width="200">
          <template #default="{ row }">
            <div class="file-cell">
              <el-icon :size="18" color="var(--color-primary)"><Document /></el-icon>
              <span>{{ row.file_name }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="100" align="right">
          <template #default="{ row }">
            {{ formatSize(row.file_size) }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag
              :class="`status-tag ${row.status}`"
              size="small"
              disable-transitions
            >
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="图谱" width="100" align="center">
          <template #default="{ row }">
            <el-tag
              v-if="row.graph_status === 'completed'"
              type="success"
              size="small"
              effect="plain"
            >
              已建图
            </el-tag>
            <el-tag
              v-else-if="row.graph_status === 'building'"
              type="warning"
              size="small"
              effect="plain"
            >
              构建中
            </el-tag>
            <el-tag
              v-else-if="row.graph_status === 'failed'"
              type="danger"
              size="small"
              effect="plain"
            >
              建图失败
            </el-tag>
            <span v-else class="graph-none">未建</span>
          </template>
        </el-table-column>
        <el-table-column label="已入库" width="100" align="center">
          <template #default="{ row }">
            {{ row.processed_chunk_count ?? '-' }}
          </template>
        </el-table-column>
        <el-table-column label="解析耗时" width="110" align="center">
          <template #default="{ row }">
            {{ formatDuration(row.parse_duration_ms) }}
          </template>
        </el-table-column>
        <el-table-column label="页数" width="80" align="center">
          <template #default="{ row }">
            {{ row.page_count ?? '-' }}
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="170">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="300" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'uploaded' || row.status === 'failed'"
              text
              type="primary"
              size="small"
              :loading="row._parsing"
              @click="handleParse(row)"
            >
              解析
            </el-button>
            <el-button
              v-if="row.status === 'partial'"
              text
              type="primary"
              size="small"
              :loading="row._resuming"
              @click="handleResume(row)"
            >
              继续解析
            </el-button>
            <el-button
              v-if="row.status === 'completed' || row.status === 'partial' || (row.status === 'failed' && (row.processed_chunk_count ?? 0) > 0)"
              text
              type="primary"
              size="small"
              @click="$router.push(`/knowledge-bases/${kbId}/documents/${row.id}/chunks`)"
            >
              切片
            </el-button>
            <el-button
              v-if="row.status === 'completed'"
              text
              type="success"
              size="small"
              :loading="row._buildingGraph"
              @click="handleBuildGraph(row)"
            >
              {{ row.graph_status === 'completed' ? '重建图' : '建图' }}
            </el-button>
            <el-button
              v-if="row.status === 'completed' && (row.graph_status === 'completed' || row.graph_status === 'failed')"
              text
              type="warning"
              size="small"
              :loading="row._clearingGraph"
              @click="handleClearGraph(row)"
            >
              清图
            </el-button>
            <el-button
              v-if="row.status === 'completed' || row.status === 'failed' || row.status === 'partial'"
              text
              type="warning"
              size="small"
              :loading="row._reparsing"
              @click="handleReparse(row)"
            >
              重解析
            </el-button>
            <el-popconfirm
              title="删除后文档的向量数据也将被清除，确定删除？"
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
      <el-empty description="暂无文档，点击上方按钮上传" :image-size="100">
        <el-button type="primary" @click="showUploadDialog">
          上传第一篇文档
        </el-button>
      </el-empty>
    </el-card>

    <!-- 上传对话框 -->
    <el-dialog
      v-model="uploadDialogVisible"
      title="上传文档"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-upload
        ref="uploadRef"
        class="upload-area"
        drag
        :action="uploadUrl"
        :headers="uploadHeaders"
        :accept="'.pdf,.docx,.doc'"
        :before-upload="beforeUpload"
        :on-success="onUploadSuccess"
        :on-error="onUploadError"
        :limit="1"
        :file-list="fileList"
      >
        <el-icon :size="48" color="#C0C4CC"><UploadFilled /></el-icon>
        <div class="upload-text">
          <p>将文件拖到此处，或<em>点击选择</em></p>
          <p class="hint">支持 .pdf / .docx / .doc 格式，单文件不超过 500MB</p>
        </div>
      </el-upload>
      <template #footer>
        <el-button @click="uploadDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElNotification, ElMessageBox } from 'element-plus'
import { getKnowledgeBase } from '@/api/knowledgeBase'
import { listDocuments, deleteDocument, parseDocument, reparseDocument, resumeDocument } from '@/api/document'
import { buildDocGraph, clearDocGraph } from '@/api/graph'

const route = useRoute()
const kbId = route.params.kbId

// --- State ---
const kbName = ref('')
const docList = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = 10
const uploadDialogVisible = ref(false)
const fileList = ref([])
let pollTimer = null

const uploadUrl = `/api/knowledge-bases/${kbId}/documents`
const uploadHeaders = {}

// --- Methods ---
function statusLabel(s) {
  const map = {
    uploaded: '待解析',
    queued: '排队中',
    uploading: '上传中',
    processing: '解析中',
    completed: '就绪',
    failed: '失败',
    partial: '部分成功',
  }
  return map[s] || s
}

function formatSize(bytes) {
  if (!bytes) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function formatDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

function formatDuration(ms) {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function showUploadDialog() {
  fileList.value = []
  uploadDialogVisible.value = true
}

function beforeUpload(file) {
  const validTypes = ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/msword']
  if (!validTypes.includes(file.type) && !file.name.match(/\.(pdf|docx|doc)$/i)) {
    ElMessage.error('仅支持 PDF、DOCX、DOC 格式')
    return false
  }
  const maxSize = 500 * 1024 * 1024
  if (file.size > maxSize) {
    ElMessage.error('文件大小不能超过 500MB')
    return false
  }
  return true
}

function onUploadSuccess() {
  ElMessage.success('上传成功，请点击"解析"按钮开始解析文档')
  uploadDialogVisible.value = false
  loadList()
}

function onUploadError(err) {
  ElMessage.error('上传失败: ' + (err?.message || '未知错误'))
}

function onPageChange(page) {
  currentPage.value = page
  loadList()
}

async function loadList() {
  try {
    const data = await getKnowledgeBase(kbId)
    kbName.value = data?.name || ''
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElNotification.error({ title: '加载知识库信息失败', message: msg })
  }

  try {
    const data = await listDocuments(kbId)
    // 保留各行的 _parsing 标记
    const oldMap = new Map(docList.value.map((d) => [d.id, d._parsing]))
    docList.value = (data || []).map((d) => ({
      ...d,
      _parsing: oldMap.get(d.id) || false,
    }))
    total.value = data?.length || 0

    // 检查是否需要轮询
    managePolling()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElNotification.error({ title: '加载文档列表失败', message: msg })
  }
}

async function handleParse(row) {
  row._parsing = true
  try {
    await parseDocument(kbId, row.id)
    ElMessage.success('解析已触发，正在后台处理...')
    await loadList()
  } catch (err) {
    row._parsing = false
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('解析失败: ' + msg)
  }
}

async function handleResume(row) {
  row._resuming = true
  try {
    await resumeDocument(kbId, row.id)
    ElMessage.success('继续解析已触发，正在后台续跑...')
    await loadList()
  } catch (err) {
    row._resuming = false
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('继续解析失败: ' + msg)
  }
}

async function handleReparse(row) {
  row._reparsing = true
  try {
    await reparseDocument(kbId, row.id)
    ElMessage.success('重新解析已触发，正在后台处理...')
    await loadList()
  } catch (err) {
    row._reparsing = false
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('重新解析失败: ' + msg)
  }
}

async function handleBuildGraph(row) {
  row._buildingGraph = true
  try {
    await buildDocGraph(kbId, row.id)
    ElMessage.success('图谱构建已触发，正在后台处理...')
    await loadList()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('图谱构建失败: ' + msg)
  } finally {
    row._buildingGraph = false
  }
}

async function handleClearGraph(row) {
  try {
    await ElMessageBox.confirm('将清除该文件对知识图谱的全部贡献（其他文件贡献的实体不受影响），确定？', '清除单文件图谱', {
      confirmButtonText: '确认清除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  row._clearingGraph = true
  try {
    await clearDocGraph(kbId, row.id)
    ElMessage.success('该文件图谱已清除')
    await loadList()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('清除图谱失败: ' + msg)
  } finally {
    row._clearingGraph = false
  }
}

async function handleDelete(docId) {
  try {
    await deleteDocument(kbId, docId)
    ElMessage.success('文档已删除')
    await loadList()
  } catch {
    ElMessage.error('删除失败，请稍后重试')
  }
}

// --- 轮询：存在 processing 状态的文档或图谱构建中时自动刷新 ---
function hasProcessing() {
  return docList.value.some(
    (d) => d.status === 'processing' || d.status === 'queued' || d.graph_status === 'building'
  )
}

function managePolling() {
  if (hasProcessing()) {
    if (!pollTimer) {
      pollTimer = setInterval(loadList, 3000)
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

// 监听 docList 变化以管理轮询
watch(docList, managePolling, { deep: true })

onMounted(() => loadList())

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

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.desc {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.file-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.graph-none {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.upload-area { width: 100%; }

.upload-text { text-align: center; }

.upload-text p {
  font-size: 14px;
  color: var(--color-text-regular);
  margin-top: 8px;
}

.upload-text p em {
  color: var(--color-primary);
  font-style: normal;
  cursor: pointer;
}

.upload-text .hint {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-top: 4px;
}
</style>
