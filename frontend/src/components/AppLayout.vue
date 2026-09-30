<template>
  <el-container class="app-layout">
    <!-- 毛玻璃图标导航竖栏 -->
    <el-aside :width="collapsed ? 'var(--aside-collapsed)' : 'var(--aside-width)'" class="app-aside">
      <div class="aside-inner">
        <div class="app-logo" @click="collapsed = false">
          <div class="logo-icon">
            <el-icon :size="20"><Document /></el-icon>
          </div>
          <transition name="fade">
            <div v-if="!collapsed" class="logo-text">
              <span class="logo-name">文档 RAG</span>
              <span class="logo-sub">RAG CONSOLE</span>
            </div>
          </transition>
        </div>

        <el-menu
          :default-active="activeRoute"
          :collapse="collapsed"
          :collapse-transition="true"
          router
          class="app-menu"
        >
          <template v-for="group in navGroups" :key="group">
            <div v-if="!collapsed" class="menu-section">{{ group }}</div>
            <div v-else class="menu-section-dot"></div>
            <el-menu-item
              v-for="item in navItems.filter(i => i.group === group)"
              :key="item.path"
              :index="item.path"
            >
              <el-icon><component :is="item.icon" /></el-icon>
              <template #title><span>{{ item.title }}</span></template>
            </el-menu-item>
          </template>
        </el-menu>
      </div>

      <div class="aside-footer">
        <el-tooltip :content="collapsed ? '展开导航' : '收起导航'" placement="right">
          <button class="collapse-btn" @click="collapsed = !collapsed">
            <el-icon :size="15"><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon>
          </button>
        </el-tooltip>
        <div v-if="!collapsed" class="aside-status">
          <div class="status-line"><span class="dot"></span><span>系统运行正常</span></div>
          <div class="status-ver">RAG Console · v2.0</div>
        </div>
      </div>
    </el-aside>

    <!-- 主区域 -->
    <el-container class="app-body">
      <el-header class="app-header">
        <div class="header-left">
          <span class="crumb">工作台</span>
          <el-icon :size="12"><ArrowRight /></el-icon>
          <span class="crumb current">{{ pageTitle }}</span>
          <span v-if="pageSubtitle" class="header-sub">{{ pageSubtitle }}</span>
        </div>
        <div class="header-right">
          <button class="search-trigger" @click="openSearch">
            <el-icon :size="15"><Search /></el-icon>
            <span class="search-text">全局搜索页面</span>
            <span class="kbd">Ctrl K</span>
          </button>
          <span class="header-divider"></span>
          <div class="header-time">
            <el-icon :size="16"><Clock /></el-icon>
            <span>{{ now }}</span>
          </div>
          <span class="header-tag">AI</span>
        </div>
      </el-header>

      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>

    <!-- 全局快捷搜索 -->
    <el-dialog
      v-model="searchOpen"
      width="520px"
      :show-close="false"
      class="quick-search"
      align-center
      @closed="searchQuery = ''"
    >
      <div class="qs-input">
        <el-icon :size="18"><Search /></el-icon>
        <input
          ref="qsInput"
          v-model="searchQuery"
          placeholder="输入关键词，快速跳转到页面…"
          @keydown.enter="jump(filtered[0])"
          @keydown.esc="searchOpen = false"
        />
        <span class="kbd">Esc</span>
      </div>
      <div class="qs-list">
        <div
          v-for="(item, idx) in filtered"
          :key="item.path"
          class="qs-item"
          :class="{ active: idx === cursor }"
          @mouseenter="cursor = idx"
          @click="jump(item)"
        >
          <span class="qs-icon"><el-icon :size="16"><component :is="item.icon" /></el-icon></span>
          <span class="qs-title">{{ item.title }}</span>
          <span class="qs-group">{{ item.group }}</span>
        </div>
        <div v-if="!filtered.length" class="qs-empty">未找到匹配页面</div>
      </div>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const activeRoute = computed(() => route.path)
const pageTitle = computed(() => route.meta.title || '控制台')
const pageSubtitle = computed(() => route.meta.subtitle || '')

/* ---------- 收展 ---------- */
const collapsed = ref(false)

/* ---------- 导航 ---------- */
const navItems = [
  { title: '知识库管理', path: '/knowledge-bases', icon: 'Folder', group: '核心' },
  { title: '文档检索', path: '/search', icon: 'Search', group: '核心' },
  { title: '智能问答', path: '/chat', icon: 'ChatDotRound', group: '核心' },
  { title: '提示词管理', path: '/prompts', icon: 'EditPen', group: '配置' },
  { title: '模型配置', path: '/models', icon: 'Cpu', group: '配置' },
  { title: '词库管理', path: '/lexicon', icon: 'Collection', group: '配置' },
  { title: '领域模板', path: '/domains', icon: 'SetUp', group: '配置' },
  { title: 'ES 对账', path: '/es-admin', icon: 'DataAnalysis', group: '配置' },
]
const navGroups = ['核心', '配置']

/* ---------- 时钟 ---------- */
const now = ref('')
let timer = null
function pad(n) { return String(n).padStart(2, '0') }
function tick() {
  const d = new Date()
  now.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}  ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/* ---------- 全局搜索 ---------- */
const searchOpen = ref(false)
const searchQuery = ref('')
const cursor = ref(0)
const qsInput = ref(null)

const filtered = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  const list = q ? navItems.filter(i => i.title.toLowerCase().includes(q)) : navItems
  return list
})

watch(searchQuery, () => { cursor.value = 0 })

function openSearch() {
  searchOpen.value = true
  nextTick(() => qsInput.value && qsInput.value.focus())
}

function jump(item) {
  if (!item) return
  searchOpen.value = false
  router.push(item.path)
}

function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault()
    openSearch()
  }
  if (searchOpen.value) {
    if (e.key === 'ArrowDown') { e.preventDefault(); cursor.value = (cursor.value + 1) % filtered.value.length }
    if (e.key === 'ArrowUp') { e.preventDefault(); cursor.value = (cursor.value - 1 + filtered.value.length) % filtered.value.length }
  }
}

onMounted(() => {
  tick()
  timer = setInterval(tick, 1000)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped>
.app-layout { height: 100vh; }

/* ============ 侧边栏（毛玻璃） ============ */
.app-aside {
  position: relative;
  flex-shrink: 0;
  height: 100vh;
  background: var(--glass-bg);
  border-right: 1px solid var(--glass-border);
  box-shadow: 8px 0 30px -18px rgba(15, 23, 42, 0.18);
  transition: width 0.28s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.aside-inner { flex: 1; display: flex; flex-direction: column; min-height: 0; }

.app-logo {
  height: var(--header-height);
  display: flex;
  align-items: center;
  padding: 0 18px;
  gap: 12px;
  cursor: pointer;
  flex-shrink: 0;
  white-space: nowrap;
}

.logo-icon {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  background-image: var(--grad-accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 8px 18px -6px rgba(34, 211, 238, 0.6);
}

.logo-text { display: flex; flex-direction: column; line-height: 1.2; }

.logo-name {
  font-size: 16px;
  font-weight: 800;
  color: var(--color-text-primary);
  letter-spacing: 0.5px;
}

.logo-sub {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 2.5px;
  color: var(--color-text-secondary);
  margin-top: 2px;
}

/* 菜单 */
.app-menu {
  background: transparent !important;
  border: none !important;
  padding: 6px 10px;
  overflow-y: auto;
  flex: 1;
}

.menu-section {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.5px;
  color: var(--color-text-secondary);
  text-transform: uppercase;
  padding: 18px 14px 8px;
}

.menu-section-dot {
  margin: 14px 14px 10px;
  height: 1px;
  background: var(--color-border);
}

:deep(.el-menu-item) {
  border-radius: 12px;
  margin-bottom: 4px;
  height: 46px;
  line-height: 46px;
  font-size: 14px;
  font-weight: 500;
  color: var(--sidebar-text);
  padding-left: 16px !important;
  transition: all 0.2s ease;
  background: transparent !important;
}

:deep(.el-menu-item .el-icon) { color: var(--sidebar-text); transition: all 0.2s ease; }

:deep(.el-menu-item:hover) {
  background: var(--sidebar-hover-bg) !important;
  color: var(--sidebar-text-hover) !important;
}
:deep(.el-menu-item:hover .el-icon) { color: var(--sidebar-text-hover) !important; }

:deep(.el-menu-item.is-active) {
  background-image: var(--grad-accent) !important;
  color: var(--sidebar-active-text) !important;
  font-weight: 700;
  box-shadow: 0 10px 22px -8px rgba(14, 165, 233, 0.6);
}
:deep(.el-menu-item.is-active .el-icon) { color: var(--sidebar-active-text) !important; }

:deep(.el-menu--collapse) { width: 100%; }
:deep(.el-menu--collapse .el-menu-item) { padding: 0 !important; justify-content: center; }

/* 底部 */
.aside-footer { padding: 12px; flex-shrink: 0; white-space: nowrap; }

.collapse-btn {
  width: 34px;
  height: 34px;
  border: none;
  background: transparent;
  border-radius: 10px;
  color: var(--color-text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto;
  cursor: pointer;
  transition: all 0.2s ease;
}
.collapse-btn:hover { color: var(--color-primary); background: var(--sidebar-hover-bg); }

.aside-status { padding: 10px 6px 2px; }
.status-line { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--color-text-secondary); }
.status-line .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--color-mint); box-shadow: 0 0 8px rgba(52, 211, 153, 0.8); }
.status-ver { margin-top: 6px; font-size: 11px; color: var(--color-text-secondary); letter-spacing: 0.5px; }

/* ============ 主区域 ============ */
.app-body { min-width: 0; }

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--glass-bg);
  border-bottom: 1px solid var(--glass-border);
  padding: 0 24px;
  height: var(--header-height);
  position: relative;
  z-index: 10;
}

.header-left { display: flex; align-items: center; gap: 8px; min-width: 0; color: var(--color-text-secondary); font-size: 13px; }
.crumb { color: var(--color-text-secondary); }
.crumb.current { color: var(--color-text-primary); font-weight: 700; font-size: 15px; }
.header-sub { font-size: 12px; color: var(--color-text-secondary); margin-left: 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.header-right { display: flex; align-items: center; gap: 14px; flex-shrink: 0; }

.search-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--glass-border-strong);
  background: rgba(255, 255, 255, 0.6);
  border-radius: 10px;
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
}
.search-trigger:hover { border-color: #BFD9F0; color: var(--color-primary); background: rgba(255, 255, 255, 0.85); }
.search-text { min-width: 84px; text-align: left; }

.kbd {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-secondary);
  background: rgba(226, 232, 240, 0.7);
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 6px;
  padding: 1px 7px;
  letter-spacing: 0.5px;
}

.header-divider { width: 1px; height: 18px; background: var(--color-border); }

.header-time { display: flex; align-items: center; gap: 7px; font-size: 13px; color: var(--color-text-regular); font-variant-numeric: tabular-nums; }
.header-time .el-icon { color: var(--color-text-secondary); }

.header-tag {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1px;
  color: #fff;
  background-image: var(--grad-accent);
  padding: 4px 10px;
  border-radius: 8px;
  box-shadow: var(--shadow-glow);
}

.app-main { background: transparent; padding: 24px; }

/* ============ 快捷搜索弹窗 ============ */
:deep(.quick-search .el-dialog__body) { padding: 14px 16px 16px; }

.qs-input {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 0 12px;
  height: 44px;
  background: rgba(255, 255, 255, 0.7);
  color: var(--color-text-secondary);
}
.qs-input:focus-within { border-color: var(--color-primary); box-shadow: 0 0 0 4px rgba(14, 165, 233, 0.12); }
.qs-input input { flex: 1; border: none; outline: none; background: transparent; font-size: 14px; color: var(--color-text-primary); }

.qs-list { margin-top: 10px; max-height: 320px; overflow-y: auto; }

.qs-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 10px;
  cursor: pointer;
  color: var(--color-text-regular);
  font-size: 14px;
  transition: all 0.15s ease;
}
.qs-item.active, .qs-item:hover { background: var(--sidebar-hover-bg); color: var(--color-primary); }
.qs-icon { display: flex; }
.qs-title { flex: 1; font-weight: 500; }
.qs-group { font-size: 11px; color: var(--color-text-secondary); }
.qs-empty { padding: 20px; text-align: center; color: var(--color-text-secondary); font-size: 13px; }

.fade-enter-active, .fade-leave-active { transition: opacity 0.18s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
