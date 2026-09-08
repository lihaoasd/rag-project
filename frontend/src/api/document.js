import apiClient from './request'

/**
 * 上传文档到知识库（仅上传，不解析）
 */
export function uploadDocument(kbId, file) {
  const formData = new FormData()
  formData.append('file', file)
  return apiClient
    .post(`/knowledge-bases/${kbId}/documents`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((res) => res.data)
}

/**
 * 获取文档列表
 */
export function listDocuments(kbId) {
  return apiClient
    .get(`/knowledge-bases/${kbId}/documents`)
    .then((res) => res.data)
}

/**
 * 获取单个文档
 */
export function getDocument(kbId, docId) {
  return apiClient
    .get(`/knowledge-bases/${kbId}/documents/${docId}`)
    .then((res) => res.data)
}

/**
 * 触发异步文档解析
 */
export function parseDocument(kbId, docId) {
  return apiClient
    .post(`/knowledge-bases/${kbId}/documents/${docId}/parse`)
    .then((res) => res.data)
}

/**
 * 触发重新解析（仅 completed / failed 状态可用）
 */
export function reparseDocument(kbId, docId) {
  return apiClient
    .post(`/knowledge-bases/${kbId}/documents/${docId}/reparse`)
    .then((res) => res.data)
}

/**
 * 继续解析（仅 partial 部分成功状态可用）：从最后已提交处续跑剩余切片
 */
export function resumeDocument(kbId, docId) {
  return apiClient
    .post(`/knowledge-bases/${kbId}/documents/${docId}/resume`)
    .then((res) => res.data)
}

/**
 * 删除文档
 */
export function deleteDocument(kbId, docId) {
  return apiClient.post(`/knowledge-bases/${kbId}/documents/${docId}/delete`)
}
