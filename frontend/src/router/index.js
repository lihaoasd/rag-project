import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/knowledge-bases',
  },
  {
    path: '/knowledge-bases',
    name: 'KnowledgeBases',
    component: () => import('@/views/KnowledgeBaseView.vue'),
    meta: { title: '知识库管理', subtitle: '管理领域知识库与文档集合' },
  },
  {
    path: '/knowledge-bases/:kbId/documents',
    name: 'Documents',
    component: () => import('@/views/DocumentListView.vue'),
    meta: { title: '文档列表', subtitle: '上传、解析与维护知识库文档' },
  },
  {
    path: '/knowledge-bases/:kbId/documents/:docId/chunks',
    name: 'Chunks',
    component: () => import('@/views/ChunkListView.vue'),
    meta: { title: '切片管理', subtitle: '查看与检索文档解析后的切片' },
  },
  {
    path: '/knowledge-bases/:kbId/graph',
    name: 'Graph',
    component: () => import('@/views/GraphView.vue'),
    meta: { title: '知识图谱', subtitle: '可视化领域实体与关系网络' },
  },
  {
    path: '/search',
    name: 'Search',
    component: () => import('@/views/SearchView.vue'),
    meta: { title: '文档检索', subtitle: '混合检索与语义召回实验' },
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { title: '智能问答', subtitle: '基于知识库的检索增强对话' },
  },
  {
    path: '/prompts',
    name: 'Prompts',
    component: () => import('@/views/PromptManagementView.vue'),
    meta: { title: '提示词管理', subtitle: '配置与版本化问答提示词' },
  },
  {
    path: '/models',
    name: 'Models',
    component: () => import('@/views/ModelConfigView.vue'),
    meta: { title: '模型配置', subtitle: '接入与管理大模型服务' },
  },
  {
    path: '/lexicon',
    name: 'Lexicon',
    component: () => import('@/views/LexiconView.vue'),
    meta: { title: '词库管理', subtitle: '维护分词与领域术语表' },
  },
  {
    path: '/domains',
    name: 'Domains',
    component: () => import('@/views/DomainConfigView.vue'),
    meta: { title: '领域模板', subtitle: '配置领域抽取与实体定义' },
  },
  {
    path: '/es-admin',
    name: 'EsAdmin',
    component: () => import('@/views/EsAdminView.vue'),
    meta: { title: 'ES 对账', subtitle: '索引一致性对账与修复' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
