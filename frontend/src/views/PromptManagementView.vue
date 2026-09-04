<template>
  <div class="prompt-view">
    <div class="page-header">
      <h1>提示词管理</h1>
      <p class="desc">管理多场景的提示词模板，支持变量占位符动态渲染</p>
    </div>

    <!-- 操作栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <el-radio-group v-model="scenarioFilter" @change="loadList">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="rag_qa">RAG 问答</el-radio-button>
          <el-radio-button value="entity_extraction">实体抽取</el-radio-button>
          <el-radio-button value="document_summary">文档总结</el-radio-button>
          <el-radio-button value="custom">自定义</el-radio-button>
          <el-radio-button value="image_recognition">图片识别</el-radio-button>
          <el-radio-button value="query_rewrite">Query 改写</el-radio-button>
          <el-radio-button value="intent_detect">意图识别</el-radio-button>
          <el-radio-button value="chat_normal">普通聊天</el-radio-button>
        </el-radio-group>
      </div>
      <div class="toolbar-right">
        <el-button type="primary" @click="showCreateDialog">
          <el-icon><Plus /></el-icon>
          新建模板
        </el-button>
      </div>
    </div>

    <!-- 列表表格 -->
    <el-card>
      <el-table :data="prompts" stripe v-loading="loading" style="width: 100%">
        <el-table-column prop="name" label="模板名称" min-width="180" />
        <el-table-column prop="scenario" label="场景" width="120" align="center">
          <template #default="{ row }">
            <el-tag :type="scenarioTagType(row.scenario)" size="small">
              {{ scenarioLabel(row.scenario) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        <el-table-column label="状态" width="160" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_active" type="success" size="small">激活中</el-tag>
            <el-tag v-else-if="row.is_default" type="info" size="small">默认</el-tag>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="160" align="center">
          <template #default="{ row }">
            {{ formatDate(row.updated_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" align="center" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="showPreviewDialog(row)">
              预览
            </el-button>
            <el-button text type="primary" size="small" @click="showEditDialog(row)">
              编辑
            </el-button>
            <el-button
              v-if="!row.is_active"
              text
              type="success"
              size="small"
              @click="handleActivate(row.id)"
            >
              激活
            </el-button>
            <el-popconfirm
              v-if="!row.is_default"
              title="确定删除该模板？"
              confirm-button-text="删除"
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
    </el-card>

    <!-- 创建/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEditing ? '编辑模板' : '新建模板'"
      width="720px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
      >
        <el-row :gutter="16">
          <el-col :span="14">
            <el-form-item label="模板名称" prop="name">
              <el-input
                v-model="form.name"
                placeholder="如：RAG 问答（正式风格）"
                maxlength="255"
                show-word-limit
              />
            </el-form-item>
          </el-col>
          <el-col :span="10">
            <el-form-item label="场景" prop="scenario">
              <el-select v-model="form.scenario" placeholder="选择场景" style="width: 100%">
                <el-option label="RAG 问答" value="rag_qa" />
                <el-option label="实体抽取" value="entity_extraction" />
                <el-option label="文档总结" value="document_summary" />
                <el-option label="自定义" value="custom" />
                <el-option label="图片识别" value="image_recognition" />
                <el-option label="Query 改写" value="query_rewrite" />
                <el-option label="意图识别" value="intent_detect" />
                <el-option label="普通聊天" value="chat_normal" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="描述">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
            placeholder="简要描述该模板的用途"
          />
        </el-form-item>

        <el-form-item label="System Prompt" prop="system_prompt">
          <el-input
            v-model="form.system_prompt"
            type="textarea"
            :rows="8"
            placeholder="输入系统提示词，使用 {variable_name} 作为变量占位符"
          />
          <div class="form-hint">
            可用变量：<code>{context_str}</code> <code>{query}</code> <code>{history}</code> <code>{kb_name}</code>
          </div>
        </el-form-item>

        <el-form-item label="User Prompt（可选）">
          <el-input
            v-model="form.user_prompt"
            type="textarea"
            :rows="3"
            placeholder="用户提示词模板（可选）"
          />
        </el-form-item>

        <el-form-item label="变量定义">
          <el-table :data="variableList" size="small" border>
            <el-table-column prop="name" label="变量名" width="140">
              <template #default="{ row, $index }">
                <template v-if="editingVarIndex === $index">
                  <el-input v-model="editingVar.name" size="small" placeholder="变量名" />
                </template>
                <template v-else>
                  <code>{{ row.name }}</code>
                </template>
              </template>
            </el-table-column>
            <el-table-column prop="label" label="标签" width="120">
              <template #default="{ row, $index }">
                <template v-if="editingVarIndex === $index">
                  <el-input v-model="editingVar.label" size="small" placeholder="标签" />
                </template>
                <template v-else>{{ row.label }}</template>
              </template>
            </el-table-column>
            <el-table-column prop="required" label="必填" width="70" align="center">
              <template #default="{ row, $index }">
                <template v-if="editingVarIndex === $index">
                  <el-checkbox v-model="editingVar.required" />
                </template>
                <template v-else>
                  {{ row.required ? '是' : '否' }}
                </template>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="描述">
              <template #default="{ row, $index }">
                <template v-if="editingVarIndex === $index">
                  <el-input v-model="editingVar.description" size="small" placeholder="描述" />
                </template>
                <template v-else>{{ row.description }}</template>
              </template>
            </el-table-column>
            <el-table-column width="100" align="center">
              <template #default="{ $index }">
                <template v-if="editingVarIndex === $index">
                  <el-button text type="primary" size="small" @click="saveVarEdit($index)">保存</el-button>
                  <el-button text size="small" @click="cancelVarEdit">取消</el-button>
                </template>
                <template v-else>
                  <el-button text type="primary" size="small" @click="startVarEdit($index)">编辑</el-button>
                  <el-button text type="danger" size="small" @click="removeVariable($index)">删除</el-button>
                </template>
              </template>
            </el-table-column>
          </el-table>
          <el-button
            type="dashed"
            size="small"
            style="margin-top: 8px; width: 100%"
            @click="addVariable"
          >
            <el-icon><Plus /></el-icon>
            添加变量
          </el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">
          {{ isEditing ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 预览对话框 -->
    <el-dialog
      v-model="previewVisible"
      title="预览渲染结果"
      width="640px"
      destroy-on-close
    >
      <div v-if="previewTemplate" class="preview-section">
        <h4>变量输入</h4>
        <el-form label-position="top">
          <el-form-item
            v-for="v in (previewTemplate.variables || [])"
            :key="v.name"
            :label="`${v.label} (${v.name})${v.required ? ' *' : ''}`"
          >
            <el-input
              v-model="previewVars[v.name]"
              type="textarea"
              :rows="2"
              :placeholder="v.description || `输入 ${v.name} 的值`"
            />
          </el-form-item>
        </el-form>

        <h4 style="margin-top: 24px;">渲染结果</h4>
        <div class="rendered-output">{{ renderedResult }}</div>
        <div v-if="missingVars.length > 0" class="missing-warning">
          缺失必填变量：{{ missingVars.join(', ') }}
        </div>
      </div>
      <template #footer>
        <el-button @click="previewVisible = false">关闭</el-button>
        <el-button type="primary" @click="doPreview">刷新渲染</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import {
  listPrompts, createPrompt, updatePrompt, deletePrompt,
  activatePrompt, previewPrompt,
} from '@/api/prompt'

// --- Constants ---
const SCENARIO_LABELS = {
  rag_qa: 'RAG 问答',
  entity_extraction: '实体抽取',
  document_summary: '文档总结',
  custom: '自定义',
  image_recognition: '图片识别',
  query_rewrite: 'Query 改写',
  intent_detect: '意图识别',
  chat_normal: '普通聊天',
}

const SCENARIO_TAG_TYPES = {
  rag_qa: '',
  entity_extraction: 'warning',
  document_summary: 'success',
  custom: 'info',
  image_recognition: 'danger',
  query_rewrite: 'primary',
  intent_detect: 'warning',
  chat_normal: 'success',
}

// --- State ---
const prompts = ref([])
const loading = ref(false)
const scenarioFilter = ref('')
const dialogVisible = ref(false)
const isEditing = ref(false)
const editingId = ref(null)
const submitting = ref(false)
const formRef = ref(null)
const form = ref({
  name: '',
  description: '',
  scenario: 'rag_qa',
  system_prompt: '',
  user_prompt: '',
  variables: [],
})

// 变量编辑状态
const variableList = ref([])
const editingVarIndex = ref(-1)
const editingVar = ref({ name: '', label: '', required: false, description: '' })

// 预览状态
const previewVisible = ref(false)
const previewTemplate = ref(null)
const previewVars = ref({})
const renderedResult = ref('')
const missingVars = ref([])

const rules = {
  name: [
    { required: true, message: '请输入模板名称', trigger: 'blur' },
    { min: 1, max: 255, message: '长度在 1 到 255 个字符', trigger: 'blur' },
  ],
  scenario: [{ required: true, message: '请选择场景', trigger: 'change' }],
  system_prompt: [{ required: true, message: '请输入系统提示词', trigger: 'blur' }],
}

// --- Methods ---
function scenarioLabel(s) { return SCENARIO_LABELS[s] || s }
function scenarioTagType(s) { return SCENARIO_TAG_TYPES[s] || 'info' }

function formatDate(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('zh-CN')
}

async function loadList() {
  loading.value = true
  try {
    const data = await listPrompts(scenarioFilter.value || undefined)
    prompts.value = data || []
  } catch {
    ElNotification.error({ title: '加载失败', message: '无法获取提示词模板列表' })
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.value = {
    name: '',
    description: '',
    scenario: 'rag_qa',
    system_prompt: '',
    user_prompt: '',
    variables: [],
  }
  variableList.value = []
  editingVarIndex.value = -1
}

function showCreateDialog() {
  resetForm()
  isEditing.value = false
  editingId.value = null
  dialogVisible.value = true
}

function showEditDialog(row) {
  isEditing.value = true
  editingId.value = row.id
  form.value = {
    name: row.name,
    description: row.description || '',
    scenario: row.scenario,
    system_prompt: row.system_prompt,
    user_prompt: row.user_prompt || '',
  }
  variableList.value = (row.variables || []).map(v => ({ ...v }))
  editingVarIndex.value = -1
  dialogVisible.value = true
}

// 变量编辑
function addVariable() {
  variableList.value.push({ name: '', label: '', required: false, description: '' })
  editingVarIndex.value = variableList.value.length - 1
  editingVar.value = { ...variableList.value[editingVarIndex.value] }
}

function startVarEdit(index) {
  editingVarIndex.value = index
  editingVar.value = { ...variableList.value[index] }
}

function saveVarEdit(index) {
  if (!editingVar.value.name.trim()) {
    ElMessage.warning('变量名不能为空')
    return
  }
  variableList.value[index] = { ...editingVar.value }
  editingVarIndex.value = -1
}

function cancelVarEdit() {
  if (editingVarIndex.value >= 0 && !variableList.value[editingVarIndex.value].name) {
    variableList.value.splice(editingVarIndex.value, 1)
  }
  editingVarIndex.value = -1
}

function removeVariable(index) {
  variableList.value.splice(index, 1)
}

async function handleSubmit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    const payload = {
      name: form.value.name,
      description: form.value.description || null,
      scenario: form.value.scenario,
      system_prompt: form.value.system_prompt,
      user_prompt: form.value.user_prompt || null,
      variables: variableList.value.filter(v => v.name.trim()).map(v => ({
        name: v.name.trim(),
        label: v.label || v.name,
        required: v.required,
        description: v.description || '',
      })),
    }

    if (isEditing.value) {
      await updatePrompt(editingId.value, payload)
      ElMessage.success('模板更新成功')
    } else {
      await createPrompt(payload)
      ElMessage.success('模板创建成功')
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

async function handleDelete(id) {
  try {
    await deletePrompt(id)
    ElMessage.success('模板已删除')
    await loadList()
  } catch (err) {
    const detail = err?.response?.data?.detail || '删除失败'
    ElMessage.error(detail)
  }
}

async function handleActivate(id) {
  try {
    await activatePrompt(id)
    ElMessage.success('已激活模板')
    await loadList()
  } catch (err) {
    const detail = err?.response?.data?.detail || '激活失败'
    ElMessage.error(detail)
  }
}

function showPreviewDialog(row) {
  previewTemplate.value = row
  previewVars.value = {}
  renderedResult.value = ''
  missingVars.value = []
  previewVisible.value = true
  // 打开即渲染一次，避免弹窗空白（填完变量后可再点「刷新渲染」）
  doPreview()
}

async function doPreview() {
  if (!previewTemplate.value) return
  try {
    const result = await previewPrompt({
      template_id: previewTemplate.value.id,
      variables: previewVars.value,
    })
    renderedResult.value = result.rendered
    missingVars.value = result.missing_variables || []
  } catch (err) {
    const detail = err?.response?.data?.detail || '预览失败'
    ElMessage.error(detail)
  }
}

onMounted(() => loadList())
</script>

<style scoped>
.prompt-view {
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

.form-hint code {
  background: var(--color-primary-bg);
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 12px;
  color: var(--color-primary);
  margin: 0 2px;
}

.preview-section h4 {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--color-text-primary);
}

.rendered-output {
  padding: 16px;
  background: #F8FAFC;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 300px;
  overflow-y: auto;
}

.missing-warning {
  margin-top: 12px;
  padding: 8px 14px;
  background: #FFF3CD;
  border: 1px solid #FFC107;
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: #856404;
}
</style>
