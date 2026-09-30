<template>
  <div class="model-view">
    <div class="page-header">
      <h1>模型配置</h1>
      <p class="desc">管理供应商与多平台、多类型的模型，支持为不同知识库绑定不同模型</p>
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
        <el-button @click="showProviderDialog">
          <el-icon><Coin /></el-icon>
          供应商管理
        </el-button>
        <el-button type="primary" @click="showCreateDialog">
          <el-icon><Plus /></el-icon>
          新建模型
        </el-button>
      </div>
    </div>

    <!-- 列表表格 -->
    <el-card>
      <el-table :data="models" stripe v-loading="loading" style="width: 100%">
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column label="类型" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="typeTagType(row.model_type)" size="small">
              {{ typeLabel(row.model_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="供应商" width="130" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.provider_name" type="primary" size="small" effect="plain">
              {{ row.provider_name }}
            </el-tag>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="platform" label="平台" width="110" align="center" />
        <el-table-column prop="model" label="模型名" min-width="170" show-overflow-tooltip />
        <el-table-column prop="api_base" label="API Base" min-width="190" show-overflow-tooltip>
          <template #default="{ row }">{{ row.api_base || '—' }}</template>
        </el-table-column>
        <el-table-column label="默认" width="80" align="center">
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

    <!-- 创建/编辑模型对话框 -->
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

        <el-form-item label="所属供应商（可选，连接信息默认继承供应商）">
          <el-select
            v-model="form.provider_id"
            placeholder="选择供应商，API 地址/Key 留空则继承其配置"
            clearable
            filterable
            style="width: 100%"
            @change="handleProviderSelect"
          >
            <el-option
              v-for="p in providerOptions"
              :key="p.id"
              :label="`${p.name}（${p.platform}${p.api_base ? ' · ' + p.api_base : ''}）`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>

        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="平台" prop="platform">
              <el-input v-model="form.platform" placeholder="挂供应商后自动填充" />
            </el-form-item>
          </el-col>
          <el-col :span="16">
            <el-form-item label="模型名" prop="model">
              <el-input v-model="form.model" placeholder="如：gpt-4o / bge-m3" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="API Base（可选）">
          <el-input
            v-model="form.api_base"
            :placeholder="form.provider_id ? '留空则继承供应商配置' : '如：http://localhost:9997/v1'"
          />
        </el-form-item>

        <el-form-item label="API Key（可选，支持 ${ENV_VAR} 占位符）">
          <el-input
            v-model="form.api_key"
            :placeholder="form.provider_id ? '留空则继承供应商配置' : '如：sk-xxx 或 ${OPENAI_API_KEY}'"
            type="password"
            show-password
          />
        </el-form-item>

        <el-form-item label="参数（JSON，可选）">
          <el-input
            v-model="paramsText"
            type="textarea"
            :rows="4"
            placeholder='如向量模型：{"dimension": 1024}；语言模型：{"temperature": 0.1}；排序模型：{"endpoint": "/reranks"}'
          />
          <div class="form-hint">参数以 JSON 对象保存，留空则使用默认值；挂供应商且留空时继承供应商参数</div>
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

    <!-- 供应商管理对话框 -->
    <el-dialog
      v-model="providerDialogVisible"
      title="供应商管理"
      width="760px"
      :close-on-click-modal="false"
    >
      <div class="provider-toolbar">
        <p class="form-hint">一个供应商下可挂多个模型，统一维护 API 地址 / Key；模型连接信息默认继承供应商，可单独覆盖。</p>
        <el-button type="primary" size="small" @click="showProviderCreateDialog">
          <el-icon><Plus /></el-icon>
          新建供应商
        </el-button>
      </div>
      <el-table :data="providers" stripe v-loading="providerLoading" style="width: 100%">
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="platform" label="平台" width="110" align="center" />
        <el-table-column prop="api_base" label="API Base" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.api_base || '—' }}</template>
        </el-table-column>
        <el-table-column prop="model_count" label="模型数" width="80" align="center" />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" align="center" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="showFetchDialog(row)">
              拉取模型
            </el-button>
            <el-button text type="primary" size="small" @click="showProviderEditDialog(row)">
              编辑
            </el-button>
            <el-popconfirm
              title="确定删除该供应商？"
              confirm-button-text="删除"
              cancel-button-text="取消"
              @confirm="handleProviderDelete(row)"
            >
              <template #reference>
                <el-button text type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 新建/编辑供应商对话框 -->
    <el-dialog
      v-model="providerEditVisible"
      :title="providerEditing ? '编辑供应商' : '新建供应商'"
      width="520px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form ref="providerFormRef" :model="providerForm" :rules="providerRules" label-position="top">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="名称" prop="name">
              <el-input v-model="providerForm.name" placeholder="如：DeepSeek / 智谱 / 本地 Xinference" maxlength="100" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="平台" prop="platform">
              <el-input v-model="providerForm.platform" placeholder="如：deepseek / openai / zhipu / xinference" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="API Base（可选）">
          <el-input v-model="providerForm.api_base" placeholder="如：https://api.deepseek.com/v1" />
        </el-form-item>
        <el-form-item label="API Key（可选，支持 ${ENV_VAR} 占位符）">
          <el-input v-model="providerForm.api_key" placeholder="如：sk-xxx 或 ${DEEPSEEK_API_KEY}" type="password" show-password />
        </el-form-item>
        <el-form-item>
          <el-switch v-model="providerForm.is_active" active-text="启用该供应商（停用后其下模型不继承连接信息）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <span class="footer-flex"></span>
          <el-button @click="providerEditVisible = false">取消</el-button>
          <el-button type="primary" :loading="providerSubmitting" @click="handleProviderSubmit">
            {{ providerEditing ? '保存' : '创建' }}
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 拉取供应商模型对话框 -->
    <el-dialog
      v-model="fetchDialogVisible"
      :title="`拉取模型 · ${fetchProvider?.name || ''}`"
      width="620px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <div class="fetch-toolbar">
        <div class="fetch-left">
          <el-select
            v-model="fetchModelType"
            placeholder="选择类型"
            style="width: 180px"
            :disabled="fetchAdding"
          >
            <el-option v-for="t in MODEL_TYPES" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
          <el-button text type="primary" size="small" :loading="fetchLoading" @click="loadFetchModels">
            重新拉取
          </el-button>
        </div>
        <span class="form-hint">从该供应商 /models 端点获取，勾选后按所选类型批量添加</span>
      </div>
      <el-table
        ref="fetchTableRef"
        :data="fetchModels"
        v-loading="fetchLoading"
        style="width: 100%"
        height="320"
        @selection-change="handleFetchSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column prop="id" label="模型 ID" min-width="200" show-overflow-tooltip />
        <el-table-column prop="owned_by" label="归属" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.owned_by || '—' }}</template>
        </el-table-column>
      </el-table>
      <template #footer>
        <div class="dialog-footer">
          <span class="form-hint">{{ fetchModels.length }} 个可用 · 已选 {{ selectedFetchModels.length }} 个</span>
          <span class="footer-flex"></span>
          <el-button @click="fetchDialogVisible = false" :disabled="fetchAdding">关闭</el-button>
          <el-button
            type="primary"
            :loading="fetchAdding"
            :disabled="selectedFetchModels.length === 0"
            @click="handleBatchAdd"
          >
            添加所选（{{ selectedFetchModels.length }}）
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import {
  listModels, createModel, updateModel, deleteModel, setDefaultModel, testModel,
  listProviders, createProvider, updateProvider, deleteProvider, fetchProviderModels,
} from '@/api/model'

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

// --- Model State ---
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
const providerOptions = ref([])
const formRef = ref(null)
const paramsText = ref('{}')
const form = ref({
  name: '',
  model_type: 'llm',
  platform: '',
  model: '',
  api_base: '',
  api_key: '',
  provider_id: '',
  is_default: false,
  is_active: true,
})

// --- Provider State ---
const providers = ref([])
const providerLoading = ref(false)
const providerDialogVisible = ref(false)
const providerEditVisible = ref(false)
const providerEditing = ref(false)
const providerEditingId = ref(null)
const providerSubmitting = ref(false)
const providerFormRef = ref(null)
const providerForm = ref({
  name: '',
  platform: '',
  api_base: '',
  api_key: '',
  is_active: true,
})

// --- Fetch Models State ---
const fetchDialogVisible = ref(false)
const fetchProvider = ref(null)
const fetchLoading = ref(false)
const fetchAdding = ref(false)
const fetchModels = ref([])
const selectedFetchModels = ref([])
const fetchModelType = ref('llm')
const fetchTableRef = ref(null)

const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  model_type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  platform: [{ required: true, message: '请输入平台', trigger: 'blur' }],
  model: [{ required: true, message: '请输入模型名', trigger: 'blur' }],
}

const providerRules = {
  name: [{ required: true, message: '请输入供应商名称', trigger: 'blur' }],
  platform: [{ required: true, message: '请输入平台', trigger: 'blur' }],
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

async function loadProviderOptions() {
  try {
    providerOptions.value = (await listProviders()) || []
  } catch {
    providerOptions.value = []
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
    provider_id: '',
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
  loadProviderOptions()
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
    provider_id: src.provider_id || '',
    is_default: false,
    is_active: true,
  }
  paramsText.value = src.params ? JSON.stringify(src.params, null, 2) : '{}'
  ElMessage.success(`已复制「${src.name}」的配置，请填写名称后保存`)
}

function handleProviderSelect(id) {
  if (!id) return
  const p = providerOptions.value.find((x) => x.id === id)
  if (!p) return
  if (!form.value.platform) form.value.platform = p.platform
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
    provider_id: row.provider_id || '',
    is_default: row.is_default,
    is_active: row.is_active,
  }
  paramsText.value = row.params ? JSON.stringify(row.params, null, 2) : '{}'
  dialogVisible.value = true
  loadProviderOptions()
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
      platform: form.value.platform || (form.value.provider_id ? '' : ''),
      model: form.value.model,
      api_base: form.value.api_base || null,
      api_key: form.value.api_key || null,
      params,
      provider_id: form.value.provider_id || null,
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

  // 挂供应商且表单连接信息为空时，用供应商配置测试
  let apiBase = form.value.api_base || null
  let apiKey = form.value.api_key || null
  if (!apiBase || !apiKey) {
    const p = providerOptions.value.find((x) => x.id === form.value.provider_id)
    if (p) {
      apiBase = apiBase || p.api_base || null
      apiKey = apiKey || p.api_key || null
    }
  }

  testing.value = true
  try {
    const res = await testModel({
      model_type: form.value.model_type,
      platform: form.value.platform,
      model: form.value.model,
      api_base: apiBase,
      api_key: apiKey,
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

// --- Provider Methods ---
async function loadProviders() {
  providerLoading.value = true
  try {
    providers.value = (await listProviders()) || []
  } catch {
    ElNotification.error({ title: '加载失败', message: '无法获取供应商列表' })
  } finally {
    providerLoading.value = false
  }
}

function showProviderDialog() {
  providerDialogVisible.value = true
  loadProviders()
}

function resetProviderForm() {
  providerForm.value = {
    name: '',
    platform: '',
    api_base: '',
    api_key: '',
    is_active: true,
  }
}

function showProviderCreateDialog() {
  resetProviderForm()
  providerEditing.value = false
  providerEditingId.value = null
  providerEditVisible.value = true
}

function showProviderEditDialog(row) {
  providerEditing.value = true
  providerEditingId.value = row.id
  providerForm.value = {
    name: row.name,
    platform: row.platform,
    api_base: row.api_base || '',
    api_key: row.api_key || '',
    is_active: row.is_active,
  }
  providerEditVisible.value = true
}

async function handleProviderSubmit() {
  const valid = await providerFormRef.value.validate().catch(() => false)
  if (!valid) return

  providerSubmitting.value = true
  try {
    const payload = {
      name: providerForm.value.name,
      platform: providerForm.value.platform,
      api_base: providerForm.value.api_base || null,
      api_key: providerForm.value.api_key || null,
      is_active: providerForm.value.is_active,
    }
    if (providerEditing.value) {
      await updateProvider(providerEditingId.value, payload)
      ElMessage.success('供应商更新成功')
    } else {
      await createProvider(payload)
      ElMessage.success('供应商创建成功')
    }
    providerEditVisible.value = false
    await loadProviders()
    await loadProviderOptions()
  } catch (err) {
    const detail = err?.response?.data?.detail || err?.message || '请稍后重试'
    ElMessage.error('操作失败: ' + detail)
  } finally {
    providerSubmitting.value = false
  }
}

async function handleProviderDelete(row) {
  try {
    await deleteProvider(row.id)
    ElMessage.success('供应商已删除')
    await loadProviders()
    await loadProviderOptions()
  } catch (err) {
    const detail = err?.response?.data?.detail || '删除失败'
    ElMessage.error(detail)
  }
}

// --- Fetch & Batch Add Provider Models ---
function showFetchDialog(row) {
  fetchProvider.value = row
  fetchModelType.value = 'llm'
  fetchModels.value = []
  selectedFetchModels.value = []
  fetchDialogVisible.value = true
  loadFetchModels()
}

async function loadFetchModels() {
  if (!fetchProvider.value) return
  fetchLoading.value = true
  try {
    const data = await fetchProviderModels(fetchProvider.value.id)
    fetchModels.value = data.models || []
    selectedFetchModels.value = []
  } catch (err) {
    const detail = err?.response?.data?.detail || err?.message || '拉取失败'
    ElMessage.error(detail)
    fetchModels.value = []
  } finally {
    fetchLoading.value = false
  }
}

function handleFetchSelectionChange(rows) {
  selectedFetchModels.value = rows
}

async function handleBatchAdd() {
  if (selectedFetchModels.value.length === 0) return
  fetchAdding.value = true
  const type = fetchModelType.value
  const added = []
  const errors = []
  for (const m of selectedFetchModels.value) {
    try {
      // name 用模型 ID（唯一）；model=ID；挂到当前供应商，连接信息继承供应商
      await createModel({
        name: m.id,
        model_type: type,
        platform: '',
        model: m.id,
        api_base: null,
        api_key: null,
        params: null,
        provider_id: fetchProvider.value.id,
        is_default: false,
        is_active: true,
      })
      added.push(m.id)
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || '添加失败'
      errors.push(`${m.id}: ${detail}`)
    }
  }
  if (added.length) ElMessage.success(`已添加 ${added.length} 个模型`)
  if (errors.length) ElMessage.warning(`部分模型添加失败（${errors.length} 个）：${errors[0]}`)
  fetchDialogVisible.value = false
  await loadList()
  await loadProviders()
  await loadProviderOptions()
  fetchAdding.value = false
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

.provider-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.fetch-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.fetch-left {
  display: flex;
  align-items: center;
  gap: 8px;
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
