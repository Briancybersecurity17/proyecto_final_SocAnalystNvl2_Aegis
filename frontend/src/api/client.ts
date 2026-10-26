import axios from 'axios'
import type { AnalysisSummary, ReportNarrative } from '../store'

export const API = axios.create({
  baseURL: '/api', // Vite proxy will forward to FastAPI
  timeout: 30000,
})

export const uploadExcel = async (file: File) => {
  const form = new FormData()
  form.append('file', file)
  const response = await API.post('/analyze', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    // El backend recibe y abre el libro antes de responder: con Excel grandes
    // los 30 s por defecto se quedaban cortos y la subida fallaba sin motivo.
    timeout: 120000,
  })
  return response.data // { analysis_id, status }
}

/** Envelope devuelto por GET /analysis/{analysis_id}. */
export interface AnalysisResponse {
  analysis_id: string
  status: 'pending' | 'processing' | 'done' | 'error'
  summary: AnalysisSummary
  error: string | null
  rag?: {
    available: boolean
    indexed?: number
    total?: number
    reason?: string | null
    model?: string
  }
}

export const getAnalysis = async (analysisId: string): Promise<AnalysisResponse> => {
  const response = await API.get<AnalysisResponse>(`/analysis/${analysisId}`)
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
  opts: {
    page?: number
    pageSize?: number
    q?: string
    level?: string
    suspicious?: boolean
    result?: string
    /** Cancela la petición anterior cuando los filtros cambian a toda velocidad. */
    signal?: AbortSignal
  } = {},
) => {
  const response = await API.get(`/analysis/${analysisId}/rows`, {
    params: {
      page: opts.page ?? 1,
      page_size: opts.pageSize ?? 25,
      q: opts.q ?? '',
      level: opts.level ?? '',
      suspicious: opts.suspicious ? 'true' : 'false',
      result: opts.result ?? '',
    },
    signal: opts.signal,
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
  /** Descripción del evento (Event_Description) tal como está en el archivo. */
  description: string
  /** Resultado del evento (Success/Failure/Allowed o código HTTP). */
  result: string
  relevance: number
  document: string
}

/** Entrada del glosario de Event IDs (base de conocimiento). */
export interface KbEventEntry {
  id: string
  titulo: string
  descripcion: string
  por_que_importa: string
  relacionados: string[]
  fuente: string
  /** 'evidencia' = observado en los registros; 'consulta' = citado en la pregunta. */
  match: 'evidencia' | 'consulta'
}

/** Técnica MITRE ATT&CK (referencia del marco, nunca un hallazgo). */
export interface KbTechnique {
  id: string
  name: string
  tactic: string
  tactic_es: string
  url: string
  descripcion_es: string
  se_detecta: string
  eventos: string[]
  aliases: string[]
  description_en: string
  match: 'eventos' | 'consulta'
  score?: number
}

/** Bloque «Referencia técnica» devuelto junto a la respuesta del asistente. */
export interface KbReference {
  available: boolean
  note: string
  event_ids: KbEventEntry[]
  techniques: KbTechnique[]
  observed_event_ids: string[]
}

/** Estado de la base de conocimiento (GET /api/kb/status). */
export interface KbStatus {
  available: boolean
  reason?: string | null
  techniques: number
  event_ids: number
  source: string
  indexed: boolean
  documents?: number
}

export interface AskResponse {
  analysis_id: string
  question: string
  retrieved: number
  filters: { level: string | null; suspicious: boolean }
  /** Redacción de la respuesta (markdown sencillo, siempre en español). */
  answer: string
  evidence: AskEvidence[]
  /** Referencia del marco (MITRE ATT&CK + Event IDs), separada de la evidencia. */
  reference?: KbReference
  summary_line: string
  confidence: 'alta' | 'media' | 'baja' | 'ninguna'
  /** 'deterministic' = motor local; 'llm' = modelo remoto (con grounding validado). */
  engine: string
  model: string | null
  warnings: string[]
  /**
   * 'aggregate' = consulta de conteos/distribución resuelta con cifras
   * exactas del resumen del dataset (sin evidencia fila a fila);
   * 'evidence' = respuesta anclada a los eventos recuperados.
   */
  mode?: 'aggregate' | 'evidence'
  rag: { available: boolean; reason?: string | null; model: string; total: number }
  kb?: KbStatus
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
 *
 * Se cachea en módulo unos segundos: la cabecera y el asistente lo sondean
 * periódicamente y, sin esta caché, cada consulta llegaba al backend (el
 * log de uvicorn se llenaba de `GET /api/rag/status` repetidos).
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

const RAG_TTL_MS = 8000
let ragCache: { key: string; at: number; data: RagStatusResponse } | null = null

export const getRagStatus = async (analysisId?: string) => {
  const key = analysisId ?? ''
  const cached = ragCache
  if (cached && cached.key === key && Date.now() - cached.at < RAG_TTL_MS) return cached.data
  const response = await API.get('/rag/status', {
    params: analysisId ? { analysis_id: analysisId } : {},
  })
  ragCache = { key, at: Date.now(), data: response.data as RagStatusResponse }
  return response.data as RagStatusResponse
}

/**
 * Estado de la base de conocimiento (MITRE ATT&CK + glosario de Event IDs).
 * Devuelve siempre 200: `available: false` si faltan dependencias opcionales.
 */
export const getKbStatus = async () => {
  const response = await API.get('/kb/status')
  return response.data as KbStatus
}

/**
 * Sigue el progreso de un análisis.
 *
 * Transporte principal: SSE (`/events`). Si el stream muere (reinicio del
 * backend, corte de red, proxy), en lugar de dejar la UI clavada en
 * «Analizando…» se pasa a consultar `GET /analysis/{id}` cada 1,5 s hasta
 * que el análisis termine o falle. El consumidor recibe el mismo flujo de
 * estados en ambos casos y su `stop()` corta lo que esté activo.
 */
export const listenAnalysisEvents = (analysisId: string, onStatus: (status: string) => void) => {
  let closed = false
  let pollTimer: number | null = null
  const eventSource = new EventSource(`${API.defaults.baseURL}/analysis/${analysisId}/events`)

  const handle = (e: MessageEvent) => {
    const raw = String(e.data)
    // sse-starlette puede mandar texto plano ("done") o JSON ("done" / {"status":"done"})
    try {
      const parsed = JSON.parse(raw)
      if (typeof parsed === 'string') onStatus(parsed)
      else if (parsed && typeof parsed.status === 'string') onStatus(parsed.status)
      else onStatus(raw)
    } catch {
      onStatus(raw)
    }
  }

  /** Respaldo: una consulta directa al análisis (mismo estado que el SSE). */
  const poll = async () => {
    if (closed) return
    try {
      const data = await getAnalysis(analysisId)
      if (closed || !data?.status) return
      onStatus(data.status)
      // 'done' y 'error' cierran la escucha desde el consumidor; aquí no se
      // reprograma para no seguir consultando tras terminar.
      if (data.status === 'done' || data.status === 'error') return
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status === 404) {
        // El backend volvió sin ese análisis (se reinició): no tiene sentido
        // seguir preguntando; se libera la UI para que se vuelva a subir.
        onStatus('error')
        return
      }
      // red caída: se reintenta en el siguiente tick, hasta que vuelva y
      // responda (done, error o 404)
    }
    if (!closed) pollTimer = window.setTimeout(() => void poll(), 1500)
  }

  const startPolling = () => {
    if (closed || pollTimer !== null) return
    pollTimer = window.setTimeout(() => void poll(), 800)
  }

  // sse-starlette emits a *named* event ("status").
  // Named events never reach onmessage – they require addEventListener.
  eventSource.addEventListener('status', handle)
  // Fallback for default "message" events
  eventSource.onmessage = handle

  eventSource.onerror = () => {
    // CONNECTING = el navegador ya está reintentando solo; solo se asume
    // corte definitivo cuando el stream queda CLOSED.
    if (eventSource.readyState === EventSource.CLOSED) {
      eventSource.close()
      startPolling()
    }
  }

  return () => {
    closed = true
    if (pollTimer !== null) {
      window.clearTimeout(pollTimer)
      pollTimer = null
    }
    eventSource.removeEventListener('status', handle)
    eventSource.close()
  }
}
