<template>
  <div class="domain-view">
    <div class="page-header">
      <h1>领域模板</h1>
      <p class="desc">配置知识库图谱本体：实体/关系类型由模板决定，创建知识库或图谱页可选用/更换模板</p>
    </div>

    <div class="toolbar">
      <span class="toolbar-hint">
        内置预设只读（可复制后修改）；删除模板前需先为其绑定的知识库更换模板
      </span>
      <el-button type="primary" @click="openNew">
        <el-icon><Plus /></el-icon>
        新建模板
      </el-button>
    </div>

    <el-table :data="list" v-loading="loading" stripe>
      <el-table-column label="名称" min-width="160">
        <template #default="{ row }">
          <el-link type="primary" :underline="false" @click="openView(row)">
            {{ row.name }}
          </el-link>
          <el-tag v-if="row.is_preset" size="small" type="warning" effect="plain" class="preset-tag">内置</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="说明" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ row.description || '—' }}</template>
      </el-table-column>
      <el-table-column label="实体类型" width="96" align="center">
        <template #default="{ row }">{{ row.entity_count }}</template>
      </el-table-column>
      <el-table-column label="关系类型" width="96" align="center">
        <template #default="{ row }">{{ row.relation_count }}</template>
      </el-table-column>
      <el-table-column label="操作" width="230" align="center">
        <template #default="{ row }">
          <el-button text type="primary" size="small" @click="row.is_preset ? openView(row) : openEdit(row)">
            {{ row.is_preset ? '查看' : '编辑' }}
          </el-button>
          <el-button text type="success" size="small" @click="handleCopy(row)">复制</el-button>
          <el-button text type="warning" size="small" @click="openView(row)">预览</el-button>
          <el-button
            v-if="!row.is_preset"
            text
            type="danger"
            size="small"
            @click="handleDelete(row)"
          >删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && list.length === 0" description="暂无领域模板" :image-size="100" />

    <!-- 模板编辑器（新建 / 编辑 / 只读预览） -->
    <el-dialog
      v-model="editor.visible"
      :title="dialogTitle"
      width="860px"
      top="4vh"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <div v-loading="editor.loading">
        <el-alert
          v-if="editor.mode === 'view'"
          type="info"
          :closable="false"
          show-icon
          title="内置预设只读：可点下方「复制为可编辑模板」后自定义"
          class="editor-alert"
        />
        <el-form label-position="top" class="editor-form">
          <div class="form-row">
            <el-form-item label="模板名称" class="row-name">
              <el-input v-model="editor.form.name" :disabled="editor.mode === 'view'" maxlength="100" placeholder="如：医疗、法律、投标" />
            </el-form-item>
            <el-form-item label="说明（可选）" class="row-desc">
              <el-input v-model="editor.form.description" :disabled="editor.mode === 'view'" placeholder="一句话说明适用场景" />
            </el-form-item>
          </div>

          <!-- 实体类型清单 -->
          <div class="section-title">
            <span>实体类型（3 ~ 20 个，code 为英文标识，作为图谱节点标签）</span>
            <el-button
              v-if="editor.mode !== 'view'"
              size="small"
              :disabled="editor.form.entities.length >= 20"
              @click="addEntity"
            >+ 添加实体类型</el-button>
          </div>
          <div class="type-list">
            <div v-for="(e, i) in editor.form.entities" :key="i" class="type-row">
              <el-input v-model="e.code" :disabled="editor.mode === 'view'" placeholder="Code，如 Disease" class="col-code" />
              <el-input v-model="e.name" :disabled="editor.mode === 'view'" placeholder="中文名，如 疾病" class="col-name" />
              <el-color-picker
                v-model="e.color"
                :disabled="editor.mode === 'view'"
                size="small"
                class="col-color"
              />
              <el-input v-model="e.description" :disabled="editor.mode === 'view'" placeholder="描述（抽取提示词用）" class="col-desc" />
              <el-input
                v-model="e.examplesText"
                :disabled="editor.mode === 'view'"
                placeholder="示例，顿号/逗号分隔（提示词引导抽取）"
                class="col-examples"
              />
              <el-button
                v-if="editor.mode !== 'view'"
                text
                type="danger"
                :disabled="editor.form.entities.length <= 3"
                @click="removeAt(editor.form.entities, i)"
              >移除</el-button>
            </div>
          </div>

          <!-- 关系类型清单 -->
          <div class="section-title">
            <span>关系类型（≤ 30 个，中文名，作为图谱边类型）</span>
            <el-button
              v-if="editor.mode !== 'view'"
              size="small"
              :disabled="editor.form.relations.length >= 30"
              @click="addRelation"
            >+ 添加关系类型</el-button>
          </div>
          <div class="type-list">
            <div v-for="(r, i) in editor.form.relations" :key="i" class="type-row">
              <el-input v-model="r.name" :disabled="editor.mode === 'view'" placeholder="关系名，如 治疗" class="col-name" />
              <el-input v-model="r.direction_hint" :disabled="editor.mode === 'view'" placeholder="方向提示，如 Hospital|Doctor→Disease（可选）" class="col-hint" />
              <el-input v-model="r.description" :disabled="editor.mode === 'view'" placeholder="描述（抽取提示词用）" class="col-desc" />
              <el-button
                v-if="editor.mode !== 'view'"
                text
                type="danger"
                @click="removeAt(editor.form.relations, i)"
              >移除</el-button>
            </div>
          </div>

          <!-- 实时校验 / 提示词预览 -->
          <div v-if="preview" class="preview-panel">
            <div class="preview-head">
              <el-tag :type="preview.valid ? 'success' : 'danger'" effect="dark" size="small">
                {{ preview.valid ? '校验通过' : `校验未通过（${preview.errors.length} 项）` }}
              </el-tag>
              <span v-if="editor.mode !== 'view'" class="preview-counts">
                {{ editor.form.entities.length }} 实体 / {{ editor.form.relations.length }} 关系
              </span>
            </div>
            <ul v-if="!preview.valid" class="preview-errors">
              <li v-for="(msg, i) in preview.errors" :key="i">{{ msg }}</li>
            </ul>
            <pre v-if="preview.prompt" class="preview-prompt">{{ preview.prompt }}</pre>
          </div>
        </el-form>
      </div>

      <template #footer>
        <div class="dialog-footer">
          <el-button
            v-if="editor.mode === 'view' && editor.row?.is_preset"
            type="warning"
            plain
            @click="handleCopy(editor.row)"
          >复制为可编辑模板</el-button>
          <el-button v-if="editor.mode !== 'view'" :loading="previewing" @click="runPreview">校验 / 预览提示词</el-button>
          <span class="footer-spacer" />
          <el-button @click="editor.visible = false">关闭</el-button>
          <el-button
            v-if="editor.mode !== 'view'"
            type="primary"
            :loading="saving"
            :disabled="!preview?.valid"
            @click="save"
          >保存</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listDomains, getDomain, createDomain, updateDomain, deleteDomain, copyDomain, previewDomain } from '@/api/domain'

const list = ref([])
const loading = ref(false)
const editor = ref({ visible: false, mode: 'create', loading: false, row: null, form: null })
const saving = ref(false)
const previewing = ref(false)
const preview = ref(null)

const dialogTitle = computed(() => {
  if (editor.value.mode === 'view') return `领域模板预览 — ${editor.value.form?.name || ''}`
  if (editor.value.mode === 'edit') return `编辑领域模板 — ${editor.value.form?.name || ''}`
  return '新建领域模板'
})

// --- 表单辅助 ---
function splitExamples(text) {
  return String(text || '')
    .split(/[,，、;；\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function joinExamples(examples) {
  return (examples || []).join('、')
}

function emptyForm() {
  return {
    name: '',
    description: '',
    entities: [
      { code: '', name: '', color: '#409EFF', description: '', examplesText: '' },
      { code: '', name: '', color: '#67C23A', description: '', examplesText: '' },
      { code: '', name: '', color: '#E6A23C', description: '', examplesText: '' },
    ],
    relations: [],
  }
}

function payloadFromForm(form) {
  return {
    name: form.name,
    description: form.description || '',
    entity_types: form.entities.map((e) => ({
      code: (e.code || '').trim(),
      name: (e.name || '').trim(),
      color: e.color || '',
      description: (e.description || '').trim(),
      examples: splitExamples(e.examplesText),
    })),
    relation_types: form.relations.map((r) => ({
      name: (r.name || '').trim(),
      description: (r.description || '').trim(),
      direction_hint: (r.direction_hint || '').trim(),
    })),
  }
}

function addEntity() {
  if (editor.value.form.entities.length < 20) {
    editor.value.form.entities.push({ code: '', name: '', color: '#909399', description: '', examplesText: '' })
  }
}

function addRelation() {
  if (editor.value.form.relations.length < 30) {
    editor.value.form.relations.push({ name: '', description: '', direction_hint: '' })
  }
}

function removeAt(arr, i) {
  if (arr.length > (arr === editor.value.form.entities ? 3 : 1)) arr.splice(i, 1)
}

// --- 弹窗打开 ---
async function loadList() {
  loading.value = true
  try {
    const data = await listDomains()
    list.value = data?.items || []
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '加载失败'
    ElMessage.error('加载领域模板失败: ' + msg)
  } finally {
    loading.value = false
  }
}

async function openDetail(row, mode) {
  editor.value = { visible: true, mode, loading: true, row, form: null }
  preview.value = null
  try {
    const detail = await getDomain(row.id)
    editor.value.form = {
      name: detail.name,
      description: detail.description || '',
      entities: (detail.entity_types || []).map((t) => ({
        code: t.code,
        name: t.name,
        color: t.color || '#909399',
        description: t.description || '',
        examplesText: joinExamples(t.examples),
      })),
      relations: (detail.relation_types || []).map((r) => ({
        name: r.name,
        description: r.description || '',
        direction_hint: r.direction_hint || '',
      })),
    }
    if (mode === 'view' || mode === 'edit') await runPreview()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '加载失败'
    ElMessage.error('加载模板详情失败: ' + msg)
    editor.value.visible = false
  } finally {
    editor.value.loading = false
  }
}

function openNew() {
  editor.value = { visible: true, mode: 'create', loading: false, row: null, form: emptyForm() }
  preview.value = null
  runPreview()
}

function openView(row) {
  openDetail(row, 'view')
}

function openEdit(row) {
  openDetail(row, 'edit')
}

// --- 校验 / 保存 / 复制 / 删除 ---
async function runPreview() {
  if (!editor.value.form) return
  previewing.value = true
  try {
    preview.value = await previewDomain(payloadFromForm(editor.value.form))
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '校验失败'
    ElMessage.error(msg)
  } finally {
    previewing.value = false
  }
}

async function save() {
  if (!preview.value?.valid) return
  saving.value = true
  try {
    const payload = payloadFromForm(editor.value.form)
    if (editor.value.mode === 'edit') {
      await updateDomain(editor.value.row.id, payload)
      ElMessage.success('模板已更新（引用该模板的知识库图谱状态已失效，需重建）')
    } else {
      await createDomain(payload)
      ElMessage.success('模板已创建')
    }
    editor.value.visible = false
    await loadList()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '保存失败'
    ElMessage.error(msg)
  } finally {
    saving.value = false
  }
}

async function handleCopy(row) {
  try {
    const detail = await copyDomain(row.id)
    ElMessage.success(`已复制为「${detail.name}」，可在编辑器中调整`)
    editor.value = { visible: true, mode: 'edit', loading: false, row: { id: detail.id }, form: null }
    preview.value = null
    // 直接以副本详情打开编辑器
    const d = detail
    editor.value.form = {
      name: d.name,
      description: d.description || '',
      entities: (d.entity_types || []).map((t) => ({
        code: t.code,
        name: t.name,
        color: t.color || '#909399',
        description: t.description || '',
        examplesText: joinExamples(t.examples),
      })),
      relations: (d.relation_types || []).map((r) => ({
        name: r.name,
        description: r.description || '',
        direction_hint: r.direction_hint || '',
      })),
    }
    await runPreview()
    await loadList()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '复制失败'
    ElMessage.error(msg)
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `删除领域模板「${row.name}」？模板被知识库引用时无法删除。`,
      '删除模板',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await deleteDomain(row.id)
    ElMessage.success('模板已删除')
    await loadList()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '删除失败'
    ElMessage.error(msg)
  }
}

onMounted(() => loadList())
</script>

<style scoped>
.domain-view {
  max-width: var(--page-max-width, 1400px);
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  gap: 12px;
}
.toolbar-hint {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.preset-tag {
  margin-left: 8px;
}
.editor-alert {
  margin-bottom: 16px;
}
.editor-form {
  max-height: 62vh;
  overflow-y: auto;
  padding-right: 6px;
}
.form-row {
  display: flex;
  gap: 16px;
}
.row-name { flex: 0 0 240px; }
.row-desc { flex: 1; }
.section-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 14px 0 8px;
  padding-top: 10px;
  border-top: 1px dashed var(--color-border);
}
.type-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.type-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.col-code { width: 130px; flex-shrink: 0; }
.col-name { width: 130px; flex-shrink: 0; }
.col-color { flex-shrink: 0; }
.col-hint { flex: 1.2; }
.col-desc { flex: 1; }
.col-examples { flex: 1.2; }
.preview-panel {
  margin-top: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 12px;
  background: #fafafa;
}
.preview-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.preview-counts {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.preview-errors {
  margin: 0;
  padding-left: 18px;
  color: var(--el-color-danger);
  font-size: 13px;
  line-height: 1.8;
}
.preview-prompt {
  max-height: 220px;
  overflow: auto;
  margin: 8px 0 0;
  padding: 10px;
  background: #0f172a;
  color: #d1e7ff;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
}
.dialog-footer {
  display: flex;
  align-items: center;
  gap: 8px;
}
.footer-spacer { flex: 1; }
</style>
