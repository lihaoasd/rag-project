import apiClient from './request'

/**
 * 多知识库语义检索
 */
export function search({
  kb_ids,
  query,
  top_k,
  similarity_threshold,
  retrieval_methods,
  rerank_model_id,
  rerank_top_n,
  highlight = true,
  query_rewrite = null,
}) {
  return apiClient
    .post('/search', {
      kb_ids: kb_ids || [],
      query,
      top_k: top_k || 10,
      similarity_threshold: similarity_threshold || 0.6,
      retrieval_methods: retrieval_methods ?? null,
      rerank_model_id: rerank_model_id || null,
      rerank_top_n: rerank_top_n ?? 5,
      highlight,
      query_rewrite: query_rewrite ?? null,
    })
    .then((res) => res.data)
}

/**
 * 单知识库语义检索（兼容旧接口）
 */
export function searchSingle(kbId, {
  query,
  top_k,
  similarity_threshold,
  retrieval_methods,
  highlight = true,
  query_rewrite = null,
}) {
  return apiClient
    .post(`/knowledge-bases/${kbId}/search`, {
      query,
      top_k: top_k || 10,
      similarity_threshold: similarity_threshold || 0.6,
      retrieval_methods: retrieval_methods ?? null,
      highlight,
      query_rewrite: query_rewrite ?? null,
    })
    .then((res) => res.data)
}
