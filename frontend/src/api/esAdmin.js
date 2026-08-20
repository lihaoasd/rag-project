import apiClient from './request'

/**
 * ES 全文检索对账管理 API
 */

/** 对账：PG vs ES 各知识库切片数差异（只读） */
export function getEsStatus() {
  return apiClient.get('/es/status').then((res) => res.data)
}

/** 从 PG 全量重建 ES 索引（kbId 为空重建全部） */
export function rebuildEs(kbId = null) {
  const params = kbId ? { kb_id: kbId } : {}
  return apiClient.post('/es/rebuild', null, { params }).then((res) => res.data)
}
