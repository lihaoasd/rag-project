/**
 * 流式问答 — 使用 fetch + ReadableStream 解析 SSE
 *
 * @param {string} kbId - 知识库 ID
 * @param {string} message - 用户问题
 * @param {string|null} sessionId - 会话 ID
 * @param {object} callbacks - 回调函数 { onToken, onSources, onDone, onError }
 * @returns {AbortController} 用于取消请求
 */
export function chatStream(kbId, message, sessionId, callbacks) {
  const { onToken, onSources, onDone, onError } = callbacks
  const controller = new AbortController()

  fetch(`/api/knowledge-bases/${kbId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const err = await response.json().catch(() => ({}))
        onError(err.detail || '请求失败')
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              switch (data.type) {
                case 'token':
                  onToken(data.content)
                  break
                case 'sources':
                  onSources(data.sources)
                  break
                case 'error':
                  onError(data.message)
                  break
                case 'done':
                  onDone()
                  break
              }
            } catch {
              // 跳过解析失败的行
            }
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError(err.message || '网络错误')
      }
    })

  return controller
}
