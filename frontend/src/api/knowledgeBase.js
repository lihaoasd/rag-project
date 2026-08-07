import apiClient from './request'

/**
 * 创建知识库
 */
export function createKnowledgeBase(data) {
  return apiClient.post('/knowledge-bases', data).then((res) => res.data)
}

/**
 * 获取知识库列表
 */
export function listKnowledgeBases() {
  return apiClient.get('/knowledge-bases').then((res) => res.data)
}

/**
 * 获取知识库详情
 */
export function getKnowledgeBase(id) {
  return apiClient.get(`/knowledge-bases/${id}`).then((res) => res.data)
}

/**
 * 删除知识库
 */
export function deleteKnowledgeBase(id) {
  return apiClient.delete(`/knowledge-bases/${id}`)
}
