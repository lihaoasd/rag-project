import apiClient from './request'

/**
 * 获取文档切片列表（分页）
 */
export function listChunks(kbId, docId, page = 1, pageSize = 20) {
  return apiClient
    .get(`/knowledge-bases/${kbId}/documents/${docId}/chunks`, {
      params: { page, page_size: pageSize },
    })
    .then((res) => res.data)
}

/**
 * 获取切片详情（完整文本）
 */
export function getChunk(kbId, docId, chunkId) {
  return apiClient
    .get(`/knowledge-bases/${kbId}/documents/${docId}/chunks/${chunkId}`)
    .then((res) => res.data)
}

/**
 * 删除单个切片
 */
export function deleteChunk(kbId, docId, chunkId) {
  return apiClient
    .post(`/knowledge-bases/${kbId}/documents/${docId}/chunks/${chunkId}/delete`)
}

/**
 * 编辑切片文本
 */
export function updateChunk(kbId, docId, chunkId, text) {
  return apiClient
    .put(`/knowledge-bases/${kbId}/documents/${docId}/chunks/${chunkId}`, { text })
    .then((res) => res.data)
}

/**
 * 重新切片文档
 */
export function rechunkDocument(kbId, docId, chunkSize, chunkOverlap) {
  return apiClient
    .post(`/knowledge-bases/${kbId}/documents/${docId}/rechunk`, {
      chunk_size: chunkSize,
      chunk_overlap: chunkOverlap,
    })
    .then((res) => res.data)
}
