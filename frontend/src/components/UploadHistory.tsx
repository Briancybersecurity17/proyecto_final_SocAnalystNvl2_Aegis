import React, { useCallback, useEffect, useRef, useState } from 'react'
import { getAnalysis, getRecentAnalyses } from '../api/client'
import type { RecentAnalysis } from '../api/client'
import { fmtCount, LEVEL_COLORS, LEVEL_LABELS } from '../charts'
import { useAnalysisStore, useUiStore } from '../store'
import { useFilterStore } from '../store/filters'
import { DashboardSection } from './DashboardSection'

/**
 * Historial de subidas (spec sub-3 §3.5): los últimos análisis completados
 * que siguen en memoria del backend, con opción de reabrir cualquiera en el
 * Dashboard. Se carga al montar y cada vez que termina un análisis nuevo
 * (status → 'done'); un análisis que ya no está en memoria (backend
 * reiniciado) se avisa y se quita de la lista recargándola.
 */
const UploadHistory: React.FC = () => {
  const status = useAnalysisStore(state => state.status)
  const setAnalysisId = useAnalysisStore(state => state.setAnalysisId)
  const setStatus = useAnalysisStore(state => state.setStatus)
  const setSummary = useAnalysisStore(state => state.setSummary)
  const setNotice = useAnalysisStore(state => state.setNotice)
  const setSection = useUiStore(state => state.setSection)

  const [items, setItems] = useState<RecentAnalysis[]>([])
  const [loading, setLoading] = useState(true)
  const [opening, setOpening] = useState<string | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)

  // Petición en vuelo: se cancela al desmontar o al lanzar otra recarga.
  const controllerRef = useRef<AbortController | null>(null)

  /** Carga (o recarga) el historial; cancela la anterior si aún volaba. */
  const fetchHistory = useCallback(() => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setLoading(true)
    getRecentAnalyses(controller.signal)
      .then(data => {
        // Solo manda la última petición lanzada.
        if (controllerRef.current !== controller) return
        setItems(data)
        setLoadFailed(false)
        setLoading(false)
      })
      .catch(() => {
        if (controllerRef.current !== controller) return
        setLoading(false)
        // Un abort (cambio de status o desmonte) no es un error visible.
        if (!controller.signal.aborted) setLoadFailed(true)
      })
  }, [])

  // Carga al montar y en cada transición de status a 'done' (subida nueva).
  // Patrón anti doble-fetch: la ejecución que sigue al montaje con el mismo
  // status se ignora salvo que la anterior se haya abortado en el cleanup —
  // en StrictMode (desarrollo) React simula desmontar/remontar y sin esta
  // salvedad la primera petición se cancelaría sin relanzarse, dejando la
  // lista vacía. Un cambio de status que no sea a 'done' tampoco recarga.
  // El abort del desmonte vive en un efecto aparte: si estuviera en el cleanup
  // de este, una transición a 'error' (o 'processing') abortaría la carga en
  // vuelo sin relanzarla y la lista quedaría vacía con texto de "sin datos".
  const prevStatusRef = useRef<string | null>(null)
  useEffect(() => {
    const previo = prevStatusRef.current
    prevStatusRef.current = status
    if (previo !== null && previo !== status && status !== 'done') return
    fetchHistory()
  }, [status, fetchHistory])
  useEffect(() => () => controllerRef.current?.abort(), [])

  /** Reabre un análisis del historial en el Dashboard con sus filtros limpios. */
  const reopen = async (id: string) => {
    setOpening(id)
    try {
      const res = await getAnalysis(id)
      // Sin summary => el análisis terminó en error en el backend.
      if (!res.summary) {
        setNotice('Ese análisis terminó con error y no se puede reabrir.')
        return
      }
      setStatus('done')
      setAnalysisId(res.analysis_id)
      setSummary(res.summary)
      // Reabrir es una acción explícita de abrir limpio: si coincide con el
      // id activo, el reset por cambio de análisis de App no dispararía, así
      // que se pide acá (idempotente para el caso de id distinto).
      useFilterStore.getState().reset()
      setSection('dashboard')
    } catch (err) {
      const codigo = (err as { response?: { status?: number } })?.response?.status
      if (codigo === 404) {
        // Backend reiniciado: el análisis ya no está en memoria; se avisa
        // y se recarga la lista para que desaparezca.
        setNotice('Ese análisis ya no está en memoria (¿se reinició el backend?). Se actualiza la lista.')
        fetchHistory()
      } else {
        setNotice('No se pudo reabrir el análisis: revisá la conexión con el backend.')
      }
    } finally {
      setOpening(null)
    }
  }

  return (
    <DashboardSection title="Historial de subidas">
      {loadFailed && (
        <p className="font-mono text-[13px] text-[#ffb020]">
          No se pudo cargar el historial: comprobá que el backend esté activo.
        </p>
      )}

      {!loadFailed && loading && items.length === 0 && (
        <p className="font-mono text-[13px] text-[var(--text-dim)]">Cargando historial…</p>
      )}

      {/* Lista vacía: estado tranquilo, sin alerta ni color de error. */}
      {!loadFailed && !loading && items.length === 0 && (
        <p className="font-mono text-[13px] text-[var(--text-dim)]">
          Aún no hay análisis previos — subí un archivo para empezar.
        </p>
      )}

      {items.length > 0 && (
        <ul className="space-y-1">
          {items.map(item => (
            <li
              key={item.analysis_id}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-hud-soft py-2"
            >
              <span className="min-w-0 truncate font-mono text-[13px] text-[var(--text-bright)]">
                {item.file_name}
              </span>
              <span className="flex items-center gap-3">
                <span className="font-mono text-[12px] text-[var(--text-dim)]">
                  {item.generated_at ? new Date(item.generated_at).toLocaleString('es-ES') : '—'}
                </span>
                <span className="font-mono text-[12px] text-[var(--text)]">
                  {fmtCount(item.rows)} registros
                </span>
                <span className="chip" style={{ color: LEVEL_COLORS[item.risk] ?? '#1fc8ff' }}>
                  {LEVEL_LABELS[item.risk] ?? item.risk}
                </span>
                <button
                  type="button"
                  className="btn-hud whitespace-nowrap"
                  disabled={opening !== null}
                  onClick={() => void reopen(item.analysis_id)}
                >
                  {opening === item.analysis_id ? 'Abriendo…' : 'Reabrir'}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </DashboardSection>
  )
}

export default UploadHistory
