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
    /**
     * Ventana temporal y facetas del motor de filtros. Se serializan con el
     * mismo `buildFilterParams` de /summary, de modo que las facetas viajan
     * como claves repetidas (`Hostname=DC-01&Hostname=WS-018`). Opcional:
     * los llamadores que no filtran no cambian de comportamiento.
     */
    filters?: {
      from?: string | null
      to?: string | null
      facets?: Record<string, string[]>
    }
    /** Cancela la petición anterior cuando los filtros cambian a toda velocidad. */
    signal?: AbortSignal
  } = {},
) => {
  // Se parte de los filtros del motor (from/to/facetas) y se completan con
  // `.set()` los cuatro filtros clásicos. Sin filtros activos la query queda
  // byte a byte como siempre: page=1&page_size=25&q=&level=&suspicious=false&result=
  const params = buildFilterParams(opts.filters ?? {})
  params.set('page', String(opts.page ?? 1))
  params.set('page_size', String(opts.pageSize ?? 25))
  params.set('q', opts.q ?? '')
  params.set('level', opts.level ?? '')
  params.set('suspicious', opts.suspicious ? 'true' : 'false')
  params.set('result', opts.result ?? '')
  const response = await API.get(`/analysis/${analysisId}/rows`, {
    params,
    signal: opts.signal,
  })
  return response.data as RowsResponse
}

/** Filtros del motor de consulta; viajan como query string en /summary y /facets. */
export type FilterParams = {
  from?: string | null
  to?: string | null
  level?: string
  suspicious?: boolean
  result?: string
  q?: string
  /** Facetas por campo: se repiten como claves ('Hostname=DC-01&Hostname=WS-018'). */
  facets?: Record<string, string[]>
}

/**
 * Resumen del análisis acotado a los filtros: el mismo `AnalysisSummary` de
 * siempre más `total` (registros del dataset) y `filtered_total` (los que
 * pasan el filtro, igual que `rows`).
 */
export type ScopedSummary = AnalysisSummary & { total?: number; filtered_total?: number }

export type FacetValue = { value: string; count: number }

export type FacetField = { name: string; label: string; distinct: number; values: FacetValue[] }

/**
 * Construye el query string de los filtros para /summary y /facets.
 *
 * CRÍTICO: las facetas deben serializarse repitiendo el nombre literal del
 * campo (`Hostname=DC-01&Hostname=WS-018`), que es como las lee el backend
 * (`request.query_params.getlist("Hostname")`). El `params` de axios con
 * arreglos serializa como `Hostname[]=DC-01&...` y el backend LO IGNORARÍA
 * en silencio (el filtro no aplicaría). Por eso se arma un URLSearchParams
 * con `append` por cada valor y se pasa tal cual como `params` de axios:
 * axios lo serializa con `toString()`, dejando las claves repetidas intactas.
 *
 * Además no se envían parámetros vacíos: `?from=` o `?level=` con valor
 * vacío producirían un 400 del backend por fecha/nivel ilegible.
 */
const buildFilterParams = (f: FilterParams): URLSearchParams => {
  const params = new URLSearchParams()
  if (f.from) params.append('from', f.from)
  if (f.to) params.append('to', f.to)
  if (f.level) params.append('level', f.level)
  if (f.suspicious) params.append('suspicious', 'true')
  if (f.result) params.append('result', f.result)
  if (f.q) params.append('q', f.q)
  if (f.facets) {
    for (const [field, values] of Object.entries(f.facets)) {
      for (const value of values) params.append(field, value)
    }
  }
  return params
}

/**
 * Resumen del análisis acotado a los filtros (GET /analysis/{id}/summary).
 * Sin filtros el hook correspondiente ni siquiera lo llama (cero peticiones).
 */
export const getScopedSummary = async (
  analysisId: string,
  f: FilterParams,
  signal?: AbortSignal,
): Promise<ScopedSummary> => {
  const response = await API.get<ScopedSummary>(`/analysis/${analysisId}/summary`, {
    params: buildFilterParams(f),
    signal,
  })
  return response.data
}

/** Fila del historial de subidas (GET /analyses, spec sub-3 §3.5). */
export type RecentAnalysis = {
  analysis_id: string
  file_name: string
  generated_at: string | null
  rows: number
  risk: string
}

/** Últimos análisis completados en el backend, lo más reciente primero. */
export const getRecentAnalyses = async (signal?: AbortSignal): Promise<RecentAnalysis[]> => {
  const response = await API.get<RecentAnalysis[]>('/analyses', { signal })
  return response.data
}

/**
 * Campos detectados en el archivo con sus valores y conteos acotados
 * (GET /analysis/{id}/facets). Ignora el filtro del propio campo al contar.
 */
export const getFacets = async (
  analysisId: string,
  f: FilterParams,
  signal?: AbortSignal,
): Promise<{ total: number; filtered_total: number; fields: FacetField[] }> => {
  const response = await API.get<{ total: number; filtered_total: number; fields: FacetField[] }>(
    `/analysis/${analysisId}/facets`,
    { params: buildFilterParams(f), signal },
  )
  return response.data
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
  /** false = completada desde la fuente oficial: usar description_en. */
  curated?: boolean
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
  /** 'deterministic' = motor local; 'llm' = modelo remoto (con grounding validado); 'streaming' = el texto aún se genera (evento meta). */
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

/** Conteo de la memoria de criterio aprendido (panel «Opciones IA»). */
export interface FeedbackStats {
  available: boolean
  total: number
  positive: number
  negative: number
  indexed: boolean
  path?: string
}

/**
 * Corrección del analista sobre una respuesta del asistente (few-shot).
 *
 * Enseña al modelo la respuesta BUANA para esa consulta; el backend la guarda
 * como criterio y la inyecta en consultas futuras afines. No reentrena nada:
 * es el RAG potenciando el criterio con feedback humano.
 */
export const sendFeedback = async (
  analysisId: string,
  question: string,
  correctedAnswer: string,
  badAnswer = '',
) => {
  const response = await API.post(`/analysis/${analysisId}/feedback`, {
    question,
    corrected_answer: correctedAnswer,
    bad_answer: badAnswer,
  })
  return response.data as { ok: boolean; stats: FeedbackStats }
}

/** Estado de la memoria de criterio para el panel de Opciones IA. */
export const getFeedbackStatus = async () => {
  const response = await API.get('/feedback')
  return response.data as FeedbackStats
}

/** Vacía la memoria de criterio aprendido (ejemplos few-shot). */
export const clearFeedback = async () => {
  const response = await API.delete('/feedback')
  return response.data as { ok: boolean; removed: number; stats: FeedbackStats }
}

/**
 * Evento del flujo SSE de `askQuestionStream`:
 * `meta` → 0..n `token` → `final` | `fallback`.
 *
 * `meta` trae evidencias y referencia antes de que el modelo escriba (la UI
 * pinta las tarjetas mientras llegan los tokens); `final` cierra con la
 * respuesta validada y `fallback` la sustituye por la determinista avisando
 * del motivo (`warning`).
 */
export type AskStreamEvent =
  | { kind: 'meta'; data: Partial<AskResponse> & { engine: string } }
  | { kind: 'token'; text: string }
  | { kind: 'final'; data: AskResponse }
  | { kind: 'fallback'; data: AskResponse & { warning?: string } }

/**
 * Consulta en streaming sobre los eventos importados (SSE vía POST).
 *
 * EventSource solo admite GET, así que se usa fetch + ReadableStream y se
 * parsean los eventos con nombre (`meta`, `token`, `final`, `fallback`) que
 * emite el backend. La promesa se resuelve al cerrarse el flujo; si el
 * transporte falla se propaga el error con el mismo formato que
 * `askQuestion`, para que el caller pueda reintentar sin streaming.
 */
export const askQuestionStream = async (
  analysisId: string,
  question: string,
  opts: { level?: string; suspicious?: boolean; nResults?: number } = {},
  onEvent: (event: AskStreamEvent) => void,
): Promise<void> => {
  const response = await fetch(`${API.defaults.baseURL}/analysis/${analysisId}/ask/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      level: opts.level ?? '',
      suspicious: opts.suspicious ?? false,
      n_results: opts.nResults ?? 8,
    }),
  })

  if (!response.ok || !response.body) {
    let detail = ''
    try {
      const data = (await response.json()) as { detail?: string }
      detail = data?.detail ?? ''
    } catch {
      // Cuerpo no JSON (p. ej. 502 de un proxy): se conserva solo el código.
    }
    throw Object.assign(new Error(detail || `HTTP ${response.status}`), {
      response: { status: response.status, data: { detail } },
    })
  }

  const emit = (block: string) => {
    const name = /^event:\s*(.+)$/m.exec(block)?.[1]?.trim()
    const dataLine = /^data:\s*([\s\S]*)$/m.exec(block)?.[1]
    if (!name || dataLine === undefined) return
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(dataLine) as Record<string, unknown>
    } catch {
      return
    }
    if (name === 'token') onEvent({ kind: 'token', text: String(payload.text ?? '') })
    else if (name === 'meta')
      onEvent({ kind: 'meta', data: payload as Partial<AskResponse> & { engine: string } })
    else if (name === 'final') onEvent({ kind: 'final', data: payload as unknown as AskResponse })
    else if (name === 'fallback')
      onEvent({
        kind: 'fallback',
        data: payload as unknown as AskResponse & { warning?: string },
      })
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    // sse-starlette separa los eventos con CRLF: se normaliza a LF para parsear.
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    let sep = buffer.indexOf('\n\n')
    while (sep !== -1) {
      emit(buffer.slice(0, sep))
      buffer = buffer.slice(sep + 2)
      sep = buffer.indexOf('\n\n')
    }
  }
  if (buffer.trim()) emit(buffer)
}

/** Olvida la memoria de conversación (turnos) que el backend guardó en RAM. */
export const clearChatHistory = async (analysisId: string) => {
  const response = await API.delete(`/analysis/${analysisId}/chat`)
  return response.data as { ok: boolean }
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

/** Recuentos de un Event_ID observado en el dataset (panel de correlaciones). */
export interface ObservedEventStats {
  event_id: string
  count: number
  suspicious: number
  levels: Record<string, number>
  hosts?: string[]
  first_seen?: string
  last_seen?: string
}

/** Recuentos comunes que el backend añade a cada entrada del glosario. */
interface EventStatsFields {
  count: number
  suspicious: number
  levels: Record<string, number>
  hosts?: string[]
  first_seen?: string
  last_seen?: string
}

/** Entrada del glosario con los recuentos reales de los registros. */
export interface CorrelationEventEntry extends EventStatsFields {
  kind: string
  /** Event ID (clave en el glosario). */
  id: string
  titulo: string
  descripcion: string
  por_que_importa: string
  logon_types?: string
  relacionados: string[]
  fuente: string
}

/** Técnica ATT&CK con su evidencia observada en este análisis. */
export interface CorrelationTechnique {
  id: string
  name: string
  tactic: string
  tactic_es: string
  url: string
  descripcion_es: string
  se_detecta: string
  eventos: string[]
  aliases: string[]
  /** false = completada desde la fuente oficial (usar description_en). */
  curated: boolean
  description_en: string
  matched_events: { event_id: string; count: number; suspicious: number }[]
  matched_rows: number
  matched_suspicious: number
}

/** Táctico con sus técnicas observadas (agregación para la cabecera). */
export interface CorrelationTactic {
  id: string
  name_es: string
  techniques: number
  rows: number
  suspicious: number
  ids: string[]
}

/** GET /api/analysis/{id}/attack/correlations — JOIN determinista con la KB. */
export interface AttackCorrelations {
  analysis_id: string
  rows: number
  observed_events: number
  observed_rows: number
  available: boolean
  note: string
  event_ids: CorrelationEventEntry[]
  unmapped: ObservedEventStats[]
  techniques: CorrelationTechnique[]
  tactics: CorrelationTactic[]
}

/**
 * Correlaciones ATT&CK de los registros de un análisis.
 *
 * Es una consulta al marco (glosario + técnicas), no inferencia: cada técnica
 * llega porque un Event_ID presente en el dataset la referencia. Los Event_ID
 * sin entrada en el glosario vuelven en `unmapped` en lugar de inventarles
 * una correlación.
 */
export const getAttackCorrelations = async (
  analysisId: string,
  signal?: AbortSignal,
): Promise<AttackCorrelations> => {
  const response = await API.get<AttackCorrelations>(
    `/analysis/${analysisId}/attack/correlations`,
    { signal },
  )
  return response.data
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

/* ===========================================================================
   Panel «Opciones IA» — configuración del LLM (proveedor cloud por defecto:
   endpoint compatible con OpenAI, p. ej. Groq)
   =========================================================================== */

/** Configuración persistida en el backend (JSON en disco, aplica al instante). */
export interface AiConfig {
  /** LLM apagado ⇒ el asistente y el informe usan el motor determinista. */
  enabled: boolean
  /** Endpoint compatible con OpenAI (`…/v1/chat/completions`) u Ollama nativo. */
  api_url: string
  api_key: string
  model: string
  /** Presupuesto del prompt: historial + evidencias + ejemplos few-shot. */
  num_ctx: number
  /** Creatividad: en SOC, evidencia y no ficción ⇒ 0–0.2 lo habitual. */
  temperature: number
  /** Solo dialecto Ollama: cuánto sigue el modelo cargado en VRAM. Ignorado en endpoints OpenAI-compatibles. */
  keep_alive: string
  /** Solo dialecto Ollama: tope de tokens generados (−1 = sin límite). Ignorado en endpoints OpenAI-compatibles. */
  num_predict: number
  /** Segundos de espera máxima por llamada al LLM. */
  timeout: number
}

/**
 * Modelo listado por el motor: Ollama (`/api/tags` / `/api/ps`) incluye
 * `size`/`details`; un proveedor compatible con OpenAI normaliza a `{name}`
 * (solo `id` del catálogo `GET {base}/models`).
 */
export interface OllamaModel {
  name: string
  size?: number
  details?: {
    parameter_size?: string
    quantization_level?: string
    family?: string
    [key: string]: unknown
  }
  [key: string]: unknown
}

/** Sonda de estado del motor de IA. */
export interface AiStatus {
  kind: 'ollama' | 'remote' | 'none'
  /** true/false si el motor respondió a la sonda; null si la URL no es inspeccionable. */
  reachable: boolean | null
  reason?: string | null
  base?: string
  latency_ms?: number
  models: OllamaModel[]
  /** Modelos residentes en VRAM (solo Ollama; vacío en proveedor cloud). */
  loaded: OllamaModel[]
}

export interface AiConfigResponse {
  config: AiConfig
  status: AiStatus
  /** ¿El modelo configurado está instalado? null si no se pudo inspeccionar. */
  model_installed: boolean | null
}

/** Resultado del botón «Probar conexión» (ping real al modelo). */
export interface AiTestResponse {
  ok: boolean
  error?: string | null
  latency_ms?: number
  reply?: string
  model?: string
  status?: AiStatus | null
}

/** Configuración vigente + estado real del motor (modelos, latencia). */
export const getAiConfig = async (): Promise<AiConfigResponse> => {
  const response = await API.get<AiConfigResponse>('/ai/config')
  return response.data
}

/** Guarda la configuración (parcial o completa). 400 con `detail` si es inválida. */
export const updateAiConfig = async (patch: Partial<AiConfig>): Promise<AiConfigResponse> => {
  const response = await API.put<AiConfigResponse>('/ai/config', patch)
  return response.data
}

/** Prueba real: sonda de estado + ping al modelo midiendo latencia. */
export const testAiConnection = async (patch: Partial<AiConfig> = {}): Promise<AiTestResponse> => {
  const response = await API.post<AiTestResponse>('/ai/test', patch)
  return response.data
}
