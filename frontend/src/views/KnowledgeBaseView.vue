<template>
  <div class="kb-view">
    <div class="page-header">
      <h1>知识库管理</h1>
      <p class="desc">管理文档知识库，每个知识库内的文档相互隔离</p>
    </div>

    <!-- 操作栏 -->
    <div class="toolbar">
      <el-input
        v-model="searchText"
        placeholder="搜索知识库..."
        :prefix-icon="Search"
        clearable
        style="width: 280px"
      />
      <el-button type="primary" @click="showCreateDialog">
        <el-icon><Plus /></el-icon>
        创建知识库
      </el-button>
    </div>

    <!-- 卡片网格 -->
    <div v-if="filteredList.length > 0" class="kb-grid">
      <el-card
        v-for="kb in filteredList"
        :key="kb.id"
        class="kb-card"
        shadow="hover"
        @click="enterKb(kb.id)"
      >
        <div class="kb-card-body">
          <div class="kb-icon">
            <el-icon :size="32"><Folder /></el-icon>
          </div>
          <div class="kb-info">
            <div class="kb-name">{{ kb.name }}</div>
            <div class="kb-meta">{{ kb.document_count || 0 }} 个文档</div>
            <div class="kb-meta">{{ formatDate(kb.created_at) }}</div>
          </div>
        </div>
        <div class="kb-card-footer" @click.stop>
          <el-button text type="primary" size="small" @click="enterKb(kb.id)">
            进入
          </el-button>
          <el-popconfirm
            title="删除后知识库内所有文档和向量数据将被清除，确定删除？"
            confirm-button-text="确认删除"
            cancel-button-text="取消"
            @confirm="handleDelete(kb.id)"
          >
            <template #reference>
              <el-button text type="danger" size="small">删除</el-button>
            </template>
          </el-popconfirm>
        </div>
      </el-card>
    </div>

    <!-- 空状态 -->
    <el-empty v-else description="暂无知识库，点击上方按钮创建" :image-size="100" />

    <!-- 创建对话框 -->
    <el-dialog
      v-model="dialogVisible"
      title="创建知识库"
      width="480px"
      :close-on-click-modal="false"
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
      >
        <el-form-item label="知识库名称" prop="name">
          <el-input
            v-model="form.name"
            placeholder="请输入知识库名称"
            maxlength="255"
            show-word-limit
          />
        </el-form-item>
        <el-form-item label="描述（可选）" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="3"
            placeholder="简要描述该知识库的用途"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">
          创建
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import { ElMessage, ElNotification } from 'element-plus'
import { listKnowledgeBases, createKnowledgeBase, deleteKnowledgeBase } from '@/api/knowledgeBase'

const router = useRouter()

// --- State ---
const kbList = ref([])
const searchText = ref('')
const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref(null)
const form = ref({ name: '', description: '' })
const rules = {
  name: [
    { required: true, message: '请输入知识库名称', trigger: 'blur' },
    { min: 1, max: 255, message: '长度在 1 到 255 个字符', trigger: 'blur' },
  ],
}

// --- Computed ---
const filteredList = computed(() => {
  if (!searchText.value) return kbList.value
  const kw = searchText.value.toLowerCase()
  return kbList.value.filter(
    k => k.name.toLowerCase().includes(kw) || (k.description || '').toLowerCase().includes(kw)
  )
})

// --- Methods ---
function formatDate(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('zh-CN')
}

function showCreateDialog() {
  form.value = { name: '', description: '' }
  dialogVisible.value = true
}

async function loadList() {
  try {
    const data = await listKnowledgeBases()
    kbList.value = data || []
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElNotification.error({ title: '加载知识库失败', message: msg })
  }
}

async function handleSubmit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    await createKnowledgeBase(form.value)
    ElMessage.success('知识库创建成功')
    dialogVisible.value = false
    await loadList()
  } catch (err) {
    const detail = err?.response?.data?.detail || err?.message || '请稍后重试'
    ElMessage.error('创建失败: ' + detail)
  } finally {
    submitting.value = false
  }
}

async function handleDelete(id) {
  try {
    await deleteKnowledgeBase(id)
    ElMessage.success('知识库已删除')
    await loadList()
  } catch {
    ElMessage.error('删除失败，请稍后重试')
  }
}

function enterKb(id) {
  router.push(`/knowledge-bases/${id}/documents`)
}

onMounted(() => loadList())
</script>

<style scoped>
.kb-view {
  max-width: var(--page-max-width, 1400px);
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.kb-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.kb-card {
  cursor: pointer;
  transition: transform 0.15s;
}
.kb-card:hover { transform: translateY(-2px); }

.kb-card-body {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 4px 0;
}

.kb-icon {
  width: 56px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-primary-bg);
  border-radius: 12px;
  color: var(--color-primary);
  flex-shrink: 0;
}

.kb-info { flex: 1; min-width: 0; }

.kb-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-meta {
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.kb-card-footer {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}
</style>
