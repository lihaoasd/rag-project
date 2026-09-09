import apiClient from './request'

/**
 * 词库管理 API — 词条 CRUD、词条×领域映射、导入导出、统计
 */

/** 分页查询词条 */
export function listTerms({ page = 1, page_size = 20, search = '' } = {}) {
  return apiClient
    .get('/lexicon/terms', { params: { page, page_size, search } })
    .then((res) => res.data)
}

/** 新增词条 */
export function createTerm(data) {
  return apiClient.post('/lexicon/terms', data).then((res) => res.data)
}

/** 更新词条 */
export function updateTerm(id, data) {
  return apiClient.put(`/lexicon/terms/${id}`, data).then((res) => res.data)
}

/** 删除词条 */
export function deleteTerm(id) {
  return apiClient.delete(`/lexicon/terms/${id}`)
}

/** 词条领域映射（词条 × 领域模板 → 实体类型） */
export function getTermDomainMappings(id) {
  return apiClient.get(`/lexicon/terms/${id}/domain-mappings`).then((res) => res.data)
}

/** 整包替换词条领域映射 */
export function updateTermDomainMappings(id, mappings) {
  return apiClient
    .put(`/lexicon/terms/${id}/domain-mappings`, { mappings })
    .then((res) => res.data)
}

/** CSV 批量导入（文本） */
export function importTerms(content) {
  return apiClient.post('/lexicon/import', { content }).then((res) => res.data)
}

/** 导出全部词条 CSV */
export function exportCsv() {
  return apiClient.get('/lexicon/export/csv', { responseType: 'text' }).then((res) => res.data)
}

/** 导出 IK 词典（一行一词，UTF-8 无 BOM，供人工放置到 ES 词典目录） */
export function exportIkDict() {
  return apiClient.get('/lexicon/export/ik-dict', { responseType: 'text' }).then((res) => res.data)
}

/** 词库统计 */
export function getLexiconStats() {
  return apiClient.get('/lexicon/stats').then((res) => res.data)
}
