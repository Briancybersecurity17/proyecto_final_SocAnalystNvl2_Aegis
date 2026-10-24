import create from 'zustand'
import { devtools } from 'zustand/middleware'

export interface AnalysisState {
  analysisId: string | null
  status: 'idle' | 'uploading' | 'processing' | 'done' | 'error'
  summary: any | null
  error: string | null
}

export type AnalysisStore = AnalysisState & {
  setAnalysisId: (id: string) => void
  setStatus: (status: AnalysisState['status']) => void
  setSummary: (summary: any) => void
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
