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
  eventSource.onmessage = (e) => {
    const data = JSON.parse(e.data)
    // Server sends { event: "status", data: "processing" }
    if (e.type === 'status') {
      onStatus(data)
    }
  }
  eventSource.onerror = () => {
    eventSource.close()
  }
  return () => eventSource.close()
}
