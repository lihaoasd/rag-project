import apiClient from './request'

/**
 * 模型配置与知识库模型绑定 API。
 */

/**
 * 获取模型配置列表
 * @param {object} [params] - 筛选条件 { model_type, platform }
 */
export function listModels(params) {
  return apiClient.get('/models', { params }).then((res) => res.data)
}

/**
 * 获取单个模型配置详情
 */
export function getModel(id) {
  return apiClient.get(`/models/${id}`).then((res) => res.data)
}

/**
 * 创建模型配置
 */
export function createModel(data) {
  return apiClient.post('/models', data).then((res) => res.data)
}

/**
 * 更新模型配置
 */
export function updateModel(id, data) {
  return apiClient.put(`/models/${id}`, data).then((res) => res.data)
}

/**
 * 删除模型配置
 */
export function deleteModel(id) {
  return apiClient.delete(`/models/${id}`)
}

/**
 * 设为该类型全局默认
 */
export function setDefaultModel(id) {
  return apiClient.post(`/models/${id}/set-default`).then((res) => res.data)
}

/**
 * 测试模型连接可用性（不落库）
 */
export function testModel(data) {
  return apiClient.post('/models/test', data).then((res) => res.data)
}

/**
 * 获取模型供应商列表（含各供应商下模型数）
 */
export function listProviders() {
  return apiClient.get('/models/providers').then((res) => res.data)
}

/**
 * 创建模型供应商
 */
export function createProvider(data) {
  return apiClient.post('/models/providers', data).then((res) => res.data)
}

/**
 * 更新模型供应商
 */
export function updateProvider(id, data) {
  return apiClient.put(`/models/providers/${id}`, data).then((res) => res.data)
}

/**
 * 删除模型供应商
 */
export function deleteProvider(id) {
  return apiClient.delete(`/models/providers/${id}`)
}

/**
 * 从供应商 OpenAI 兼容 /models 端点拉取可用模型列表
 */
export function fetchProviderModels(providerId) {
  return apiClient.post(`/models/providers/${providerId}/fetch-models`).then((res) => res.data)
}

/**
 * 获取知识库各类型模型的绑定与最终生效配置
 */
export function getKbModels(kbId) {
  return apiClient.get(`/knowledge-bases/${kbId}/models`).then((res) => res.data)
}

/**
 * 绑定/解绑知识库模型（modelConfigId 为 null 时解绑回退默认）
 */
export function bindKbModel(kbId, modelType, modelConfigId) {
  return apiClient
    .put(`/knowledge-bases/${kbId}/models/${modelType}`, { model_config_id: modelConfigId })
    .then((res) => res.data)
}
