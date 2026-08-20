<template>
  <div class="es-admin-view">
    <div class="page-header">
      <h2>ES 全文检索对账</h2>
      <span class="header-hint">对比 PostgreSQL（权威源）与 Elasticsearch 的切片数；每个知识库均可手动重建（如词库更新后按新词典重新分词），发现差异时优先重建对应库</span>
    </div>

    <!-- 连接异常提示 -->
    <el-alert
      v-if="status.healthy === false"
      type="error"
      :closable="false"
      show-icon
      class="mb"
      title="Elasticsearch 不可达"
      description="无法执行对账，请检查 ES 服务（{{ status.index }}）后重试。"
    />

    <!-- 统计卡片：总数 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :span="6">
        <div class="stat-card">
          <div class="stat-label">ES 连接</div>
          <div class="stat-value">
            <el-tag :type="status.healthy ? 'success' : 'danger'">
              {{ status.healthy ? '正常' : '异常' }}
            </el-tag>
          </div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card">
          <div class="stat-label">PG 切片数（权威源）</div>
          <div class="stat-value">{{ status.pg_total ?? '-' }}</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card">
          <div class="stat-label">ES 文档数</div>
          <div class="stat-value">{{ status.es_total ?? '-' }}</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card">
          <div class="stat-label">差异知识库</div>
          <div class="stat-value" :class="{ 'diff-value': diffCount > 0 }">{{ diffCount }}</div>
        </div>
      </el-col>
    </el-row>

    <!-- 统计卡片：索引状态（①） -->
    <el-row v-if="indexStats" :gutter="16" class="stat-row">
      <el-col :span="6">
        <div class="stat-card">
          <div class="stat-label">索引健康</div>
          <div class="stat-value">
            <el-tag :type="indexStats.health === 'green' ? 'success' : 'warning'" size="small">
              {{ indexStats.health }}
            </el-tag>
          </div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card">
          <div class="stat-label">索引文档数</div>
          <div class="stat-value">{{ indexStats.docs_count }}</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card">
          <div class="stat-label">存储 / segments</div>
          <div class="stat-value stat-sub">{{ indexStats.store_size }}<span class="sub"> / {{ indexStats.segments }}</span></div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card">
          <div class="stat-label">已删除文档</div>
          <div class="stat-value">{{ indexStats.deleted_docs }}</div>
        </div>
      </el-col>
    </el-row>

    <div class="toolbar">
      <el-button type="primary" :loading="loading" @click="loadStatus">
        <el-icon><Refresh /></el-icon>刷新对账
      </el-button>
      <el-alert
        v-if="diffCount > 0"
        type="warning"
        :closable="false"
        show-icon
        title="发现数据不一致"
        description="重建会先清空对应知识库的 ES 索引，再从 PostgreSQL 全量重灌；期间该库全文检索可能短暂异常，请确认后操作。"
        class="diff-alert"
      />
      <div class="spacer" />
      <el-button
        type="warning"
        plain
        :disabled="diffCount === 0 || !status.healthy"
        :loading="rebuildingAll"
        @click="confirmRebuild()"
      >
        <el-icon><RefreshRight /></el-icon>重建全部索引
      </el-button>
    </div>

    <!-- 知识库对账（④ 全库三态） -->
    <div class="section-title">
      <span>知识库对账</span>
      <span class="section-sub">共 {{ kbTotal }} 个知识库，{{ diffCount }} 个有差异</span>
    </div>
    <el-table :data="kbs" border stripe v-loading="loading" class="mb">
      <el-table-column prop="kb_id" label="知识库 ID" min-width="260" show-overflow-tooltip />
      <el-table-column label="知识库名称" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ row.kb_name || row.kb_id }}</template>
      </el-table-column>
      <el-table-column prop="pg" label="PG 切片数" width="110" />
      <el-table-column prop="es" label="ES 文档数" width="110" />
      <el-table-column prop="pg_docs" label="PG 文档数" width="110" />
      <el-table-column prop="es_docs" label="ES 文档数" width="110" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusTag(row.status)" size="small">
            {{ statusText(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="110">
        <template #default="{ row }">
          <el-button
            text
            type="primary"
            size="small"
            :disabled="!status.healthy"
            :loading="rebuildingKb === row.kb_id"
            @click="confirmRebuild(row.kb_id)"
          >
            重建
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-if="kbTotal > pageSize"
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="kbTotal"
      :page-sizes="[10, 20, 50]"
      layout="total, sizes, prev, pager, next"
      @current-change="loadStatus"
      @size-change="onPageSizeChange"
      class="mb"
    />

    <!-- 文档级差异（②） -->
    <div class="section-title">
      <span>文档级差异</span>
      <span class="section-sub">
        {{ docDiffCount }} 条（含文件名，便于定位缺失/残留的文档）
        <template v-if="docDiffCount >= 500">，超出 500 条上限仅展示前 500</template>
      </span>
    </div>
    <template v-if="docDiffCount > 0">
      <el-table :data="docDiffs" border stripe class="mb">
        <el-table-column prop="kb_id" label="知识库 ID" min-width="200" show-overflow-tooltip />
        <el-table-column prop="file_name" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column prop="doc_id" label="文档 ID" min-width="200" show-overflow-tooltip />
        <el-table-column prop="pg" label="PG 切片数" width="110" />
        <el-table-column prop="es" label="ES 文档数" width="110" />
        <el-table-column label="差异方向" width="120">
          <template #default="{ row }">
            <el-tag :type="row.pg > row.es ? 'warning' : 'danger'" size="small">
              {{ row.pg > row.es ? '缺数据' : '有残留' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </template>
    <el-empty v-else-if="status.healthy !== false" description="文档级数据一致" :image-size="70" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, RefreshRight } from '@element-plus/icons-vue'
import { getEsStatus, rebuildEs } from '@/api/esAdmin'

// --- State ---
const status = ref({})
const loading = ref(false)
const rebuildingAll = ref(false)
const rebuildingKb = ref(null)
const page = ref(1)
const pageSize = ref(20)

const indexStats = computed(() => status.value.index_stats || null)
const diffCount = computed(() => status.value.diff_count ?? 0)
const kbs = computed(() => status.value.kbs ?? [])
const kbTotal = computed(() => status.value.kb_total ?? 0)
const docDiffs = computed(() => status.value.doc_diffs ?? [])
const docDiffCount = computed(() => status.value.doc_diff_count ?? 0)

// --- Methods ---
function statusTag(status) {
  return { ok: 'success', missing: 'warning', residual: 'danger' }[status] || 'info'
}

function statusText(status) {
  return { ok: '一致', missing: '缺数据', residual: '有残留' }[status] || status
}

async function loadStatus() {
  loading.value = true
  try {
    status.value = await getEsStatus(page.value, pageSize.value)
    // 后端切片后当前页为空（如删除知识库）时回到第一页
    if (status.value.kbs?.length === 0 && page.value > 1) {
      page.value = 1
      status.value = await getEsStatus(page.value, pageSize.value)
    }
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '获取对账状态失败')
  } finally {
    loading.value = false
  }
}

function onPageSizeChange() {
  page.value = 1
  loadStatus()
}

async function confirmRebuild(kbId = null) {
  const scope = kbId ? `该知识库（${kbId.slice(0, 8)}…）` : '全部知识库'
  try {
    await ElMessageBox.confirm(
      `确认重建 ${scope} 的 ES 索引？将先清空再按 PostgreSQL 全量重灌，期间该库全文检索可能短暂异常。`,
      '重建确认',
      { type: 'warning', confirmButtonText: '确认重建', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }

  if (kbId) {
    rebuildingKb.value = kbId
  } else {
    rebuildingAll.value = true
  }
  try {
    const result = await rebuildEs(kbId)
    ElMessage.success(`重建完成：${result.rebuilt_chunks} 条切片`)
    await loadStatus()
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '重建失败')
  } finally {
    rebuildingAll.value = false
    rebuildingKb.value = null
  }
}

onMounted(loadStatus)
</script>

<style scoped>
.es-admin-view {
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

.mb {
  margin-bottom: 16px;
}

.stat-row {
  margin-bottom: 16px;
}

.stat-card {
  background: #fff;
  border: 1px solid var(--color-border, #e4e7ed);
  border-radius: 8px;
  padding: 14px 16px;
}

.stat-label {
  color: var(--color-text-secondary, #909399);
  font-size: 13px;
  margin-bottom: 8px;
}

.stat-value {
  font-size: 22px;
  font-weight: 600;
}

.stat-sub {
  font-size: 18px;
}

.sub {
  font-size: 14px;
  color: var(--color-text-secondary, #909399);
  font-weight: 400;
}

.diff-value {
  color: var(--el-color-warning);
}

.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 18px;
}

.diff-alert {
  flex: 1;
}

.spacer {
  flex: 1;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 10px;
}

.section-sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--color-text-secondary, #909399);
  margin-left: 8px;
}
</style>
