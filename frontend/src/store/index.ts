import create from 'zustand'
import { devtools } from 'zustand/middleware'

export interface SuspiciousDetail {
  Hostname?: string
  Event_ID?: string | number
  Event_Description?: string
  Timestamp?: string
  Source_IP?: string
  User?: string
  Severity?: string
  [key: string]: unknown
}

export interface AnalysisSummary {
  rows: number
  columns: string[]
  risk: string
  risk_justification: string
  severity_counts: Record<string, number>
  unique_hosts: number | null
  events_per_day: Record<string, number>
  top_hosts: Record<string, number>
  suspicious_events: number
  suspicious_details: SuspiciousDetail[]
  suspicious_correlations: Record<string, number>
  sheet_names?: string[]
  missing_columns?: string[]
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
