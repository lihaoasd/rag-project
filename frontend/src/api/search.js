import apiClient from './request'

/**
 * 语义检索文档片段
 */
export function search(kbId, { query, top_k, similarity_threshold, offset, limit }) {
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
