import apiClient from './request'

/**
 * 领域模板列表（内置预设在前）
 */
export function listDomains() {
  return apiClient.get('/domains').then((res) => res.data)
}

/**
 * 模板详情（渲染 meta + 引用 KB 数）
 */
export function getDomain(id) {
  return apiClient.get(`/domains/${id}`).then((res) => res.data)
}

/**
 * 新建自定义模板
 */
export function createDomain(data) {
  return apiClient.post('/domains', data).then((res) => res.data)
}

/**
 * 整包更新自定义模板
 */
export function updateDomain(id, data) {
  return apiClient.put(`/domains/${id}`, data).then((res) => res.data)
}

/**
 * 删除自定义模板（仍有 KB 引用 → 409）
 */
export function deleteDomain(id) {
  return apiClient.delete(`/domains/${id}`)
}

/**
 * 复制模板为可编辑副本
 */
export function copyDomain(id) {
  return apiClient.post(`/domains/${id}/copy`).then((res) => res.data)
}

/**
 * 配置实时校验 + 生成抽取提示词
 */
export function previewDomain(data) {
  return apiClient.post('/domains/preview', data).then((res) => res.data)
}

/**
 * 知识库当前绑定模板与生效 meta
 */
export function getKbDomain(kbId) {
  return apiClient.get(`/knowledge-bases/${kbId}/domain`).then((res) => res.data)
}

/**
 * 知识库换绑领域模板（图谱状态联动失效）
 */
export function bindKbDomain(kbId, domainProfileId) {
  return apiClient
    .put(`/knowledge-bases/${kbId}/domain`, { domain_profile_id: domainProfileId })
    .then((res) => res.data)
}
