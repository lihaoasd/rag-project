<template>
  <div class="search-view">
    <div class="page-header">
      <h1>文档检索</h1>
      <p class="desc">基于语义搜索知识库中的文档片段</p>
    </div>

    <el-card>
      <div class="search-bar">
        <el-input
          v-model="query"
          placeholder="输入检索关键词或自然语言描述..."
          size="large"
          clearable
          @keyup.enter="handleSearch"
        >
          <template #append>
            <el-button type="primary" :icon="Search" @click="handleSearch">
              检索
            </el-button>
          </template>
        </el-input>
      </div>
      <el-empty description="功能开发中，敬请期待" :image-size="120" />
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Search } from '@element-plus/icons-vue'

const query = ref('')

const handleSearch = () => {
  // Phase 2: 调用检索 API
}
</script>

<style scoped>
.search-view { max-width: 1200px; }
.page-header { margin-bottom: 24px; }
.page-header h1 { font-size: 24px; color: #303133; margin-bottom: 8px; }
.page-header .desc { color: #909399; font-size: 14px; }
.search-bar { margin-bottom: 24px; }
</style>
