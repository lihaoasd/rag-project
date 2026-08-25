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
        <template #header>
          <div class="detail-header">
            <span>实体详情</span>
            <el-button v-if="detail?.entity" text type="primary" size="small" @click="detail = null">关闭</el-button>
          </div>
        </template>
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
        <el-empty v-else description="点击图谱节点查看实体详情与证据" :image-size="80" />
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
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import { getKnowledgeBase } from '@/api/knowledgeBase'
import { getGraphStats, getGraphData, buildKbGraph, clearKbGraph, searchGraph, getEntityDetail, expandGraphNode } from '@/api/graph'

const route = useRoute()
const router = useRouter()
const kbId = route.params.kbId

// --- State ---
const kbName = ref('')
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
let chart = null

// 类型配色
const TYPE_COLORS = {
  Organization: '#409EFF',
  Project: '#67C23A',
  Person: '#E6A23C',
  Money: '#F56C6C',
  Date: '#909399',
  Location: '#00BFA5',
  Qualification: '#9C27B0',
  Event: '#FF7043',
}

function typeColor(type) {
  return TYPE_COLORS[type] || '#909399'
}

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

    const categories = Object.keys(TYPE_COLORS).map((t) => ({ name: t }))
    const nodeData = nodes.map((n) => ({
      id: n.id,
      name: n.name,
      value: n.degree,
      symbolSize: Math.min(12 + n.degree * 1.2, 44),
      category: TYPE_COLORS[n.type] ? n.type : 'Event',
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
  await loadGraph()
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
  grid-template-columns: 1fr 340px;
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
.detail-header { display: flex; justify-content: space-between; align-items: center; }

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
