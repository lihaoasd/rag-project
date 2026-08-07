<template>
  <div class="kb-view">
    <div class="page-header">
      <h1>知识库管理</h1>
      <p class="desc">管理文档知识库，每个知识库内的文档相互隔离</p>
    </div>

    <el-card>
      <template #header>
        <div class="card-header">
          <span>知识库列表</span>
          <el-button type="primary" @click="handleCreate">
            <el-icon><Plus /></el-icon>
            创建知识库
          </el-button>
        </div>
      </template>
      <el-empty description="功能开发中，敬请期待" :image-size="120" />
    </el-card>
  </div>
</template>

<script setup>
const handleCreate = () => {
  // Phase 2: 打开创建对话框
}
</script>

<style scoped>
.kb-view { max-width: 1200px; }
.page-header { margin-bottom: 24px; }
.page-header h1 { font-size: 24px; color: #303133; margin-bottom: 8px; }
.page-header .desc { color: #909399; font-size: 14px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>
