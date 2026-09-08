<template>
  <div class="graph-view">
    <!-- 面包屑 + 操作栏 -->
    <div class="page-header">
      <div class="breadcrumb">
        <el-button text type="primary" @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          返回
        </el-button>
        <span class="sep">/</span>
        <span class="current">{{ kbName || '知识图谱' }}</span>
      </div>
      <div class="header-row">
        <h1>知识图谱</h1>
        <div class="actions">
          <el-input
            v-model="searchText"
            placeholder="输入实体名检索图谱（如：甲公司）"
            clearable
            style="width: 320px"
            @keyup.enter="handleSearch"
            @clear="loadGraph"
          >
            <template #append>
              <el-button :loading="searching" @click="handleSearch">
                <el-icon><Search /></el-icon>
              </el-button>
            </template>
          </el-input>
          <el-tag
            v-if="metaDomain"
            effect="plain"
            class="domain-tag"
            style="cursor: pointer"
            @click="openDomainDialog"
          >
            领域：{{ metaDomain.name }}（{{ metaEntityTypes.length }} 类实体）· 点击更换
          </el-tag>
          <el-button type="primary" :loading="building" @click="handleBuildKb">
            <el-icon><RefreshRight /></el-icon>
            全量构建
          </el-button>
          <el-button type="danger" plain :disabled="!stats.graph_exists" @click="handleClearKb">
            <el-icon><Delete /></el-icon>
            清空图谱
          </el-button>
        </div>
      </div>
    </div>

    <!-- 构建中提示（§11.3：避免清空后/重建中的空图谱误判） -->
    <el-alert
      v-if="build.building > 0"
      type="warning"
      :closable="false"
      show-icon
      title="图谱构建中，页面将自动刷新..."
      class="build-alert"
    />

    <!-- 统计卡片 -->
    <div class="stat-cards">
      <el-card shadow="never" class="stat-card">
        <div class="stat-num">{{ stats.stats?.total_entities ?? 0 }}</div>
        <div class="stat-label">实体数</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-num">{{ stats.stats?.total_relations ?? 0 }}</div>
        <div class="stat-label">关系数</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-num">{{ build.built }}/{{ build.doc_total }}</div>
        <div class="stat-label">已建图文件</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-num small">
          <span v-for="(cnt, type) in stats.stats?.entity_types || {}" :key="type" class="type-chip" :style="{ color: typeColor(type) }">
            {{ type }} {{ cnt }}
          </span>
          <span v-if="!Object.keys(stats.stats?.entity_types || {}).length" class="muted">-</span>
        </div>
        <div class="stat-label">实体类型分布</div>
      </el-card>
    </div>

    <!-- 主区：图 + 详情面板 -->
    <div class="main-area">
      <el-card shadow="never" class="chart-card">
        <div v-if="!graphLoaded" class="chart-tip">
          <el-empty description="图谱为空，请先在文档列表对文件「建图」或点击上方「全量构建」" :image-size="100" />
        </div>
        <div v-else ref="chartRef" class="chart"></div>
        <div v-if="graphLoaded && graphData.has_more" class="load-more-bar">
          <el-button :loading="loadingMore" @click="loadMore">
            加载更多（已显示 {{ graphData.nodes.length }} / 共 {{ graphData.total_nodes }} 节点）
          </el-button>
          <span class="load-more-hint">节点较多时建议使用搜索聚焦查看</span>
        </div>
      </el-card>

      <el-card shadow="never" class="detail-card">
        <el-tabs v-model="rightTab" class="right-tabs">
          <!-- 实体列表：分页 + 搜索 + 编辑 + 点击渲染画布 -->
          <el-tab-pane label="实体列表" name="entity-list">
            <div class="entity-list-pane">
              <el-input
                v-model="listQuery"
                placeholder="搜索实体名"
                clearable
                size="small"
                class="list-search"
                @keyup.enter="loadEntityList"
                @clear="loadEntityList"
              >
                <template #append>
                  <el-button :loading="listLoading" @click="loadEntityList">
                    <el-icon><Search /></el-icon>
                  </el-button>
                </template>
              </el-input>
              <el-table :data="entityList.items" size="small" v-loading="listLoading" stripe>
                <el-table-column label="名称" min-width="110" show-overflow-tooltip>
                  <template #default="{ row }">
                    <el-link type="primary" :underline="false" @click="renderEntityToCanvas(row.name)">
                      {{ row.name }}
                    </el-link>
                  </template>
                </el-table-column>
                <el-table-column label="类型" width="104">
                  <template #default="{ row }">
                    <el-tag :color="typeColor(row.type)" effect="dark" size="small" class="type-badge">
                      {{ row.type }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="relation_count" label="关系数" width="66" align="center" />
                <el-table-column label="chunk数" width="70" align="center">
                  <template #default="{ row }">
                    <el-link
                      v-if="row.chunk_count > 0"
                      type="primary"
                      :underline="false"
                      @click="showEntityEvidence(row.name)"
                    >{{ row.chunk_count }}</el-link>
                    <span v-else class="muted">0</span>
                  </template>
                </el-table-column>
                <el-table-column prop="description" label="描述" min-width="90" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.description || '—' }}</template>
                </el-table-column>
                <el-table-column label="操作" width="112" align="center">
                  <template #default="{ row }">
                    <el-button text type="primary" size="small" @click="openEditDialog(row)">编辑</el-button>
                    <el-button text type="success" size="small" @click="renderEntityToCanvas(row.name)">渲染</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <div class="list-footer">
                <el-pagination
                  small
                  background
                  layout="total, prev, pager, next"
                  :total="entityList.total"
                  :page-size="entityListPageSize"
                  :current-page="entityListPage"
                  @current-change="onListPageChange"
                />
              </div>
            </div>
          </el-tab-pane>

          <!-- 详情：选中实体的属性 / 邻居 / 证据 -->
          <el-tab-pane label="详情" name="detail">
            <template v-if="detail?.entity">
              <div class="entity-title">
                <el-tag :color="typeColor(detail.entity.type)" effect="dark" size="small" class="type-badge">
                  {{ detail.entity.type }}
                </el-tag>
                <span class="entity-name">{{ detail.entity.name }}</span>
                <el-button
                  type="primary"
                  plain
                  size="small"
                  :loading="expanding"
                  @click="handleExpand(detail.entity.name)"
                >
                  <el-icon><Share /></el-icon>展开邻居
                </el-button>
              </div>
              <p class="entity-desc">{{ detail.entity.description || '（无描述）' }}</p>
              <el-divider content-position="left">邻居（{{ detail.neighbors.length }}）</el-divider>
              <div v-if="detail.neighbors.length" class="neighbor-list">
                <div v-for="(nb, i) in detail.neighbors" :key="i" class="neighbor-item" @click="handleEntityClick(nb.name)">
                  <el-tag size="small" effect="plain" class="nb-type">{{ nb.type }}</el-tag>
                  <span class="nb-name">{{ nb.name }}</span>
                  <span class="nb-rel">{{ nb.direction === 'out' ? '→' : '←' }} {{ nb.relation }}</span>
                </div>
              </div>
              <div v-else class="muted">无邻居</div>
              <el-divider content-position="left">chunk 证据（{{ detail.evidence.length }}）</el-divider>
              <div v-if="detail.evidence.length" class="evidence-list">
                <div v-for="(ev, i) in detail.evidence" :key="i" class="evidence-item">
                  <div class="evidence-meta">
                    <span>{{ ev.file_name }}</span>
                    <el-tag size="small" type="info" effect="plain">第 {{ ev.page_number }} 页</el-tag>
                    <el-button text type="primary" size="small" @click="openChunk(ev, detail.entity.name)">
                      查看原文
                    </el-button>
                  </div>
                  <p class="evidence-text" v-html="highlight(ev.text, detail.entity.name)"></p>
                </div>
              </div>
              <div v-else class="muted">无证据</div>
            </template>
            <el-empty v-else description="点击图谱节点或列表实体查看详情与证据" :image-size="80" />
          </el-tab-pane>
        </el-tabs>
      </el-card>
    </div>

    <!-- 检索证据 -->
    <el-card v-if="searchResult.nodes.length" shadow="never" class="search-evidence">
      <template #header>检索结果证据（{{ searchResult.evidence.length }} 个实体）</template>
      <div v-for="evg in searchResult.evidence" :key="evg.entity" class="search-evidence-group">
        <div class="search-evidence-title" @click="handleEntityClick(evg.entity)">{{ evg.entity }}</div>
        <div v-for="(ev, i) in evg.chunks" :key="i" class="evidence-item">
          <div class="evidence-meta">
            <span>{{ ev.file_name }}</span>
            <el-tag size="small" type="info" effect="plain">第 {{ ev.page_number }} 页</el-tag>
            <el-button text type="primary" size="small" @click="openChunk(ev, evg.entity)">查看原文</el-button>
          </div>
          <p class="evidence-text" v-html="highlight(ev.text, evg.entity)"></p>
        </div>
      </div>
    </el-card>

    <!-- chunk 原文弹窗（不跳转，当前页查看） -->
    <el-dialog
      v-model="chunkDialog.visible"
      :title="chunkDialog.title"
      width="680px"
      top="6vh"
    >
      <template v-if="chunkDialog.ev">
        <div class="chunk-dialog-meta">
          <el-tag size="small" type="info" effect="plain">{{ chunkDialog.ev.file_name }}</el-tag>
          <el-tag size="small" type="info" effect="plain">第 {{ chunkDialog.ev.page_number }} 页</el-tag>
          <el-tag size="small" type="info" effect="plain">切片 #{{ chunkDialog.ev.chunk_index }}</el-tag>
        </div>
        <p class="chunk-dialog-text" v-html="highlightFull(chunkDialog.ev.text, chunkDialog.entity)"></p>
      </template>
      <template #footer>
        <el-button @click="chunkDialog.visible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 领域模板更换弹窗（v2：本体变化 → 图谱状态失效需重建） -->
    <el-dialog
      v-model="domainDialog.visible"
      title="更换领域模板"
      width="480px"
      :close-on-click-modal="false"
    >
      <div v-loading="domainDialog.loading">
        <p class="domain-dialog-tip">
          更换后图谱将按新本体重新抽取，当前已构建图谱<b>状态会失效</b>（需手动「全量构建」）。
        </p>
        <el-form label-position="top">
          <el-form-item label="目标领域模板">
            <el-select v-model="domainDialog.target" style="width: 100%" placeholder="选择领域模板">
              <el-option
                v-for="d in domainDialog.domains"
                :key="d.id"
                :label="`${d.name}${d.is_preset ? '（内置）' : ''}（${d.entity_count} 实体 / ${d.relation_count} 关系）`"
                :value="d.id"
              />
            </el-select>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="domainDialog.visible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="domainDialog.saving"
          :disabled="!domainDialog.target || domainDialog.target === domainDialog.current"
          @click="saveDomainChange"
        >
          确认更换
        </el-button>
      </template>
    </el-dialog>

    <!-- 实体编辑弹窗（名称/类型/描述） -->
    <el-dialog
      v-model="editDialog.visible"
      title="编辑实体"
      width="520px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form ref="editFormRef" :model="editDialog.form" :rules="editRules" label-width="72px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="editDialog.form.name" maxlength="100" placeholder="实体名" />
        </el-form-item>
        <el-form-item label="类型" prop="type">
          <el-select v-model="editDialog.form.type" style="width: 100%">
            <el-option
              v-for="o in entityTypeOptions"
              :key="o.code"
              :label="o.label"
              :value="o.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input
            v-model="editDialog.form.description"
            type="textarea"
            :rows="4"
            maxlength="500"
            show-word-limit
            placeholder="实体描述（可选）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="editDialog.saving" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 实体 chunk 证据弹窗（列表 chunk 数点击查看原文） -->
    <el-dialog
      v-model="evidenceDialog.visible"
      :title="`chunk 证据 — ${evidenceDialog.entity}`"
      width="720px"
      top="6vh"
    >
      <div v-if="evidenceDialog.items.length" class="evidence-dialog-list">
        <div v-for="(ev, i) in evidenceDialog.items" :key="i" class="evidence-dialog-item">
          <div class="evidence-meta">
            <span>{{ ev.file_name }}</span>
            <el-tag size="small" type="info" effect="plain">第 {{ ev.page_number }} 页</el-tag>
            <el-tag size="small" type="info" effect="plain">切片 #{{ ev.chunk_index }}</el-tag>
          </div>
          <p class="evidence-dialog-text" v-html="highlightFull(ev.text, evidenceDialog.entity)"></p>
        </div>
      </div>
      <el-empty v-else description="暂无 chunk 证据" :image-size="80" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import { getKnowledgeBase } from '@/api/knowledgeBase'
import { getGraphStats, getGraphMeta, getGraphData, buildKbGraph, clearKbGraph, searchGraph, getEntityDetail, expandGraphNode, listGraphEntities, updateGraphEntity } from '@/api/graph'
import { listDomains, bindKbDomain } from '@/api/domain'

const route = useRoute()
const router = useRouter()
const kbId = route.params.kbId

// --- State ---
const kbName = ref('')
// v2 领域模板 meta：生效模板类型/颜色驱动图例与类型下拉
const metaDomain = ref(null)
const metaEntityTypes = ref([])
const domainDialog = ref({ visible: false, loading: false, saving: false, domains: [], current: '', target: '' })
const stats = ref({ graph_exists: false, stats: {}, build: {} })
const build = ref({ doc_total: 0, built: 0 })
const graphLoaded = ref(false)
const chartRef = ref(null)
const searchText = ref('')
const searching = ref(false)
const building = ref(false)
const loadingMore = ref(false)
const detail = ref(null)
const searchResult = ref({ nodes: [], edges: [], evidence: [] })
// 分页加载：已加载节点名 + 聚合后的图谱数据
const loadedNames = ref([])
const graphData = ref({ nodes: [], edges: [], total_nodes: 0, has_more: false })
// 分级展开：已展开过的节点名（画布上以边框标记）
const expandedNames = ref([])
const expanding = ref(false)
// chunk 原文弹窗（点击"查看原文"在当前页打开，不跳转）
const chunkDialog = ref({ visible: false, ev: null, entity: '', title: '' })
// 右侧卡片标签页：entity-list（实体列表） / detail（详情）
const rightTab = ref('entity-list')
// 实体分页列表
const listQuery = ref('')
const listLoading = ref(false)
const entityList = ref({ total: 0, items: [] })
const entityListPage = ref(1)
const entityListPageSize = ref(50)
// 实体编辑弹窗
const editDialog = ref({ visible: false, saving: false, form: null })
const editFormRef = ref(null)
const editRules = {
  name: [{ required: true, message: '请输入实体名', trigger: 'blur' }],
  type: [{ required: true, message: '请选择实体类型', trigger: 'change' }],
}
// 通用本体 9 类（docs/09 §3.1，编辑弹窗类型下拉）
const ENTITY_TYPE_OPTIONS = ['Organization', 'Person', 'Place', 'Event', 'Time', 'Project', 'Product', 'Quantity', 'Concept']
// 实体 chunk 证据弹窗（列表 chunk 数点击）
const evidenceDialog = ref({ visible: false, entity: '', items: [] })
let chart = null

// 类型配色（v2：领域模板 meta 颜色优先，以下为内置预设兜底色板）
const TYPE_COLORS = {
  Organization: '#409EFF',
  Project: '#67C23A',
  Person: '#E6A23C',
  Event: '#FF7043',
  Place: '#00BFA5',
  Time: '#909399',
  Product: '#F56C6C',
  Quantity: '#B37FEB',
  Concept: '#9C27B0',
  Money: '#F56C6C',
  Date: '#909399',
  Location: '#00BFA5',
  Qualification: '#9C27B0',
}
const typeColorMap = ref({ ...TYPE_COLORS })

function typeColor(type) {
  return typeColorMap.value[type] || '#909399'
}

// 实体编辑/筛选类型下拉：生效模板 code + 中文名（模板外旧数据不展示为可选项）
const entityTypeOptions = computed(() => {
  if (metaEntityTypes.value.length) {
    return metaEntityTypes.value.map((t) => ({
      code: t.code,
      label: `${t.name}（${t.code}）`,
    }))
  }
  return ENTITY_TYPE_OPTIONS.map((c) => ({ code: c, label: c }))
})

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/knowledge-bases')
}

function openChunk(ev, entity) {
  chunkDialog.value = {
    visible: true,
    ev,
    entity: entity || '',
    title: `切片原文${entity ? ' — ' + entity : ''}`,
  }
}

function highlight(text, name) {
  if (!name) return escapeHtml(text)
  const idx = text.indexOf(name)
  if (idx === -1) return escapeHtml(text)
  const prefix = idx > 60 ? '…' + escapeHtml(text.slice(idx - 60, idx)) : escapeHtml(text.slice(0, idx))
  const suffix = escapeHtml(text.slice(idx + name.length, idx + name.length + 160))
  return `${prefix}<mark>${escapeHtml(name)}</mark>${suffix}${text.length > idx + name.length + 160 ? '…' : ''}`
}

function highlightFull(text, name) {
  /* 全文渲染并高亮实体所有出现位置（弹窗用） */
  const safe = escapeHtml(text)
  if (!name) return safe
  const escaped = escapeHtml(name)
  const re = new RegExp(escaped.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')
  if (!re.test(safe)) return safe
  return safe.replace(re, '<mark>$&</mark>')
}

function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// --- 图渲染 ---
function renderChart(nodes, edges) {
  graphLoaded.value = true
  nextTick(() => {
    if (!chartRef.value) return
    if (!chart) chart = echarts.init(chartRef.value)

    // v2：分类 = 实际节点类型（模板 meta 配色），不再按固定本体映射/Event 兜底
    const typeSet = [...new Set(nodes.map((n) => n.type))]
    const categories = typeSet.map((t) => ({ name: t, itemStyle: { color: typeColor(t) } }))
    const nodeData = nodes.map((n) => ({
      id: n.id,
      name: n.name,
      value: n.degree,
      symbolSize: Math.min(12 + n.degree * 1.2, 44),
      category: n.type,
      itemStyle: {
        color: typeColor(n.type),
        // 已展开过的节点加边框标记（分级浏览）
        borderColor: expandedNames.value.includes(n.name) ? '#303133' : typeColor(n.type),
        borderWidth: expandedNames.value.includes(n.name) ? 2 : 0,
      },
      // 核心节点（degree 高）常显标签，其余悬停显示，避免大数据量标签渲染卡顿
      label: { show: n.degree >= 5 },
    }))
    const linkData = edges.map((e) => ({
      source: e.source,
      target: e.target,
      label: { show: true, formatter: e.type, fontSize: 9 },
    }))

    chart.setOption(
      {
        // 大数据量下关闭动画（物理布局 + 动画是卡顿主因）
        animation: false,
        tooltip: {
          formatter: (p) =>
            p.dataType === 'node'
              ? `${p.data.name}<br/>类型: ${p.data.category}<br/>关联数: ${p.data.value}`
              : `${p.data.label.formatter}`,
        },
        legend: { data: categories.map((c) => c.name), top: 0, textStyle: { fontSize: 11 } },
        series: [
          {
            type: 'graph',
            layout: 'force',
            data: nodeData,
            links: linkData,
            categories,
            roam: true,
            draggable: true,
            // 物理参数调优：摩擦/斥力降低震荡，收敛更快
            force: { repulsion: 160, edgeLength: [50, 140], gravity: 0.08, friction: 0.6 },
            label: { show: false, position: 'bottom', fontSize: 10 },
            labelLayout: { hideOverlap: true },
            emphasis: { focus: 'adjacency', label: { show: true }, lineStyle: { width: 2 } },
            lineStyle: { color: 'source', curveness: 0.08, opacity: 0.35 },
            edgeLabel: { show: false },
          },
        ],
      },
      // 增量加载时保留已布局位置，减少重排开销
      { lazyUpdate: true }
    )
    chart.off('click')
    chart.on('click', (params) => {
      if (params.dataType === 'node') handleEntityClick(params.data.name)
    })
  })
}

// --- 数据加载 ---
async function loadGraphMeta() {
  try {
    const data = await getGraphMeta(kbId)
    const domain = data?.domain
    if (domain) {
      metaDomain.value = domain
      metaEntityTypes.value = domain.entity_types || []
      const map = { ...TYPE_COLORS }
      for (const t of metaEntityTypes.value) {
        if (t.color) map[t.code] = t.color
      }
      typeColorMap.value = map
    }
  } catch { /* meta 拉取失败不影响图谱浏览（保留内置配色） */ }
}

async function openDomainDialog() {
  domainDialog.value.current = metaDomain.value?.id || ''
  domainDialog.value.target = domainDialog.value.current
  domainDialog.value.visible = true
  domainDialog.value.loading = true
  try {
    const data = await listDomains()
    domainDialog.value.domains = data?.items || []
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '加载失败'
    ElMessage.error('加载领域模板失败: ' + msg)
    domainDialog.value.visible = false
  } finally {
    domainDialog.value.loading = false
  }
}

async function saveDomainChange() {
  const target = domainDialog.value.target
  domainDialog.value.saving = true
  try {
    await bindKbDomain(kbId, target)
    domainDialog.value.visible = false
    await loadGraphMeta()
    await loadGraph()
    ElMessage.warning('领域模板已更换，图谱状态已失效——请点击「全量构建」以新本体重建')
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('更换失败: ' + msg)
  } finally {
    domainDialog.value.saving = false
  }
}

async function loadStats() {
  try {
    const data = await getGraphStats(kbId)
    stats.value = data
    build.value = data.build || { doc_total: 0, built: 0 }
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.warning(msg)
  }
}

async function loadGraph() {
  try {
    const data = await getGraphData(kbId)
    graphData.value = data
    loadedNames.value = (data.nodes || []).map((n) => n.name)
    if (data.nodes.length) renderChart(data.nodes, data.edges)
    else graphLoaded.value = false
    await loadStats()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.warning(msg)
  }
}

/** 合并新节点/边到画布（节点按 id、边按 source|type|target 去重），返回新增节点数 */
function mergeGraphData(nodes, edges) {
  const nodeIds = new Set(graphData.value.nodes.map((n) => n.id))
  const newNodes = (nodes || []).filter((n) => !nodeIds.has(n.id))
  const edgeKeys = new Set(graphData.value.edges.map((e) => `${e.source}|${e.type}|${e.target}`))
  const newEdges = (edges || []).filter((e) => !edgeKeys.has(`${e.source}|${e.type}|${e.target}`))
  graphData.value = {
    nodes: [...graphData.value.nodes, ...newNodes],
    edges: [...graphData.value.edges, ...newEdges],
    total_nodes: graphData.value.total_nodes,
    has_more: graphData.value.has_more,
  }
  loadedNames.value = graphData.value.nodes.map((n) => n.name)
  return newNodes.length
}

async function loadMore() {
  loadingMore.value = true
  try {
    const data = await getGraphData(kbId, {
      limit: 150,
      offset: loadedNames.value.length,
      excludeNames: loadedNames.value,
    })
    const added = mergeGraphData(data.nodes, data.edges)
    graphData.value = {
      ...graphData.value,
      total_nodes: data.total_nodes,
      has_more: data.has_more,
    }
    if (added) renderChart(graphData.value.nodes, graphData.value.edges)
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('加载更多失败: ' + msg)
  } finally {
    loadingMore.value = false
  }
}

/** 分级展开：以实体为中心拉取 1-hop 邻居并入画布（逐层浏览） */
async function handleExpand(name) {
  expanding.value = true
  try {
    const data = await expandGraphNode(kbId, name, 100)
    const added = mergeGraphData(data.nodes, data.edges)
    if (!expandedNames.value.includes(name)) expandedNames.value.push(name)
    renderChart(graphData.value.nodes, graphData.value.edges)
    ElMessage.success(`已展开「${name}」${data.nodes.length} 个邻居${added ? '' : '（均在画布中）'}`)
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('展开失败: ' + msg)
  } finally {
    expanding.value = false
  }
}

async function handleSearch() {
  const query = (searchText.value || '').trim()
  if (!query) return
  searching.value = true
  try {
    const data = await searchGraph(kbId, query)
    searchResult.value = data
    if (data.nodes.length) renderChart(data.nodes, data.edges)
    else ElMessage.info('图谱中未找到相关实体')
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('检索失败: ' + msg)
  } finally {
    searching.value = false
  }
}

async function handleEntityClick(name) {
  try {
    detail.value = await getEntityDetail(kbId, name)
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.warning(msg)
  }
}

// --- 实体列表（分页 + 搜索） ---
async function loadEntityList() {
  listLoading.value = true
  try {
    entityList.value = await listGraphEntities(kbId, {
      page: entityListPage.value,
      pageSize: entityListPageSize.value,
      q: listQuery.value.trim(),
    })
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.warning(msg)
  } finally {
    listLoading.value = false
  }
}

function onListPageChange(page) {
  entityListPage.value = page
  loadEntityList()
}

/** 点击实体：1-hop 邻居并入画布 + 切到详情 Tab 展示该实体 */
async function renderEntityToCanvas(name) {
  try {
    const data = await expandGraphNode(kbId, name, 100)
    mergeGraphData(data.nodes, data.edges)
    if (!expandedNames.value.includes(name)) expandedNames.value.push(name)
    renderChart(graphData.value.nodes, graphData.value.edges)
    rightTab.value = 'detail'
    await handleEntityClick(name)
    ElMessage.success(`已渲染「${name}」及其 ${data.nodes.length} 个邻居`)
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('渲染失败: ' + msg)
  }
}

/** 列表 chunk 数点击：拉取该实体全部证据原文弹窗 */
async function showEntityEvidence(name) {
  try {
    const detail = await getEntityDetail(kbId, name)
    evidenceDialog.value = { visible: true, entity: name, items: detail.evidence || [] }
    if (!evidenceDialog.value.items.length) ElMessage.info('该实体暂无 chunk 证据')
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.warning(msg)
  }
}

// --- 实体编辑（名称/类型/描述） ---
function openEditDialog(row) {
  editDialog.value = {
    visible: true,
    saving: false,
    form: {
      old_name: row.name,
      old_type: row.type,
      name: row.name,
      type: row.type,
      description: row.description || '',
    },
  }
}

async function saveEdit() {
  if (!editFormRef.value) return
  try {
    await editFormRef.value.validate()
  } catch {
    return
  }
  const form = editDialog.value.form
  editDialog.value.saving = true
  try {
    await updateGraphEntity(kbId, form)
    ElMessage.success('实体已更新')
    editDialog.value.visible = false
    await loadEntityList()
    // 画布含旧节点（id 变更后失效）→ 重拉第一页保持一致
    const oldId = `${form.old_type}_${form.old_name}`
    if (graphData.value.nodes.some((n) => n.id === oldId)) await loadGraph()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('更新失败: ' + msg)
  } finally {
    editDialog.value.saving = false
  }
}

async function handleBuildKb() {
  try {
    await ElMessageBox.confirm('将清空当前知识库图谱并重新构建全部已解析文件，耗时较长，确定？', '全量构建', {
      confirmButtonText: '开始构建',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  building.value = true
  try {
    await buildKbGraph(kbId)
    ElMessage.success('全量构建已触发，构建中...（页面将自动刷新）')
    startRefresh()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('构建触发失败: ' + msg)
  } finally {
    building.value = false
  }
}

async function handleClearKb() {
  try {
    await ElMessageBox.confirm('将删除该知识库的整个知识图谱（文档不受影响），确定？', '清空图谱', {
      confirmButtonText: '确认清空',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await clearKbGraph(kbId)
    ElMessage.success('图谱已清空')
    graphLoaded.value = false
    detail.value = null
    searchResult.value = { nodes: [], edges: [], evidence: [] }
    graphData.value = { nodes: [], edges: [], total_nodes: 0, has_more: false }
    loadedNames.value = []
    expandedNames.value = []
    await loadStats()
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElMessage.error('清空失败: ' + msg)
  }
}

// --- 构建中自动刷新 ---
let refreshTimer = null
async function refreshLoop() {
  await loadStats()
  const data = await getGraphData(kbId).catch(() => ({ nodes: [] }))
  if (data.nodes.length) {
    graphData.value = data
    loadedNames.value = (data.nodes || []).map((n) => n.name)
    renderChart(data.nodes, data.edges)
  }
  const st = await getGraphStats(kbId).catch(() => ({ build: {} }))
  if (!(st.build && st.build.building > 0)) stopRefresh()
}

function startRefresh() {
  if (refreshTimer) return
  refreshTimer = setInterval(refreshLoop, 3000)
}

function stopRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

onMounted(async () => {
  try {
    const data = await getKnowledgeBase(kbId)
    kbName.value = data?.name || ''
  } catch { /* ignore */ }
  await loadGraphMeta()
  await loadGraph()
  await loadEntityList()
  // 从检索页跳转而来：自动检索目标实体
  const entity = route.query.entity
  if (entity) {
    searchText.value = String(entity)
    await handleSearch()
  }
})

onBeforeUnmount(() => {
  stopRefresh()
  if (chart) {
    chart.dispose()
    chart = null
  }
})
</script>

<style scoped>
.graph-view { max-width: var(--page-max-width, 1400px); }

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-bottom: 12px;
}
.breadcrumb .sep { color: var(--color-border); }
.breadcrumb .current { color: var(--color-text-primary); font-weight: 500; }

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
  gap: 12px;
  flex-wrap: wrap;
}
.header-row h1 { font-size: 22px; font-weight: 600; color: var(--color-text-primary); margin: 0; }
.actions { display: flex; align-items: center; gap: 8px; }

.stat-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}

.build-alert {
  margin-bottom: 12px;
}
.stat-card { text-align: center; }
.stat-num { font-size: 24px; font-weight: 700; color: var(--color-text-primary); }
.stat-num.small { font-size: 12px; display: flex; flex-wrap: wrap; gap: 4px; justify-content: center; }
.stat-label { font-size: 12px; color: var(--color-text-secondary); margin-top: 4px; }
.type-chip { white-space: nowrap; }
.muted { color: var(--color-text-secondary); }

.main-area {
  display: grid;
  grid-template-columns: 1fr 400px;
  gap: 12px;
}
.chart-card { min-height: 520px; }
.chart { width: 100%; height: 520px; }
.chart-tip { display: flex; align-items: center; justify-content: center; height: 520px; }
.load-more-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 10px 0 2px;
}
.load-more-hint { font-size: 12px; color: var(--color-text-secondary); }
.detail-card { min-height: 520px; max-height: 600px; overflow-y: auto; }
.detail-card :deep(.el-tabs__content) { overflow: visible; }

/* 实体列表 Tab */
.entity-list-pane { display: flex; flex-direction: column; gap: 8px; }
.list-search { width: 100%; }
.list-footer { display: flex; justify-content: flex-end; margin-top: 4px; }

/* 实体证据弹窗 */
.evidence-dialog-list { max-height: 60vh; overflow-y: auto; }
.evidence-dialog-item { margin-bottom: 12px; }
.evidence-dialog-text {
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text-primary);
  max-height: 180px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--color-fill-light);
  padding: 8px 10px;
  border-radius: 4px;
  margin: 4px 0 0;
}
.evidence-dialog-text :deep(mark) { background: #ffe58f; padding: 0 2px; border-radius: 2px; }

.entity-title { display: flex; align-items: center; gap: 8px; }
.entity-name { font-size: 16px; font-weight: 600; }
.entity-desc { font-size: 12px; color: var(--color-text-secondary); margin: 6px 0 0; }

.neighbor-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  cursor: pointer;
  font-size: 13px;
}
.neighbor-item:hover { background: var(--color-fill-light); }
.nb-name { flex: 1; }
.nb-rel { color: var(--color-text-secondary); font-size: 12px; }

.evidence-item { margin-bottom: 10px; }
.evidence-meta { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--color-text-secondary); }
.evidence-text {
  font-size: 13px;
  color: var(--color-text-regular);
  background: var(--color-fill-light);
  padding: 6px 8px;
  border-radius: 4px;
  margin: 4px 0 0;
  max-height: 84px;
  overflow: hidden;
}
.evidence-text :deep(mark) { background: #ffe58f; padding: 0 2px; border-radius: 2px; }

.search-evidence { margin-top: 12px; }
.search-evidence-group { margin-bottom: 12px; }
.search-evidence-title {
  font-weight: 600;
  color: var(--color-primary);
  cursor: pointer;
  margin-bottom: 4px;
  font-size: 14px;
}

.chunk-dialog-meta { display: flex; gap: 6px; margin-bottom: 10px; }
.chunk-dialog-text {
  font-size: 14px;
  line-height: 1.8;
  color: var(--color-text-primary);
  max-height: 55vh;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--color-fill-light);
  padding: 12px;
  border-radius: 6px;
  margin: 0;
}
.chunk-dialog-text :deep(mark) { background: #ffe58f; padding: 0 2px; border-radius: 2px; }
</style>
