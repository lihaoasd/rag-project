import apiClient from './request'

/**
 * 上传文档到知识库
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
 * 删除文档
 */
export function deleteDocument(kbId, docId) {
  return apiClient.delete(`/knowledge-bases/${kbId}/documents/${docId}`)
}
