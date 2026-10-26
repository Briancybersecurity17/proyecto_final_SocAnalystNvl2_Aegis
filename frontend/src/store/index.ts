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
  sheet_names?: string[]
  missing_columns?: string[]
  file_name?: string
  generated_at?: string
  non_numeric_event_ids?: string[]
  ai_insights?: string[]
}

export interface AnalysisState {
  analysisId: string | null
  status: 'idle' | 'uploading' | 'processing' | 'done' | 'error'
  summary: AnalysisSummary | null
  error: string | null
}

export type AnalysisStore = AnalysisState & {
  setAnalysisId: (id: string) => void
  setStatus: (status: AnalysisState['status']) => void
  setSummary: (summary: AnalysisSummary) => void
  setError: (msg: string) => void
  reset: () => void
}

export const useAnalysisStore = create<AnalysisStore>()(
  devtools((set) => ({
    analysisId: null,
    status: 'idle',
    summary: null,
    error: null,
    setAnalysisId: (id) => set({ analysisId: id }),
    setStatus: (status) => set({ status }),
    setSummary: (summary) => set({ summary, status: 'done' }),
    setError: (msg) => set({ error: msg, status: 'error' }),
    reset: () => set({ analysisId: null, status: 'idle', summary: null, error: null }),
  }))
)

/** Secciones de la aplicación (navegación). */
export type Section = 'upload' | 'dashboard' | 'security' | 'assistant' | 'risk' | 'report'

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
