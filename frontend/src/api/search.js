import apiClient from './request'

/**
 * 多知识库语义检索
 */
export function search({ kb_ids, query, top_k, similarity_threshold, offset, limit }) {
  return apiClient
    .post('/search', {
      kb_ids: kb_ids || [],
      query,
      top_k: top_k || 10,
      similarity_threshold: similarity_threshold || 0.6,
      offset: offset || 0,
      limit: limit || 20,
    })
    .then((res) => res.data)
}

/**
 * 单知识库语义检索（兼容旧接口）
 */
export function searchSingle(kbId, { query, top_k, similarity_threshold, offset, limit }) {
  return apiClient
    .post(`/knowledge-bases/${kbId}/search`, {
      query,
      top_k: top_k || 10,
      similarity_threshold: similarity_threshold || 0.6,
      offset: offset || 0,
      limit: limit || 20,
    })
    .then((res) => res.data)
}
