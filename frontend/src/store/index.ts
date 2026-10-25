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

export interface AnalysisSummary {
  rows: number
  columns: string[]
  risk: string
  risk_justification: string
  severity_counts: Record<string, number>
  level_counts: Record<string, number>
  risk_levels: Record<string, RiskLevelBlock>
  unique_hosts: number | null
  events_per_day: Record<string, number>
  events_per_day_by_level: Record<string, Record<string, number>>
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
