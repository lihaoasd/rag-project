<template>
  <div class="model-view">
    <div class="page-header">
      <h1>模型配置</h1>
      <p class="desc">管理多平台、多类型的模型，支持为不同知识库绑定不同模型</p>
    </div>

    <!-- 操作栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <el-radio-group v-model="typeFilter" @change="loadList">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button v-for="t in MODEL_TYPES" :key="t.value" :value="t.value">
            {{ t.label }}
          </el-radio-button>
        </el-radio-group>
      </div>
      <div class="toolbar-right">
        <el-button type="primary" @click="showCreateDialog">
          <el-icon><Plus /></el-icon>
          新建模型
        </el-button>
      </div>
    </div>

    <!-- 列表表格 -->
    <el-card>
      <el-table :data="models" stripe v-loading="loading" style="width: 100%">
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column label="类型" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="typeTagType(row.model_type)" size="small">
              {{ typeLabel(row.model_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="platform" label="平台" width="120" align="center" />
        <el-table-column prop="model" label="模型名" min-width="180" show-overflow-tooltip />
        <el-table-column prop="api_base" label="API Base" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.api_base || '—' }}</template>
        </el-table-column>
        <el-table-column label="默认" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.is_active"
              inline-prompt
              active-text="启"
              inactive-text="停"
              @change="(val) => handleToggleActive(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" align="center" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="showEditDialog(row)">
              编辑
            </el-button>
            <el-button
              v-if="!row.is_default"
              text
              type="success"
              size="small"
              @click="handleSetDefault(row)"
            >
              设为默认
            </el-button>
            <el-popconfirm
              title="确定删除该模型配置？"
              confirm-button-text="删除"
              cancel-button-text="取消"
              @confirm="handleDelete(row)"
            >
              <template #reference>
                <el-button text type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEditing ? '编辑模型' : '新建模型'"
      width="680px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <el-form-item v-if="!isEditing" label="从已有配置复制（可选）">
          <el-select
            v-model="copySourceId"
            placeholder="选择一个已有模型，快速填充下方表单"
            clearable
            filterable
            style="width: 100%"
            @change="handleCopyFrom"
          >
            <el-option
              v-for="m in copyOptions"
              :key="m.id"
              :label="`${m.name}（${typeLabel(m.model_type)} · ${m.model}）`"
              :value="m.id"
            />
          </el-select>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="名称" prop="name">
              <el-input v-model="form.name" placeholder="如：主向量模型" maxlength="100" show-word-limit />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="模型类型" prop="model_type">
              <el-select v-model="form.model_type" placeholder="选择类型" style="width: 100%" :disabled="isEditing">
                <el-option v-for="t in MODEL_TYPES" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="平台" prop="platform">
              <el-input v-model="form.platform" placeholder="如：openai / xinference" />
            </el-form-item>
          </el-col>
          <el-col :span="16">
            <el-form-item label="模型名" prop="model">
              <el-input v-model="form.model" placeholder="如：gpt-4o / bge-m3" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="API Base（可选）">
          <el-input v-model="form.api_base" placeholder="如：http://localhost:9997/v1" />
        </el-form-item>

        <el-form-item label="API Key（可选，支持 ${ENV_VAR} 占位符）">
          <el-input v-model="form.api_key" placeholder="如：sk-xxx 或 ${OPENAI_API_KEY}" type="password" show-password />
        </el-form-item>

        <el-form-item label="参数（JSON，可选）">
          <el-input
            v-model="paramsText"
            type="textarea"
            :rows="4"
            placeholder='如向量模型：{"dimension": 1024}；语言模型：{"temperature": 0.1}；排序模型：{"endpoint": "/reranks"}'
          />
          <div class="form-hint">参数以 JSON 对象保存，留空则使用默认值</div>
        </el-form-item>

        <el-form-item>
          <el-switch v-model="form.is_default" active-text="设为该类型全局默认" />
        </el-form-item>

        <el-form-item>
          <el-switch v-model="form.is_active" active-text="启用该模型（停用后不参与解析与启动自检）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button :loading="testing" @click="handleTest">测试连接</el-button>
          <span class="footer-flex"></span>
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="submitting" @click="handleSubmit">
            {{ isEditing ? '保存' : '创建' }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import { listModels, createModel, updateModel, deleteModel, setDefaultModel, testModel } from '@/api/model'

const MODEL_TYPES = [
  { value: 'llm', label: '语言模型' },
  { value: 'embedding', label: '向量模型' },
  { value: 'rerank', label: '排序模型' },
  { value: 'vision', label: '视觉模型' },
  { value: 'ocr', label: 'OCR 模型' },
]

const TYPE_LABELS = Object.fromEntries(MODEL_TYPES.map(t => [t.value, t.label]))
const TYPE_TAG_TYPES = {
  llm: '',
  embedding: 'success',
  rerank: 'warning',
  vision: 'info',
  ocr: 'danger',
}

// --- State ---
const models = ref([])
const loading = ref(false)
const typeFilter = ref('')
const dialogVisible = ref(false)
const isEditing = ref(false)
const editingId = ref(null)
const submitting = ref(false)
const testing = ref(false)
const copySourceId = ref(null)
const copyOptions = ref([])
const formRef = ref(null)
const paramsText = ref('{}')
const form = ref({
  name: '',
  model_type: 'llm',
  platform: '',
  model: '',
  api_base: '',
  api_key: '',
  is_default: false,
  is_active: true,
})

const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  model_type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  platform: [{ required: true, message: '请输入平台', trigger: 'blur' }],
  model: [{ required: true, message: '请输入模型名', trigger: 'blur' }],
}

// --- Methods ---
function typeLabel(t) { return TYPE_LABELS[t] || t }
function typeTagType(t) { return TYPE_TAG_TYPES[t] || 'info' }

function parseParams(text) {
  const trimmed = (text || '').trim()
  if (!trimmed) return null
  try {
    const obj = JSON.parse(trimmed)
    if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) {
      throw new Error('参数必须是 JSON 对象')
    }
    return obj
  } catch (e) {
    ElMessage.error('参数 JSON 格式错误: ' + e.message)
    return undefined
  }
}

async function loadList() {
  loading.value = true
  try {
    const data = await listModels({ model_type: typeFilter.value || undefined })
    models.value = data || []
  } catch {
    ElNotification.error({ title: '加载失败', message: '无法获取模型配置列表' })
  } finally {
    loading.value = false
  }
}

async function loadCopyOptions() {
  try {
    copyOptions.value = (await listModels()) || []
  } catch {
    copyOptions.value = []
  }
}

function resetForm() {
  form.value = {
    name: '',
    model_type: 'llm',
    platform: '',
    model: '',
    api_base: '',
    api_key: '',
    is_default: false,
    is_active: true,
  }
  paramsText.value = '{}'
  copySourceId.value = null
}

function showCreateDialog() {
  resetForm()
  isEditing.value = false
  editingId.value = null
  dialogVisible.value = true
  loadCopyOptions()
}

function handleCopyFrom(id) {
  if (!id) return
  const src = copyOptions.value.find((m) => m.id === id)
  if (!src) return
  form.value = {
    name: '',
    model_type: src.model_type,
    platform: src.platform,
    model: src.model,
    api_base: src.api_base || '',
    api_key: src.api_key || '',
    is_default: false,
    is_active: true,
  }
  paramsText.value = src.params ? JSON.stringify(src.params, null, 2) : '{}'
  ElMessage.success(`已复制「${src.name}」的配置，请填写名称后保存`)
}

function showEditDialog(row) {
  isEditing.value = true
  editingId.value = row.id
  form.value = {
    name: row.name,
    model_type: row.model_type,
    platform: row.platform,
    model: row.model,
    api_base: row.api_base || '',
    api_key: row.api_key || '',
    is_default: row.is_default,
    is_active: row.is_active,
  }
  paramsText.value = row.params ? JSON.stringify(row.params, null, 2) : '{}'
  dialogVisible.value = true
}

async function handleSubmit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  const params = parseParams(paramsText.value)
  if (params === undefined) return

  submitting.value = true
  try {
    const payload = {
      name: form.value.name,
      model_type: form.value.model_type,
      platform: form.value.platform,
      model: form.value.model,
      api_base: form.value.api_base || null,
      api_key: form.value.api_key || null,
      params,
      is_default: form.value.is_default,
      is_active: form.value.is_active,
    }

    if (isEditing.value) {
      await updateModel(editingId.value, payload)
      ElMessage.success('模型更新成功')
    } else {
      await createModel(payload)
      ElMessage.success('模型创建成功')
    }
    dialogVisible.value = false
    await loadList()
  } catch (err) {
    const detail = err?.response?.data?.detail || err?.message || '请稍后重试'
    ElMessage.error('操作失败: ' + detail)
  } finally {
    submitting.value = false
  }
}

async function handleTest() {
  if (!form.value.platform || !form.value.model) {
    ElMessage.warning('请先填写平台和模型名')
    return
  }
  const params = parseParams(paramsText.value)
  if (params === undefined) return

  testing.value = true
  try {
    const res = await testModel({
      model_type: form.value.model_type,
      platform: form.value.platform,
      model: form.value.model,
      api_base: form.value.api_base || null,
      api_key: form.value.api_key || null,
      params,
    })
    if (res.success) {
      const extra = res.detail ? `${res.message} · ${res.detail}` : res.message
      const latency = res.latency_ms != null ? `（${res.latency_ms}ms）` : ''
      ElNotification.success({ title: '连接成功', message: `${extra}${latency}`, duration: 5000 })
    } else {
      ElNotification.error({ title: '连接失败', message: res.message, duration: 5000 })
    }
  } catch (err) {
    const detail = err?.response?.data?.detail || err?.message || '测试失败'
    ElNotification.error({ title: '测试失败', message: detail })
  } finally {
    testing.value = false
  }
}

async function handleSetDefault(row) {
  try {
    await setDefaultModel(row.id)
    ElMessage.success('已设为默认')
    await loadList()
  } catch (err) {
    const detail = err?.response?.data?.detail || '操作失败'
    ElMessage.error(detail)
  }
}

async function handleToggleActive(row, val) {
  try {
    await updateModel(row.id, { is_active: val })
    ElMessage.success(val ? '模型已启用' : '模型已停用')
    await loadList()
  } catch (err) {
    const detail = err?.response?.data?.detail || '操作失败'
    ElMessage.error(detail)
  }
}

async function handleDelete(row) {
  try {
    await deleteModel(row.id)
    ElMessage.success('模型已删除')
    await loadList()
  } catch (err) {
    const detail = err?.response?.data?.detail || '删除失败'
    ElMessage.error(detail)
  }
}

onMounted(() => loadList())
</script>

<style scoped>
.model-view {
  max-width: var(--page-max-width, 1400px);
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.text-muted {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.form-hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.dialog-footer {
  display: flex;
  align-items: center;
}

.footer-flex {
  flex: 1;
}
</style>
