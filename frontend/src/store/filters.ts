import { create } from 'zustand'
import type { StoreApi, UseBoundStore } from 'zustand'

/**
 * Filtros del motor de consulta del dashboard.
 *
 * Vive en su propio store (separado del análisis) porque cambia mucho más
 * a menudo: cada toggle re-renderiza solo a quien lo escuche y su vida útil
 * es la de un análisis (reset() al cambiar de dataset).
 */
export type FilterState = {
  /** Rango de fechas (YYYY-MM-DD); null = sin filtro. */
  from: string | null
  to: string | null
  /** Nivel de riesgo ('' = todos). */
  level: string
  /** Solo eventos marcados como sospechosos. */
  suspicious: boolean
  /** Resultado del evento (Success/Failure/…; '' = todos). */
  result: string
  /** Búsqueda de texto libre ('' = sin búsqueda). */
  q: string
  /** Facetas por campo: 'Hostname' → ['DC-01', 'WS-018'] (OR dentro del campo). */
  facets: Record<string, string[]>
  /**
   * Época de limpieza: 0 al arrancar; +1 en cada `clearAll` y `reset`.
   * Sirve para que los timers pendientes (p. ej. el debounce de `q`)
   * detecten que fueron invalidados por una limpieza total y no reescriban
   * un estado ya vaciado.
   */
  clearEpoch: number
}

export type FilterActions = {
  setRange: (from: string | null, to: string | null) => void
  /** Alterna un valor dentro de la faceta del campo (quita la clave si queda vacía). */
  toggleFacet: (field: string, value: string) => void
  setLevel: (level: string) => void
  setQ: (q: string) => void
  setResult: (result: string) => void
  setSuspicious: (suspicious: boolean) => void
  /** Vuelve a filtros neutros (botón «Limpiar filtros»). */
  clearAll: () => void
  /** Igual que clearAll: se expone aparte para el cambio de análisis. */
  reset: () => void
}

const EMPTY_FILTERS: FilterState = {
  from: null,
  to: null,
  level: '',
  suspicious: false,
  result: '',
  q: '',
  facets: {},
  clearEpoch: 0,
}

export const useFilterStore: UseBoundStore<StoreApi<FilterState & FilterActions>> =
  create<FilterState & FilterActions>()((set) => ({
    ...EMPTY_FILTERS,
    setRange: (from, to) => set({ from, to }),
    toggleFacet: (field, value) =>
      set((s) => {
        const current = s.facets[field] ?? []
        const next = current.includes(value)
          ? current.filter((v) => v !== value)
          : [...current, value]
        const facets = { ...s.facets }
        if (next.length > 0) facets[field] = next
        else delete facets[field]
        return { facets }
      }),
    setLevel: (level) => set({ level }),
    setQ: (q) => set({ q }),
    setResult: (result) => set({ result }),
    setSuspicious: (suspicious) => set({ suspicious }),
    // La época avanza en ambas limpiezas totales: los timers pendientes
    // (debounce de `q`) comparan contra el valor vivo y se abstienen.
    clearAll: () => set((s) => ({ ...EMPTY_FILTERS, clearEpoch: s.clearEpoch + 1 })),
    reset: () => set((s) => ({ ...EMPTY_FILTERS, clearEpoch: s.clearEpoch + 1 })),
  }))

/**
 * true si hay algún filtro activo (algún campo no vacío). Es la condición
 * para decidir entre el resumen global y el acotado del motor de filtros.
 */
export const selectIsFiltered = (s: FilterState): boolean =>
  s.from !== null ||
  s.to !== null ||
  s.level !== '' ||
  s.result !== '' ||
  s.q !== '' ||
  s.suspicious ||
  Object.values(s.facets).some((values) => values.length > 0)
