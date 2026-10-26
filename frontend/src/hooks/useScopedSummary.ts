import { useEffect, useRef, useState } from 'react'
import { getScopedSummary } from '../api/client'
import type { ScopedSummary } from '../api/client'
import type { AnalysisSummary } from '../store'
import { useAnalysisStore } from '../store'
import { selectIsFiltered, useFilterStore } from '../store/filters'

/** Retardo para agrupar cambios de filtro a toda velocidad antes de pedir. */
const DEBOUNCE_MS = 300

export type ScopedSummaryResult = {
  /** Resumen a pintar: el acotado si lo hay; nunca un dashboard en blanco. */
  summary: AnalysisSummary | null
  /** Registros del dataset completo (denominador del contador «Y de X»). */
  total: number
  /** Registros que pasan los filtros (del último resumen acotado conocido). */
  filteredTotal: number
  loading: boolean
  error: string | null
}

/**
 * Decide entre el resumen global y el resumen acotado por el motor de filtros.
 *
 * - Sin filtros activos: devuelve el `summary` global del store SIN hacer
 *   ninguna petición (decisión de diseño D6).
 * - Con filtros: pide `/summary` con debounce de 300 ms y cancelación por
 *   `AbortController`; mientras carga muestra el último resumen conocido y,
 *   en error de red, vuelve al global con el mensaje en `error` (spec §5.4).
 */
export const useScopedSummary = (): ScopedSummaryResult => {
  const analysisId = useAnalysisStore((s) => s.analysisId)
  const globalSummary = useAnalysisStore((s) => s.summary)
  const filters = useFilterStore()
  const isFiltered = selectIsFiltered(filters)

  const [scoped, setScoped] = useState<ScopedSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Serialización estable de los filtros (dependencia del efecto): cambia
  // solo cuando cambia su contenido (claves y valores ordenados), no con la
  // identidad de los objetos, para no disparar peticiones en bucle.
  const filtersKey = JSON.stringify([
    filters.from,
    filters.to,
    filters.level,
    filters.suspicious,
    filters.result,
    filters.q,
    Object.entries(filters.facets)
      .sort(([a], [b]) => (a < b ? -1 : 1))
      .map(([field, values]) => [field, [...values].sort()]),
  ])

  const prevAnalysisId = useRef<string | null>(null)

  // Cambio de análisis: los filtros de un dataset no pueden apuntar a otro,
  // así que se limpian y se descarta el resumen acotado anterior.
  useEffect(() => {
    if (prevAnalysisId.current === analysisId) return
    prevAnalysisId.current = analysisId
    useFilterStore.getState().reset()
    setScoped(null)
    setError(null)
  }, [analysisId])

  // Resumen acotado: solo con filtros activos; sin ellos, cero peticiones.
  useEffect(() => {
    if (!analysisId || !isFiltered) return

    setLoading(true)
    // Cada schedule tiene su propio AbortController: el cleanup de este
    // efecto cancela tanto el timer como la petición en vuelo. Confiamos en
    // el abort para el orden (una respuesta cancelada se rechaza, nunca se
    // entrega) y además se comprueba `signal.aborted` antes de escribir
    // estado, por si una respuesta vieja llegara en el mismo tick del cambio.
    const controller = new AbortController()

    const timer = window.setTimeout(() => {
      // Se leen los filtros VIVOS al disparar (no el closure del render):
      // si mientras corría el debounce se limpiaron (p. ej. por el reset al
      // cambiar de análisis), no se pide nada.
      const f = useFilterStore.getState()
      if (!selectIsFiltered(f)) {
        setLoading(false)
        return
      }
      setError(null)
      void (async () => {
        try {
          const data = await getScopedSummary(
            analysisId,
            {
              from: f.from,
              to: f.to,
              // Los campos vacíos no viajan: el backend rechazaría un
              // `?from=` vacío con 400 por fecha ilegible.
              level: f.level || undefined,
              suspicious: f.suspicious || undefined,
              result: f.result || undefined,
              q: f.q || undefined,
              facets: Object.keys(f.facets).length > 0 ? f.facets : undefined,
            },
            controller.signal,
          )
          if (controller.signal.aborted) return
          setScoped(data)
          setLoading(false)
        } catch (err) {
          // Cancelar (cambio de filtros/análisis o desmonte) no es un error.
          if (controller.signal.aborted) return
          setError(err instanceof Error ? err.message : 'No se pudo cargar el resumen filtrado')
          setScoped(null) // al fallar se vuelve al resumen global (spec §5.4)
          setLoading(false)
        }
      })()
    }, DEBOUNCE_MS)

    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [analysisId, isFiltered, filtersKey])

  // `total` es SIEMPRE el `rows` del resumen global: no cambia con los filtros.
  const total = globalSummary?.rows ?? 0

  // Sin análisis cargado o sin filtros: el resumen global, sin carga ni error.
  if (!analysisId || !isFiltered) {
    return { summary: globalSummary, total, filteredTotal: total, loading: false, error: null }
  }

  // Con filtros: último acotado conocido (o el global mientras carga).
  return {
    summary: scoped ?? globalSummary,
    total,
    filteredTotal: scoped ? scoped.filtered_total ?? scoped.rows : total,
    loading,
    error,
  }
}
