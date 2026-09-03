<template>
  <div class="search-view">
    <!-- 左侧参数面板 -->
    <div class="search-left">
      <div class="panel-header">
        <h1>文档检索</h1>
        <p class="desc">基于语义搜索知识库中的文档片段，无需精确匹配关键词</p>
      </div>

      <div class="param-card">
        <div class="param-group">
          <label class="param-label">知识库</label>
          <el-select
            v-model="kbIds"
            placeholder="选择知识库（支持多选）"
            multiple
            collapse-tags
            collapse-tags-tooltip
            @change="handleKbChange"
          >
            <el-option
              v-for="kb in kbList"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id"
            />
          </el-select>
        </div>

        <div class="param-group">
          <label class="param-label">检索内容</label>
          <el-input
            v-model="query"
            type="textarea"
            :rows="4"
            placeholder="输入检索关键词或自然语言描述..."
            @keyup.enter="handleSearch"
          />
        </div>

        <div class="param-group">
          <label class="param-label">检索策略（可多选，多选按 RRF 融合）</label>
          <el-checkbox-group v-model="retrievalMethods">
            <el-checkbox value="dense">稠密向量</el-checkbox>
            <el-checkbox value="sparse">稀疏向量</el-checkbox>
            <el-checkbox value="fulltext">全文检索</el-checkbox>
            <el-checkbox value="graph">知识图谱</el-checkbox>
          </el-checkbox-group>
        </div>

        <div class="param-group">
          <label class="param-label">
            Top K
            <span class="param-hint">{{ topK }}</span>
          </label>
          <el-slider
            v-model="topK"
            :min="1"
            :max="50"
            :step="1"
            show-stops
          />
        </div>

        <div class="param-group">
          <label class="param-label">
            相似度阈值
            <span class="param-hint">{{ (similarityThreshold * 100).toFixed(0) }}%</span>
          </label>
          <el-slider
            v-model="similarityThreshold"
            :min="0"
            :max="1"
            :step="0.05"
            :format-tooltip="(v) => (v * 100).toFixed(0) + '%'"
            show-stops
          />
        </div>

        <div class="param-group">
          <label class="param-label">排序模型（可选）</label>
          <el-select
            v-model="rerankModelId"
            placeholder="不使用重排"
            clearable
            style="width: 100%"
          >
            <el-option
              v-for="m in rerankModels"
              :key="m.id"
              :label="`${m.name}（${m.model}）`"
              :value="m.id"
            />
          </el-select>
        </div>

        <template v-if="rerankModelId">
          <div class="param-group">
            <label class="param-label">
              进入排序模型候选数
              <span class="param-hint">{{ rerankTopN }}</span>
            </label>
            <el-slider
              v-model="rerankTopN"
              :min="0"
              :max="30"
              :step="1"
              show-stops
            />
          </div>
          <div class="param-group">
            <span class="param-hint-sub">向量门槛 = 相似度阈值；有排序模型时全量召回交给排序模型</span>
          </div>
        </template>

        <el-button
          type="primary"
          :loading="searching"
          :icon="Search"
          size="large"
          style="width: 100%"
          @click="handleSearch"
        >
          开始检索
        </el-button>
      </div>
    </div>

    <!-- 右侧结果面板 -->
    <div class="search-right">
      <!-- 未搜索时的引导 -->
      <div v-if="!searched" class="empty-guide">
        <el-empty description="选择知识库并输入检索词，开始语义搜索" :image-size="120" />
      </div>

      <!-- 搜索结果 -->
      <template v-else>
        <div class="results-header">
          <span>检索结果<span class="count">（共 {{ total }} 条，耗时 {{ elapsed }}ms）</span></span>
        </div>

        <div v-if="results.length > 0" class="results-list">
          <div v-for="item in results" :key="item.rank" class="result-card">
            <div class="result-header">
              <div class="result-score">
                <span class="score-label">相关性</span>
                <template v-if="showPercentScore">
                  <div class="score-bar">
                    <div class="score-fill" :style="{ width: (item.score * 100).toFixed(0) + '%' }" />
                  </div>
                  <span class="score-num">{{ (item.score * 100).toFixed(0) }}%</span>
                </template>
                <template v-else>
                  <span class="score-num">{{ Number(item.score).toFixed(4) }}</span>
                </template>
              </div>
              <span class="result-rank">#{{ item.rank }}</span>
            </div>
            <div
              class="result-text"
              :class="{ 'is-expanded': item.expanded }"
              v-html="item.highlight ? item.highlight : highlightText(item.text, query)"
            />
            <div class="expand-toggle">
              <el-button text type="primary" size="small" @click="toggleExpand(item)">
                <el-icon><ArrowDown v-if="!item.expanded" /><ArrowUp v-else /></el-icon>
                {{ item.expanded ? '收起' : '展开全文' }}
              </el-button>
            </div>
            <div class="result-meta">
              <span><el-icon><Folder /></el-icon> {{ item.kb_name || '未知知识库' }}</span>
              <span><el-icon><Document /></el-icon> {{ item.file_name }}</span>
              <span><el-icon><Location /></el-icon> 第 {{ item.page_number }} 页</span>
              <el-tag v-if="item.graph_entity" type="success" size="small" class="graph-tag" @click="goGraph(item)">
                图谱·{{ item.graph_entity }}
              </el-tag>
            </div>
          </div>
        </div>

        <el-empty v-else description="未找到相关内容，请尝试其他检索词" :image-size="80" />
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Search, ArrowDown, ArrowUp } from '@element-plus/icons-vue'
import { ElMessage, ElNotification } from 'element-plus'
import { listKnowledgeBases } from '@/api/knowledgeBase'
import { listModels } from '@/api/model'
import { search as searchDocuments } from '@/api/search'

// --- State ---
const kbList = ref([])
const kbIds = ref([])
const query = ref('')
const results = ref([])
const total = ref(0)
const searched = ref(false)
const searching = ref(false)
const elapsed = ref(0)
const topK = ref(10)
const similarityThreshold = ref(0.6)
const retrievalMethods = ref(['dense'])
const rerankModels = ref([])
const rerankModelId = ref(null)
const rerankTopN = ref(5)

// 分数是否按百分比展示：rerank 或单选稠密向量时分数为 [0,1]；否则（稀疏/全文/图谱单选或 RRF 融合）显示原始数字
const showPercentScore = computed(() =>
  Boolean(rerankModelId.value) ||
  (retrievalMethods.value.length === 1 && retrievalMethods.value[0] === 'dense')
)

// --- Methods ---
function handleKbChange() {
  results.value = []
  searched.value = false
}

/** 从图谱证据跳转到知识图谱页 */
function goGraph(item) {
  const kbId = item.kb_id
  if (!kbId) return
  const entity = (item.graph_entity || '').split('、')[0]
  const url = entity
    ? `/knowledge-bases/${kbId}/graph?entity=${encodeURIComponent(entity)}`
    : `/knowledge-bases/${kbId}/graph`
  window.open(url, '_blank')
}

async function handleSearch() {
  if (kbIds.value.length === 0) {
    ElMessage.warning('请先选择知识库')
    return
  }
  if (!query.value.trim()) {
    ElMessage.warning('请输入检索内容')
    return
  }

  if (retrievalMethods.value.length === 0) {
    ElMessage.warning('请至少选择一种检索策略')
    return
  }

  searching.value = true
  searched.value = true
  const t0 = performance.now()

  try {
    const data = await searchDocuments({
      kb_ids: kbIds.value,
      query: query.value.trim(),
      top_k: topK.value,
      similarity_threshold: similarityThreshold.value,
      retrieval_methods: retrievalMethods.value,
      rerank_model_id: rerankModelId.value || null,
      rerank_top_n: rerankTopN.value,
      highlight: true,
    })
    results.value = (data?.results || []).map((r, i) => ({ ...r, rank: i + 1, expanded: false }))
    total.value = data?.total || 0
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '检索失败'
    ElMessage.error(msg)
    results.value = []
    total.value = 0
  } finally {
    searching.value = false
    elapsed.value = (performance.now() - t0).toFixed(0)
  }
}

function toggleExpand(item) {
  item.expanded = !item.expanded
}

function highlightText(text, keyword) {
  if (!text || !keyword) return text
  const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const regex = new RegExp(`(${escaped})`, 'gi')
  return text.replace(regex, '<mark>$1</mark>')
}

onMounted(async () => {
  try {
    const data = await listKnowledgeBases()
    kbList.value = data || []
  } catch (err) {
    const msg = err?.response?.data?.detail || err?.message || '未知错误'
    ElNotification.error({ title: '加载知识库列表失败', message: msg })
  }
  try {
    rerankModels.value = (await listModels({ model_type: 'rerank' })) || []
  } catch {
    rerankModels.value = []
  }
})
</script>

<style scoped>
.search-view {
  display: flex;
  gap: 24px;
  height: calc(100vh - 80px);
  max-width: var(--page-max-width, 1400px);
}

/* ===== 左侧面板 ===== */
.search-left {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
}

.panel-header h1 {
  font-size: 20px;
  margin: 0 0 4px;
  color: var(--color-text-primary);
}

.panel-header .desc {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin: 0;
  line-height: 1.5;
}

.param-card {
  background: #fff;
  border-radius: var(--radius-md);
  padding: 20px;
  box-shadow: var(--shadow-card);
  border: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.param-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.param-group .param-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-primary);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.param-group .param-hint {
  font-size: 12px;
  font-weight: 500;
  color: var(--color-primary);
}

.param-group .param-hint-sub {
  font-size: 12px;
  color: var(--color-text-secondary);
}

/* ===== 右侧结果面板 ===== */
.search-right {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
}

.empty-guide {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.results-header {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: 16px;
  position: sticky;
  top: 0;
  background: var(--color-bg);
  padding: 8px 0;
  z-index: 1;
}

.results-header .count {
  font-weight: 400;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.results-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 24px;
}

.result-card {
  background: #fff;
  border-radius: var(--radius-md);
  padding: 20px 24px;
  box-shadow: var(--shadow-card);
  border: 1px solid var(--color-border);
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.result-score {
  display: flex;
  align-items: center;
  gap: 8px;
}

.score-label {
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.score-bar {
  width: 120px;
  height: 6px;
  background: #E5E6EB;
  border-radius: 3px;
  overflow: hidden;
}

.score-fill {
  height: 100%;
  background: var(--color-primary);
  border-radius: 3px;
  transition: width 0.4s ease;
}

.score-num {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-primary);
}

.result-rank {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.result-text {
  font-size: 14px;
  line-height: 1.8;
  color: var(--color-text-regular);
  max-height: 120px;
  overflow: hidden;
  position: relative;
}

.result-text::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 40px;
  background: linear-gradient(transparent, #fff);
}

.result-text.is-expanded {
  max-height: none;
  overflow: visible;
}

.result-text.is-expanded::after {
  display: none;
}

.expand-toggle {
  margin-top: 4px;
}

.result-text :deep(mark) {
  background: #D1FAE5;
  color: var(--color-primary-dark);
  padding: 0 3px;
  border-radius: 3px;
}

.result-text :deep(em) {
  background: #D1FAE5;
  color: var(--color-primary-dark);
  padding: 0 3px;
  border-radius: 3px;
  font-style: normal;
}

.result-meta {
  display: flex;
  gap: 20px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
  flex-wrap: wrap;
}

.result-meta span {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.graph-tag {
  cursor: pointer;
}
</style>
