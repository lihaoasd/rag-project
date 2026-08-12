import apiClient from './request'

/**
 * 获取提示词模板列表
 * @param {string} [scenario] - 场景筛选
 */
export function listPrompts(scenario) {
  return apiClient.get('/prompts', { params: { scenario } }).then((res) => res.data)
}

/**
 * 获取单个模板详情
 */
export function getPrompt(id) {
  return apiClient.get(`/prompts/${id}`).then((res) => res.data)
}

/**
 * 创建提示词模板
 */
export function createPrompt(data) {
  return apiClient.post('/prompts', data).then((res) => res.data)
}

/**
 * 更新提示词模板
 */
export function updatePrompt(id, data) {
  return apiClient.put(`/prompts/${id}`, data).then((res) => res.data)
}

/**
 * 删除提示词模板
 */
export function deletePrompt(id) {
  return apiClient.delete(`/prompts/${id}`)
}

/**
 * 激活模板（同一场景其他模板自动取消激活）
 */
export function activatePrompt(id) {
  return apiClient.post(`/prompts/${id}/activate`).then((res) => res.data)
}

/**
 * 预览渲染结果
 */
export function previewPrompt(data) {
  return apiClient.post('/prompts/preview', data).then((res) => res.data)
}
