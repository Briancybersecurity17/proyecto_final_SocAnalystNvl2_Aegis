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

/**
 * Un evento recuperado por el RAG como evidencia citable.
 */
export interface AskEvidence {
  log_id: string
  timestamp: string
  host: string
  event_id: string
  user: string
  source_ip: string
  destination: string
  severity: string
  level: string
  suspicious: boolean
  description: string
  relevance: number
  document: string
}

export interface AskResponse {
  analysis_id: string
  question: string
  retrieved: number
  filters: { level: string | null; suspicious: boolean }
  /** Redacción de la respuesta (markdown sencillo, siempre en español). */
  answer: string
  evidence: AskEvidence[]
  summary_line: string
  confidence: 'alta' | 'media' | 'baja' | 'ninguna'
  /** 'deterministic' = motor local; 'llm' = modelo remoto (con grounding validado). */
  engine: string
  model: string | null
  warnings: string[]
  rag: { available: boolean; reason?: string | null; model: string; total: number }
}

/**
 * Consulta en lenguaje natural sobre los eventos importados.
 *
 * El backend hace recuperación híbrida (semántica + léxica) y devuelve una
 * respuesta anclada a los eventos recuperados: sin hallazgos inventados.
 */
export const askQuestion = async (
  analysisId: string,
  question: string,
  opts: { level?: string; suspicious?: boolean; nResults?: number } = {},
) => {
  const response = await API.post(`/analysis/${analysisId}/ask`, {
    question,
    level: opts.level ?? '',
    suspicious: opts.suspicious ?? false,
    n_results: opts.nResults ?? 8,
  })
  return response.data as AskResponse
}

/**
 * Estado del índice semántico local (motor + eventos indexados).
 * Devuelve siempre 200: `available: false` si el motor no está instalado.
 */
export interface RagStatusResponse {
  available: boolean
  reason?: string | null
  model: string
  total: number
  events?: number
  levels?: Record<string, number>
  suspicious?: number
}

export const getRagStatus = async (analysisId?: string) => {
  const response = await API.get('/rag/status', {
    params: analysisId ? { analysis_id: analysisId } : {},
  })
  return response.data as RagStatusResponse
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
