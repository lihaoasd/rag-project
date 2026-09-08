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
  },
  {
    path: '/knowledge-bases/:kbId/documents',
    name: 'Documents',
    component: () => import('@/views/DocumentListView.vue'),
  },
  {
    path: '/knowledge-bases/:kbId/documents/:docId/chunks',
    name: 'Chunks',
    component: () => import('@/views/ChunkListView.vue'),
  },
  {
    path: '/knowledge-bases/:kbId/graph',
    name: 'Graph',
    component: () => import('@/views/GraphView.vue'),
  },
  {
    path: '/search',
    name: 'Search',
    component: () => import('@/views/SearchView.vue'),
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('@/views/ChatView.vue'),
  },
  {
    path: '/prompts',
    name: 'Prompts',
    component: () => import('@/views/PromptManagementView.vue'),
  },
  {
    path: '/models',
    name: 'Models',
    component: () => import('@/views/ModelConfigView.vue'),
  },
  {
    path: '/lexicon',
    name: 'Lexicon',
    component: () => import('@/views/LexiconView.vue'),
  },
  {
    path: '/domains',
    name: 'Domains',
    component: () => import('@/views/DomainConfigView.vue'),
  },
  {
    path: '/es-admin',
    name: 'EsAdmin',
    component: () => import('@/views/EsAdminView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
