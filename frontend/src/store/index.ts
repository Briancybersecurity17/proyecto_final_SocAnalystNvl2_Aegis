import { create } from 'zustand'
import { devtools } from 'zustand/middleware'

export interface SuspiciousDetail {
  Hostname?: string
  Event_ID?: string | number
  Event_Description?: string
  Timestamp?: string
  Source_IP?: string
  User?: string
  Severity?: string
  _reason?: string
  [key: string]: unknown
}

export interface RiskLevelBlock {
  level: string
  label: string
  count: number
  percentage: number
  justification: string
  examples: SuspiciousDetail[]
}

export interface SuspiciousIp {
  ip: string
  events: number
  hosts: number
}

export interface ReportEvidence {
  log_id: string
  timestamp: string
  host: string
  event_id: string
  user: string
  source_ip: string
  description: string
  severity: string
  /** Resultado del evento (Success/Failure/Allowed o código HTTP), si existe. */
  result?: string
  reason: string
}

export interface ReportFinding {
  id: string
  title: string
  level: string
  explanation: string
  evidence: ReportEvidence[]
}

export interface ActionItem {
  priority: string
  action: string
  rationale: string
}

export interface ReportNarrative {
  executive_summary: string
  methodology: string
  correlation: string
  conclusions: string
  findings: ReportFinding[]
  action_plan: ActionItem[]
  source: 'local' | 'llm'
  /**
   * De dónde salió CADA campo: 'llm' solo si el validador aceptó esa prosa,
   * 'local' en el resto. `source: 'llm'` significa «al menos un campo» — sin
   * este mapa la UI no puede distinguir un informe 100 % IA de uno donde
   * solo pasó un campo (p. ej. correlation) y el resto es el fallback local.
   */
  field_sources?: Record<string, 'llm' | 'local'>
  model: string
  generated_at: string
}

/** Un valor original del campo Severity y el nivel de riesgo al que se traduce. */
export interface SeverityMappingEntry {
  raw: string
  level: string
  count: number
  percentage: number
}

export interface AnalysisSummary {
  rows: number
  columns: string[]
  risk: string
  risk_justification: string
  severity_counts: Record<string, number>
  /** Éxito/fallo o código HTTP por registro (campo Result del archivo). */
  result_counts?: Record<string, number>
  /** Traducción auditable de cada valor original de Severity a nuestro nivel. */
  severity_mapping?: SeverityMappingEntry[]
  level_counts: Record<string, number>
  /** Event ID → nº de registros (los que trae EL ARCHIVO cargado). */
  event_id_counts?: Record<string, number>
  risk_levels: Record<string, RiskLevelBlock>
  unique_hosts: number | null
  events_per_day: Record<string, number>
  events_per_day_by_level: Record<string, Record<string, number>>
  /** Serie por fecha+hora presente en los datos: "2026-10-23 07:00" → niveles. */
  events_per_hour_by_level?: Record<string, Record<string, number>>
  top_hosts: Record<string, number>
  top_hosts_by_level: Record<string, Record<string, number>>
  suspicious_events: number
  suspicious_details: SuspiciousDetail[]
  suspicious_correlations: Record<string, number>
  suspicious_by_ip: SuspiciousIp[]
  /**
   * Totales reales (sin el tope de 50 de las listas anteriores): el
   * dashboard debe enseñar el número de hosts/IPs distintos que tocan
   * eventos sospechosos, aunque las gráficas muestren como máximo 50.
   */
  suspicious_hosts_total?: number
  suspicious_ips_total?: number
  /** Serie temporal SOLO de sospechosos: "2026-01-10" → niveles. */
  suspicious_per_day_by_level?: Record<string, Record<string, number>>
  /** Serie por fecha+hora de sospechosos: "2026-01-10 09:00" → niveles. */
  suspicious_per_hour_by_level?: Record<string, Record<string, number>>
  sheet_names?: string[]
  missing_columns?: string[]
  file_name?: string
  generated_at?: string
  non_numeric_event_ids?: string[]
  ai_insights?: string[]
  /** Solo en /summary acotado (motor de filtros): registros del dataset completo. */
  total?: number
  /** Solo en /summary acotado: registros que pasan los filtros (igual que `rows`). */
  filtered_total?: number
}

export interface AnalysisState {
  analysisId: string | null
  status: 'idle' | 'uploading' | 'processing' | 'done' | 'error'
  summary: AnalysisSummary | null
  error: string | null
  /**
   * Aviso no crítico para el analista (p. ej. «análisis restaurado tras
   * recargar» o «el backend se reinició»). No es un error: no bloquea nada
   * y se muestra en una banda global desechable.
   */
  notice: string | null
}

export type AnalysisStore = AnalysisState & {
  setAnalysisId: (id: string) => void
  setStatus: (status: AnalysisState['status']) => void
  setSummary: (summary: AnalysisSummary) => void
  setError: (msg: string) => void
  setNotice: (msg: string | null) => void
  reset: () => void
}

export const useAnalysisStore = create<AnalysisStore>()(
  devtools((set) => ({
    analysisId: null,
    status: 'idle',
    summary: null,
    error: null,
    notice: null,
    setAnalysisId: (id) => set({ analysisId: id }),
    setStatus: (status) => set({ status }),
    setSummary: (summary) => set({ summary, status: 'done' }),
    setError: (msg) => set({ error: msg, status: 'error' }),
    setNotice: (msg) => set({ notice: msg }),
    reset: () => set({ analysisId: null, status: 'idle', summary: null, error: null, notice: null }),
  }))
)

/** Secciones de la aplicación (navegación). */
export type Section = 'upload' | 'dashboard' | 'security' | 'assistant' | 'attack' | 'ai' | 'risk' | 'report'

interface UiStore {
  section: Section
  setSection: (section: Section) => void
}

/**
 * Navegación global: vive en el store para que cualquier componente (p. ej.
 * los estados vacíos con botón «Ir a Carga de archivos») pueda cambiar de
 * sección sin pasar callbacks por todo el árbol.
 */
export const useUiStore = create<UiStore>()(
  devtools((set) => ({
    section: 'dashboard',
    setSection: (section) => set({ section }),
  }))
)
