<template>
  <div class="search-view">
    <div class="page-header">
      <h1>文档检索</h1>
      <p class="desc">基于语义搜索知识库中的文档片段，无需精确匹配关键词</p>
    </div>

    <!-- 搜索栏 -->
    <div class="search-bar">
      <div class="search-row">
        <el-select
          v-model="kbId"
          placeholder="选择知识库"
          style="width: 220px"
          @change="handleKbChange"
        >
          <el-option
            v-for="kb in kbList"
            :key="kb.id"
            :label="kb.name"
            :value="kb.id"
          />
        </el-select>
        <el-input
          v-model="query"
          placeholder="输入检索关键词或自然语言描述..."
          size="large"
          clearable
          @keyup.enter="handleSearch"
        >
          <template #append>
            <el-button
              type="primary"
              :loading="searching"
              :icon="Search"
              @click="handleSearch"
            >
              检索
            </el-button>
          </template>
        </el-input>
      </div>
    </div>

    <!-- 结果区域 -->
    <div v-if="searched" class="results-area">
      <div class="results-header">
        检索结果<span class="count">（共 {{ total }} 条，耗时 {{ elapsed }}ms）</span>
      </div>

      <div v-if="results.length > 0" class="results-list">
        <div v-for="item in results" :key="item.rank" class="result-card">
          <div class="result-header">
            <div class="result-score">
              <span class="score-label">相关性</span>
              <div class="score-bar">
                <div class="score-fill" :style="{ width: (item.score * 100).toFixed(0) + '%' }" />
              </div>
              <span class="score-num">{{ (item.score * 100).toFixed(0) }}%</span>
            </div>
            <span class="result-rank">#{{ item.rank }}</span>
          </div>
          <div class="result-text" v-html="highlightText(item.text, query)" />
          <div class="result-meta">
            <span><el-icon><Document /></el-icon> {{ item.file_name }}</span>
            <span><el-icon><Location /></el-icon> 第 {{ item.page_number }} 页</span>
          </div>
        </div>

        <el-pagination
          v-if="total > pageSize"
          :current-page="currentPage"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="onPageChange"
        />
      </div>

      <el-empty v-else description="未找到相关内容，请尝试其他检索词" :image-size="80" />
    </div>

    <!-- 未搜索时的引导 -->
    <el-card v-if="!searched">
      <el-empty description="选择知识库并输入检索词，开始语义搜索" :image-size="100" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { listKnowledgeBases } from '@/api/knowledgeBase'
import { search as searchDocuments } from '@/api/search'

// --- State ---
const kbList = ref([])
const kbId = ref(null)
const query = ref('')
const results = ref([])
const total = ref(0)
const searched = ref(false)
const searching = ref(false)
const elapsed = ref(0)
const currentPage = ref(1)
const pageSize = 20

// --- Methods ---
function handleKbChange() {
  results.value = []
  searched.value = false
}

async function handleSearch() {
  if (!kbId.value) {
    ElMessage.warning('请先选择知识库')
    return
  }
  if (!query.value.trim()) {
    ElMessage.warning('请输入检索内容')
    return
  }

  searching.value = true
  searched.value = true
  const t0 = performance.now()

  try {
    const data = await searchDocuments(kbId.value, {
      query: query.value.trim(),
      top_k: 10,
      offset: (currentPage.value - 1) * pageSize.value,
      limit: pageSize.value,
    })
    results.value = (data?.results || []).map((r, i) => ({ ...r, rank: i + 1 }))
    total.value = data?.total || 0
  } catch {
    results.value = []
    total.value = 0
  } finally {
    searching.value = false
    elapsed.value = (performance.now() - t0).toFixed(0)
  }
}

function onPageChange(page) {
  currentPage.value = page
  handleSearch()
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
  } catch {
    kbList.value = []
  }
})
</script>

<style scoped>
.search-view { max-width: var(--page-max-width, 1400px); }

.search-bar {
  background: #fff;
  padding: 24px;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-card);
  border: 1px solid var(--color-border);
  margin-bottom: 24px;
}

.search-row {
  display: flex;
  gap: 12px;
}

.search-row .el-input { flex: 1; }

.results-header {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: 16px;
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

.result-text :deep(mark) {
  background: #D1FAE5;
  color: var(--color-primary-dark);
  padding: 0 3px;
  border-radius: 3px;
}

.result-meta {
  display: flex;
  gap: 20px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}

.result-meta span {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--color-text-secondary);
}
</style>
