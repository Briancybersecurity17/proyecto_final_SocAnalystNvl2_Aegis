import type { Section } from './store'

/**
 * Referencia al análisis activo guardada en el navegador.
 *
 * El backend mantiene los análisis en memoria; lo único que la pestaña debe
 * recordar es *cuál* era el análisis y en qué sección estaba el analista.
 * Así, un F5 —o el gestor de memoria del navegador que recicla la pestaña—
 * no obliga a subir el Excel otra vez: al volver se consulta el backend con
 * ese id y se restaura la vista.
 *
 * Todo lo que se persiste es un identificador: ningún dato del archivo se
 * copia aquí (la fuente de verdad sigue siendo el backend).
 */

const KEY = 'dojo_soc.analysis_ref'

export interface AnalysisRef {
  analysisId: string
  section: Section
  savedAt: string
}

const SECTIONS: Section[] = ['upload', 'dashboard', 'security', 'assistant', 'attack', 'ai', 'risk', 'report']

/** Guarda la referencia. Si localStorage no está disponible (modo privado,
 *  cuota agotada) la app sigue funcionando, solo que sin restaurar. */
export const saveAnalysisRef = (analysisId: string, section: Section): void => {
  try {
    const payload: AnalysisRef = { analysisId, section, savedAt: new Date().toISOString() }
    window.localStorage.setItem(KEY, JSON.stringify(payload))
  } catch {
    // almacenamiento no disponible: se ignora deliberadamente
  }
}

export const loadAnalysisRef = (): AnalysisRef | null => {
  try {
    const raw = window.localStorage.getItem(KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<AnalysisRef>
    if (!parsed || typeof parsed.analysisId !== 'string' || !parsed.analysisId) return null
    const section = SECTIONS.includes(parsed.section as Section)
      ? (parsed.section as Section)
      : 'dashboard'
    return {
      analysisId: parsed.analysisId,
      section,
      savedAt: typeof parsed.savedAt === 'string' ? parsed.savedAt : '',
    }
  } catch {
    return null
  }
}

export const clearAnalysisRef = (): void => {
  try {
    window.localStorage.removeItem(KEY)
  } catch {
    // idem
  }
}
