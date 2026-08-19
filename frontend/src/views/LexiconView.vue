<template>
  <div class="lexicon-view">
    <div class="page-header">
      <h2>词库管理</h2>
      <span class="header-hint">自定义词典：供全文检索分词（IK 最长约 1 小时生效）与查询扩展（即时生效）</span>
    </div>

    <div class="toolbar">
      <el-input
        v-model="search"
        placeholder="搜索词条或别名..."
        clearable
        style="width: 220px"
        @keyup.enter="load"
        @clear="load"
      />
      <el-input
        v-model="categoryFilter"
        placeholder="分类过滤"
        clearable
        style="width: 160px"
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
      <el-table-column prop="category" label="分类" width="130">
        <template #default="{ row }">
          <el-tag v-if="row.category" type="info" size="small">{{ row.category }}</el-tag>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-switch
            :model-value="row.enabled"
            @change="(v) => toggleEnabled(row, v)"
          />
        </template>
      </el-table-column>
      <el-table-column prop="updated_at" label="更新时间" width="170">
        <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button text type="primary" size="small" @click="openEdit(row)">编辑</el-button>
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

    <!-- 新增/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑词条' : '新增词条'"
      width="480px"
    >
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
        <el-form-item label="分类">
          <el-input v-model="form.category" placeholder="自由文本，如：资质 / 公司 / 术语（可空）" />
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

    <!-- 批量导入对话框 -->
    <el-dialog v-model="importVisible" title="CSV 批量导入" width="560px">
      <p class="import-hint">
        每行一个词条，格式：<code>词条,别名1;别名2,分类</code>（首行表头自动跳过）
      </p>
      <el-input
        v-model="importContent"
        type="textarea"
        :rows="8"
        placeholder="甲公司,甲建设集团;甲建设,公司&#10;乙公司,,建筑"
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
import { Search, Plus, Upload, Download } from '@element-plus/icons-vue'
import { listTerms, createTerm, updateTerm, deleteTerm, importTerms, exportCsv } from '@/api/lexicon'

// --- State ---
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const search = ref('')
const categoryFilter = ref('')
const loading = ref(false)

const dialogVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)
const form = ref({ term: '', aliases: [], category: '', enabled: true })

const importVisible = ref(false)
const importContent = ref('')
const importing = ref(false)

// --- Methods ---
async function load() {
  loading.value = true
  try {
    const data = await listTerms({
      page: page.value,
      page_size: pageSize.value,
      search: search.value.trim(),
      category: categoryFilter.value.trim(),
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
  form.value = { term: '', aliases: [], category: '', enabled: true }
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
    category: row.category || '',
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

async function toggleEnabled(row, value) {
  try {
    await updateTerm(row.id, { enabled: value })
    row.enabled = value
    ElMessage.success(value ? '已启用（分词最长 1 小时生效，查询扩展即时生效）' : '已停用')
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '操作失败')
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`确认删除词条「${row.term}」？`, '删除确认', { type: 'warning' })
    await deleteTerm(row.id)
    ElMessage.success('已删除')
    load()
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') {
      ElMessage.error(err?.response?.data?.detail || err?.message || '删除失败')
    }
  }
}

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
