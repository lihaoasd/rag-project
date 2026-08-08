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
        <el-button type="primary" @click="showUploadDialog">
          <el-icon><Upload /></el-icon>
          上传文档
        </el-button>
      </div>
      <p class="desc">支持的格式：PDF（.pdf）、Word（.docx / .doc），单文件上限 100MB</p>
    </div>

    <!-- 表格 -->
    <el-card v-if="docList.length > 0">
      <el-table :data="docList" stripe style="width: 100%">
        <el-table-column prop="file_name" label="文件名" min-width="200">
          <template #default="{ row }">
            <div class="file-cell">
              <el-icon :size="18" color="#10B981"><Document /></el-icon>
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
        <el-table-column label="操作" width="100" align="center" fixed="right">
          <template #default="{ row }">
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
          <p class="hint">支持 .pdf / .docx / .doc 格式，单文件不超过 100MB</p>
        </div>
      </el-upload>
      <template #footer>
        <el-button @click="uploadDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getKnowledgeBase } from '@/api/knowledgeBase'
import { listDocuments, deleteDocument } from '@/api/document'

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

const uploadUrl = `/api/knowledge-bases/${kbId}/documents`
const uploadHeaders = {}

// --- Methods ---
function statusLabel(s) {
  const map = { uploading: '上传中', processing: '解析中', completed: '就绪', failed: '失败' }
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
  const maxSize = 100 * 1024 * 1024
  if (file.size > maxSize) {
    ElMessage.error('文件大小不能超过 100MB')
    return false
  }
  return true
}

function onUploadSuccess() {
  ElMessage.success('文档上传成功，正在解析中...')
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
  } catch { /* ignore */ }

  try {
    const data = await listDocuments(kbId)
    docList.value = data || []
    total.value = data?.length || 0
  } catch {
    docList.value = []
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

onMounted(() => loadList())
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

.file-cell {
  display: flex;
  align-items: center;
  gap: 8px;
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
