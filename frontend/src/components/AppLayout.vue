<template>
  <el-container class="app-layout">
    <!-- 侧边栏 -->
    <el-aside width="220px" class="app-aside">
      <div class="app-logo">
        <h2>📄 投标RAG</h2>
      </div>
      <el-menu
        :default-active="activeRoute"
        router
        class="app-menu"
      >
        <el-menu-item index="/knowledge-bases">
          <el-icon><Folder /></el-icon>
          <span>知识库管理</span>
        </el-menu-item>
        <el-menu-item index="/search">
          <el-icon><Search /></el-icon>
          <span>文档检索</span>
        </el-menu-item>
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>智能问答</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <!-- 主内容区 -->
    <el-container>
      <el-header class="app-header">
        <span class="app-title">投标文档 RAG 智能问答系统</span>
      </el-header>
      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const activeRoute = computed(() => route.path)
</script>

<style scoped>
.app-layout {
  height: 100vh;
}

.app-aside {
  background-color: #304156;
  overflow-y: auto;
}

.app-logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.app-logo h2 {
  color: #fff;
  font-size: 18px;
  font-weight: 600;
}

.app-menu {
  border-right: none;
}

.app-header {
  display: flex;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 24px;
}

.app-title {
  font-size: 16px;
  color: #303133;
  font-weight: 500;
}

.app-main {
  background: #f5f7fa;
  padding: 24px;
}
</style>
