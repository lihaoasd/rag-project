<template>
  <el-container class="app-layout">
    <!-- 侧边栏（浅色） -->
    <el-aside width="220px" class="app-aside">
      <div class="app-logo">
        <div class="logo-icon">
          <el-icon :size="20"><Document /></el-icon>
        </div>
        <span>投标文档 RAG</span>
      </div>
      <el-menu
        :default-active="activeRoute"
        router
        background-color="transparent"
        text-color="var(--sidebar-text)"
        active-text-color="var(--sidebar-active-text)"
        class="app-menu"
      >
        <el-menu-item index="/knowledge-bases">
          <template #title>
            <el-icon><Folder /></el-icon>
            <span>知识库管理</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/search">
          <template #title>
            <el-icon><Search /></el-icon>
            <span>文档检索</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/chat">
          <template #title>
            <el-icon><ChatDotRound /></el-icon>
            <span>智能问答</span>
          </template>
        </el-menu-item>
        <el-menu-item index="/prompts">
          <template #title>
            <el-icon><EditPen /></el-icon>
            <span>提示词管理</span>
          </template>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <!-- 主内容 -->
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
.app-layout { height: 100vh; }

.app-aside {
  background: var(--sidebar-bg);
  border-right: 1px solid var(--color-border);
  overflow-y: auto;
}

.app-logo {
  height: 56px;
  display: flex;
  align-items: center;
  padding: 0 20px;
  gap: 10px;
  border-bottom: 1px solid var(--color-border);
}

.logo-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--color-primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.app-logo span {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: -0.3px;
}

.app-menu {
  padding: 12px 8px;
}

:deep(.el-menu-item) {
  border-radius: 8px;
  margin-bottom: 2px;
  height: 42px;
  line-height: 42px;
  font-size: 14px;
  transition: all 0.15s;
}

:deep(.el-menu-item:hover) {
  background-color: #F1F5F9 !important;
  color: var(--color-text-primary) !important;
}

:deep(.el-menu-item.is-active) {
  background-color: var(--sidebar-active) !important;
  color: var(--sidebar-active-text) !important;
  font-weight: 600;
}

.app-header {
  display: flex;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid var(--color-border);
  padding: 0 24px;
  height: var(--header-height);
}

.app-title {
  font-size: 14px;
  color: var(--color-text-secondary);
  font-weight: 500;
}

.app-main {
  background: var(--color-bg);
  padding: 24px;
}
</style>
