import apiClient from './request'

/**
 * 图谱概要统计
 */
export function getGraphStats(kbId) {
  return apiClient.get(`/knowledge-bases/${kbId}/graph/stats`).then((res) => res.data)
}

/**
 * 图谱本体 meta（v2 动态）：生效领域模板的类型/颜色/关系清单
 */
export function getGraphMeta(kbId) {
  return apiClient.get(`/knowledge-bases/${kbId}/graph/meta`).then((res) => res.data)
}

/**
 * 图谱数据（ECharts 渲染，按 degree 降序分页；excludeNames=已加载节点名，增量加载）
 */
export function getGraphData(kbId, { limit = 150, offset = 0, excludeNames = [] } = {}) {
  return apiClient
    .get(`/knowledge-bases/${kbId}/graph`, {
      params: { limit, offset, exclude_names: excludeNames },
    })
    .then((res) => res.data)
}

/**
 * 全量构建知识库图谱（异步）
 */
export function buildKbGraph(kbId) {
  return apiClient.post(`/knowledge-bases/${kbId}/graph/build`).then((res) => res.data)
}

/**
 * 清空整个知识库图谱
 */
export function clearKbGraph(kbId) {
  return apiClient.delete(`/knowledge-bases/${kbId}/graph`)
}

/**
 * 单文件增量构建图谱（异步）
 */
export function buildDocGraph(kbId, docId) {
  return apiClient.post(`/knowledge-bases/${kbId}/documents/${docId}/graph/build`).then((res) => res.data)
}

/**
 * 清除单文件图谱
 */
export function clearDocGraph(kbId, docId) {
  return apiClient.delete(`/knowledge-bases/${kbId}/documents/${docId}/graph`)
}

/**
 * 单文件图谱状态
 */
export function getDocGraphStatus(kbId, docId) {
  return apiClient.get(`/knowledge-bases/${kbId}/documents/${docId}/graph/status`).then((res) => res.data)
}

/**
 * 子图检索（返回 nodes/edges/evidence）
 */
export function searchGraph(kbId, query, maxHops = 2, limit = 200) {
  return apiClient
    .post(`/knowledge-bases/${kbId}/graph/search`, { query, max_hops: maxHops, limit })
    .then((res) => res.data)
}

/**
 * 分级展开：返回指定实体 1-hop 邻居子图（逐层浏览）
 */
export function expandGraphNode(kbId, name, limit = 100) {
  return apiClient
    .post(`/knowledge-bases/${kbId}/graph/expand`, { name, limit })
    .then((res) => res.data)
}

/**
 * 实体详情（属性 + 邻居 + chunk 证据）
 */
export function getEntityDetail(kbId, name) {
  return apiClient.get(`/knowledge-bases/${kbId}/graph/entities`, { params: { name } }).then((res) => res.data)
}

/**
 * 实体分页列表（实体管理）：名称模糊搜索 + 关系数/chunk 数
 */
export function listGraphEntities(kbId, { page = 1, pageSize = 50, q = '' } = {}) {
  return apiClient
    .get(`/knowledge-bases/${kbId}/graph/entities/list`, {
      params: { page, page_size: pageSize, q },
    })
    .then((res) => res.data)
}

/**
 * 编辑实体（名称/类型/描述；键冲突返回 409）
 */
export function updateGraphEntity(kbId, payload) {
  return apiClient.put(`/knowledge-bases/${kbId}/graph/entities`, payload).then((res) => res.data)
}
