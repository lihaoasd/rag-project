import apiClient from './request'

/**
 * ES 全文检索对账管理 API
 */

/** 对账：PG vs ES 各知识库切片数差异（只读；page/pageSize 控制知识库列表分页） */
export function getEsStatus(page = 1, pageSize = 20) {
  return apiClient.get('/es/status', { params: { page, page_size: pageSize } }).then((res) => res.data)
}

/** 从 PG 全量重建 ES 索引（kbId 为空重建全部） */
export function rebuildEs(kbId = null) {
  const params = kbId ? { kb_id: kbId } : {}
  return apiClient.post('/es/rebuild', null, { params }).then((res) => res.data)
}
