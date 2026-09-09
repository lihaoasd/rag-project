<template>
  <div class="lexicon-view">
    <div class="page-header">
      <h2>词库管理</h2>
      <span class="header-hint">
        全局自定义词典：供全文检索分词（IK 词典导出）与查询扩展（即时生效）；
        词条与知识图谱的关系通过“领域映射”（词条 × 领域模板 → 实体类型）配置
      </span>
    </div>

    <div class="toolbar">
      <el-input
        v-model="search"
        placeholder="搜索词条或别名..."
        clearable
        style="width: 240px"
        @keyup.enter="load"
        @clear="load"
      />
      <el-button type="primary" @click="load">
        <el-icon><Search /></el-icon>查询
      </el-button>
      <div class="spacer" />
      <el-button @click="openImport">
        <el-icon><Upload /></el-icon>批量导入
      </el-button>
      <el-button @click="handleExport">
        <el-icon><Download /></el-icon>导出
      </el-button>
      <el-button @click="handleExportIkDict">
        <el-icon><Document /></el-icon>导出 IK 词典
      </el-button>
      <el-button type="primary" plain @click="openCreate">
        <el-icon><Plus /></el-icon>新增词条
      </el-button>
    </div>

    <el-table :data="items" v-loading="loading" border stripe>
      <el-table-column prop="term" label="词条" min-width="180" show-overflow-tooltip />
      <el-table-column prop="aliases" label="别名/同义词" min-width="220">
        <template #default="{ row }">
          <el-tag v-for="a in row.aliases" :key="a" size="small" class="alias-tag">{{ a }}</el-tag>
          <span v-if="!row.aliases || row.aliases.length === 0" class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column label="领域映射" width="130">
        <template #default="{ row }">
          <el-tag v-if="row.mappings_count" type="success" size="small">
            {{ row.mappings_count }} 个领域
          </el-tag>
          <span v-else class="muted">未映射</span>
        </template>
      </el-table-column>
      <el-table-column prop="updated_at" label="更新时间" width="170">
        <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button text type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button text type="success" size="small" @click="openMapping(row)">领域映射</el-button>
          <el-button text type="danger" size="small" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="load"
      />
    </div>

    <!-- 新增/编辑词条对话框 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑词条' : '新增词条'" width="480px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="词条" required>
          <el-input v-model="form.term" placeholder="标准词，如：建筑工程施工总承包一级" />
        </el-form-item>
        <el-form-item label="别名">
          <el-select
            v-model="form.aliases"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="输入后回车添加别名（如：甲建设集团）"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 领域映射对话框：词条 × 领域模板 → 该模板内实体类型 -->
    <el-dialog
      v-model="mappingVisible"
      :title="`领域映射：${mappingTermName || ''}`"
      width="760px"
      destroy-on-close
    >
      <div class="mapping-hint">
        词条在“领域模板”下映射为该模板内的 1 个实体类型（同一领域至多 1 个）。
        已映射领域：图谱抽取建实体 / LLM 实体名归并 / 图谱检索候选按此生效；
        未映射领域该词条不进图（仍全局用于 IK 分词与查询同义词扩展）。
      </div>
      <div v-loading="mappingLoading" class="mapping-body">
        <div v-for="(row, idx) in mappingRows" :key="idx" class="mapping-row">
          <el-select
            v-model="row.domain_profile_id"
            filterable
            placeholder="选择领域模板"
            style="flex: 1"
            @change="(pid) => onDomainChange(row, pid)"
          >
            <el-option
              v-for="d in availableDomains(row)"
              :key="d.id"
              :label="d.name"
              :value="d.id"
            />
          </el-select>
          <el-select
            v-model="row.entity_code"
            filterable
            :disabled="!row.domain_profile_id"
            :loading="!!row.loading"
            placeholder="选择实体类型"
            style="flex: 1"
          >
            <el-option
              v-for="e in entityOptions(row.domain_profile_id)"
              :key="e.code"
              :label="e.name"
              :value="e.code"
            />
          </el-select>
          <el-button text type="danger" :disabled="mappingRows.length === 1" @click="removeMappingRow(idx)">
            删除
          </el-button>
        </div>
        <el-button class="add-mapping" plain @click="addMappingRow">
          <el-icon><Plus /></el-icon>添加领域
        </el-button>
      </div>
      <template #footer>
        <el-button @click="mappingVisible = false">取消</el-button>
        <el-button type="primary" :loading="mappingSaving" :disabled="mappingLoading" @click="handleSaveMappings">
          保存映射
        </el-button>
      </template>
    </el-dialog>

    <!-- 批量导入对话框 -->
    <el-dialog v-model="importVisible" title="CSV 批量导入" width="560px">
      <p class="import-hint">
        每行一个词条，格式：<code>词条,别名1;别名2</code>（首行表头自动跳过）。
        旧文件第 3 列（分类）自动忽略；词条与图谱的归属请导入后在“领域映射”中配置。
      </p>
      <el-input
        v-model="importContent"
        type="textarea"
        :rows="8"
        placeholder="甲公司,甲建设集团;甲建设&#10;乙公司,"
      />
      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="handleImport">导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Plus, Upload, Download, Document } from '@element-plus/icons-vue'
import {
  listTerms, createTerm, updateTerm, deleteTerm, importTerms, exportCsv, exportIkDict,
  getTermDomainMappings, updateTermDomainMappings,
} from '@/api/lexicon'
import { listDomains, getDomain } from '@/api/domain'

// --- State ---
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const search = ref('')
const loading = ref(false)

const dialogVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)
const form = ref({ term: '', aliases: [], enabled: true })

// 领域映射
const mappingVisible = ref(false)
const mappingLoading = ref(false)
const mappingSaving = ref(false)
const mappingTermId = ref(null)
const mappingTermName = ref('')
const mappingRows = ref([])
const domains = ref([])                 // [{id, name, is_preset}]
const entityOptionsCache = ref({})      // { domainId: [{code, name}] }

const importVisible = ref(false)
const importContent = ref('')
const importing = ref(false)

// --- 词条列表 ---
async function load() {
  loading.value = true
  try {
    const data = await listTerms({
      page: page.value,
      page_size: pageSize.value,
      search: search.value.trim(),
    })
    items.value = data?.items || []
    total.value = data?.total || 0
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '加载词条失败')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.value = { term: '', aliases: [], enabled: true }
  editingId.value = null
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.value = {
    term: row.term,
    aliases: [...(row.aliases || [])],
    enabled: row.enabled,
  }
  dialogVisible.value = true
}

async function handleSave() {
  if (!form.value.term.trim()) {
    ElMessage.warning('词条不能为空')
    return
  }
  saving.value = true
  try {
    const payload = { ...form.value, term: form.value.term.trim() }
    if (editingId.value) {
      await updateTerm(editingId.value, payload)
      ElMessage.success('词条已更新')
    } else {
      await createTerm(payload)
      ElMessage.success('词条已新增')
    }
    dialogVisible.value = false
    load()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`确认删除词条「${row.term}」？其领域映射将一并删除。`, '删除确认', { type: 'warning' })
    await deleteTerm(row.id)
    ElMessage.success('已删除')
    load()
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') {
      ElMessage.error(err?.response?.data?.detail || err?.message || '删除失败')
    }
  }
}

// --- 领域映射 ---
async function ensureDomains() {
  if (domains.value.length) return
  const data = await listDomains()
  domains.value = data?.items || []
}

async function ensureEntityOptions(profileId) {
  if (!profileId || entityOptionsCache.value[profileId]) return
  const detail = await getDomain(profileId)
  entityOptionsCache.value[profileId] = (detail?.entity_types || []).map((e) => ({
    code: e.code,
    name: e.name,
  }))
}

function entityOptions(profileId) {
  return entityOptionsCache.value[profileId] || []
}

function availableDomains(row) {
  const used = mappingRows.value
    .filter((r) => r !== row && r.domain_profile_id)
    .map((r) => r.domain_profile_id)
  return domains.value.filter((d) => !used.includes(d.id))
}

async function openMapping(row) {
  mappingLoading.value = true
  mappingVisible.value = true
  mappingTermId.value = row.id
  mappingTermName.value = row.term
  mappingRows.value = []
  try {
    await ensureDomains()
    const data = await getTermDomainMappings(row.id)
    const existing = data?.items || []
    for (const m of existing) {
      await ensureEntityOptions(m.domain_profile_id)
    }
    mappingRows.value = existing.map((m) => ({
      domain_profile_id: m.domain_profile_id,
      entity_code: m.entity_code,
      loading: false,
    }))
    if (mappingRows.value.length === 0) {
      mappingRows.value = [{ domain_profile_id: '', entity_code: '', loading: false }]
    }
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '加载领域映射失败')
  } finally {
    mappingLoading.value = false
  }
}

async function onDomainChange(row, pid) {
  row.entity_code = ''
  row.loading = true
  try {
    await ensureEntityOptions(pid)
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '加载实体类型失败')
  } finally {
    row.loading = false
  }
}

function addMappingRow() {
  mappingRows.value.push({ domain_profile_id: '', entity_code: '', loading: false })
}

function removeMappingRow(idx) {
  mappingRows.value.splice(idx, 1)
}

async function handleSaveMappings() {
  const partial = mappingRows.value.find((r) => (r.domain_profile_id && !r.entity_code) || (!r.domain_profile_id && r.entity_code))
  if (partial) {
    ElMessage.warning('存在未完成的映射行：请选择领域后选择实体类型，或删除该行')
    return
  }
  const mappings = mappingRows.value
    .filter((r) => r.domain_profile_id && r.entity_code)
    .map((r) => ({ domain_profile_id: r.domain_profile_id, entity_code: r.entity_code }))
  mappingSaving.value = true
  try {
    await updateTermDomainMappings(mappingTermId.value, mappings)
    ElMessage.success('领域映射已保存')
    mappingVisible.value = false
    load()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '保存领域映射失败')
  } finally {
    mappingSaving.value = false
  }
}

// --- 导入 / 导出 ---
function openImport() {
  importContent.value = ''
  importVisible.value = true
}

async function handleImport() {
  if (!importContent.value.trim()) {
    ElMessage.warning('请输入 CSV 内容')
    return
  }
  importing.value = true
  try {
    const result = await importTerms(importContent.value)
    ElMessage.success(`导入完成：新增 ${result.imported}，跳过重复 ${result.skipped}`)
    if (result.errors?.length) {
      console.warn('导入部分失败:', result.errors)
    }
    importVisible.value = false
    load()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '导入失败')
  } finally {
    importing.value = false
  }
}

async function handleExport() {
  try {
    const csv = await exportCsv()
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `lexicon_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '导出失败')
  }
}

/** 导出 IK 词典文件（一行一词，人工放置到 ES 词典目录后重启 ES 生效） */
async function handleExportIkDict() {
  try {
    const dict = await exportIkDict()
    const blob = new Blob([dict], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'ik_ext.dic'
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已导出 ik_ext.dic，请放置到 ES 词典目录并重启 ES 生效')
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '导出失败')
  }
}

function formatTime(iso) {
  if (!iso) return '-'
  return iso.replace('T', ' ').slice(0, 19)
}

onMounted(load)
</script>

<style scoped>
.lexicon-view {
  max-width: var(--page-max-width, 1400px);
  margin: 0 auto;
  padding: 16px 0;
}

.page-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 14px;
}

.page-header h2 {
  margin: 0;
}

.header-hint {
  color: var(--color-text-secondary, #909399);
  font-size: 13px;
}

.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
}

.spacer {
  flex: 1;
}

.alias-tag {
  margin-right: 6px;
}

.muted {
  color: var(--color-text-secondary, #909399);
}

.pagination {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
}

.mapping-hint {
  color: var(--color-text-secondary, #909399);
  font-size: 12px;
  line-height: 1.6;
  margin: 0 0 12px;
  padding: 8px 10px;
  background: #f5f7fa;
  border-radius: 4px;
}

.mapping-body {
  min-height: 40px;
}

.mapping-row {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}

.add-mapping {
  width: 100%;
  margin-top: 2px;
}

.import-hint {
  color: var(--color-text-secondary, #909399);
  font-size: 13px;
  margin: 0 0 10px;
}

.import-hint code {
  background: #f5f7fa;
  padding: 1px 5px;
  border-radius: 3px;
}
</style>
