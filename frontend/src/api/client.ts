import axios from 'axios'
import type { ReportNarrative } from '../store'

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

export interface RowsResponse {
  total: number
  total_rows: number
  page: number
  page_size: number
  columns: string[]
  rows: Array<Record<string, unknown> & {
    risk_level: string
    risk_reason: string
    suspicious: boolean
  }>
}

export const getRows = async (
  analysisId: string,
  opts: { page?: number; pageSize?: number; q?: string; level?: string; suspicious?: boolean } = {},
) => {
  const response = await API.get(`/analysis/${analysisId}/rows`, {
    params: {
      page: opts.page ?? 1,
      page_size: opts.pageSize ?? 25,
      q: opts.q ?? '',
      level: opts.level ?? '',
      suspicious: opts.suspicious ? 'true' : 'false',
    },
  })
  return response.data as RowsResponse
}

/**
 * Pide al backend que redacte el informe técnico con la IA.
 * El backend envía al modelo SOLO datos calculados de los registros
 * (grounding estricto) y siempre devuelve un informe completo, usando el
 * generador local como fallback si no hay modelo remoto configurado.
 */
export const generateReport = async (analysisId: string) => {
  const response = await API.post(`/analysis/${analysisId}/report`, null, {
    timeout: 90000, // permite esperar a un modelo remoto
  })
  return response.data as ReportNarrative
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
