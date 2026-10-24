import axios from 'axios'

export const API = axios.create({
  baseURL: '/api', // Vite proxy will forward to FastAPI
  timeout: 30000,
})

export const uploadExcel = async (file: File) => {
  const form = new FormData()
  form.append('file', file)
  const response = await API.post('/analyze', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return response.data // { analysis_id, status }
}

export const getAnalysis = async (analysisId: string) => {
  const response = await API.get(`/analysis/${analysisId}`)
  return response.data
}

export const listenAnalysisEvents = (analysisId: string, onStatus: (status: string) => void) => {
  const eventSource = new EventSource(`${API.defaults.baseURL}/analysis/${analysisId}/events`)

  const handle = (e: MessageEvent) => {
    const raw = String(e.data)
    // sse-starlette can send plain text or JSON-encoded payloads
    try {
      const parsed = JSON.parse(raw)
      onStatus(typeof parsed === 'string' ? parsed : raw)
    } catch {
      onStatus(raw)
    }
  }

  // sse-starlette emits a *named* event ("status").
  // Named events never reach onmessage – they require addEventListener.
  eventSource.addEventListener('status', handle)
  // Fallback for default "message" events
  eventSource.onmessage = handle

  eventSource.onerror = () => {
    eventSource.close()
  }

  return () => {
    eventSource.removeEventListener('status', handle)
    eventSource.close()
  }
}
