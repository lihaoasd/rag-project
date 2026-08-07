<template>
  <div class="doc-view">
    <div class="page-header">
      <h1>文档管理</h1>
      <p class="desc">上传和管理知识库中的投标文档</p>
    </div>

    <el-card>
      <template #header>
        <div class="card-header">
          <span>文档列表</span>
          <el-button type="primary">
            <el-icon><Upload /></el-icon>
            上传文档
          </el-button>
        </div>
      </template>
      <el-empty description="请先选择一个知识库" :image-size="120" />
    </el-card>
  </div>
</template>

<script setup>
</script>

<style scoped>
.doc-view { max-width: 1200px; }
.page-header { margin-bottom: 24px; }
.page-header h1 { font-size: 24px; color: #303133; margin-bottom: 8px; }
.page-header .desc { color: #909399; font-size: 14px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>
