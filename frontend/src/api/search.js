import apiClient from './request'

/**
 * 多知识库语义检索
 */
export function search({
  kb_ids,
  query,
  top_k,
  similarity_threshold,
  retrieval_method,
  hybrid_ranker,
  hybrid_weights,
  rerank_model_id,
  rerank_top_n,
  rerank_vector_threshold,
  highlight = true,
}) {
  return apiClient
    .post('/search', {
      kb_ids: kb_ids || [],
      query,
      top_k: top_k || 10,
      similarity_threshold: similarity_threshold || 0.6,
      retrieval_method: retrieval_method ?? null,
      hybrid_ranker: hybrid_ranker ?? null,
      hybrid_weights: hybrid_weights ?? null,
      rerank_model_id: rerank_model_id || null,
      rerank_top_n: rerank_top_n ?? 5,
      rerank_vector_threshold: rerank_vector_threshold ?? null,
      highlight,
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
  retrieval_method,
  hybrid_ranker,
  hybrid_weights,
  highlight = true,
}) {
  return apiClient
    .post(`/knowledge-bases/${kbId}/search`, {
      query,
      top_k: top_k || 10,
      similarity_threshold: similarity_threshold || 0.6,
      retrieval_method: retrieval_method ?? null,
      hybrid_ranker: hybrid_ranker ?? null,
      hybrid_weights: hybrid_weights ?? null,
      highlight,
    })
    .then((res) => res.data)
}
