import React, { useCallback, useEffect, useRef, useState } from 'react'
import { getRows, RowsResponse } from '../api/client'
import { useAnalysisStore } from '../store'
import { LEVEL_LABELS } from '../charts'
import { DashboardSection } from './DashboardSection'

const LEVELS = [
  { id: '', label: 'Todos' },
  { id: 'critico', label: 'Crítico' },
  { id: 'alto', label: 'Alto' },
  { id: 'medio', label: 'Medio' },
  { id: 'bajo', label: 'Bajo' },
]

const LEVEL_BADGE: Record<string, { color: string; bg: string }> = {
  critico: { color: '#dc2626', bg: 'rgba(220,38,38,0.16)' },
  alto: { color: '#f97316', bg: 'rgba(249,115,22,0.16)' },
  medio: { color: '#facc15', bg: 'rgba(250,204,21,0.16)' },
  bajo: { color: '#10b981', bg: 'rgba(16,185,129,0.16)' },
}

// Tono de cada filtro de nivel («Todos» usa el cian del HUD)
const LEVEL_TONE: Record<string, string> = {
  '': '#1fc8ff',
  critico: '#dc2626',
  alto: '#f97316',
  medio: '#facc15',
  bajo: '#10b981',
}

// Orden de lectura para el analista: primero qué pasó (descripción) y si tuvo
// éxito o fallo (Result), después el resto de campos. Las columnas que no
// estén en esta lista (otros datasets) se muestran al final en su orden
// original: no se descarta ninguna.
const COLUMN_PRIORITY = [
  'Log_ID', 'Timestamp', 'Hostname', 'Event_ID', 'Event_Description', 'Result',
  'User', 'Source_IP', 'Destination', 'Log_Source', 'Process_CommandLine', 'Severity',
]

const orderColumns = (cols: string[]): string[] => {
  const rank = (c: string) => {
    const i = COLUMN_PRIORITY.indexOf(c)
    return i === -1 ? COLUMN_PRIORITY.length : i
  }
  return [...cols].sort((a, b) => rank(a) - rank(b) || cols.indexOf(a) - cols.indexOf(b))
}

// Columnas de texto largo: necesitan más ancho para leer el contexto de un vistazo
const LONG_COLUMNS = new Set(['Event_Description', 'Process_CommandLine'])

const DataTable: React.FC = () => {
  const analysisId = useAnalysisStore(state => state.analysisId)
  const status = useAnalysisStore(state => state.status)
  const summary = useAnalysisStore(state => state.summary)

  const [data, setData] = useState<RowsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [page, setPage] = useState(1)
  const [qInput, setQInput] = useState('') // texto del input (se estabiliza con debounce)
  const [q, setQ] = useState('') // búsqueda ya estabilizada, la que viaja al backend
  const [level, setLevel] = useState('')
  const [result, setResult] = useState('')
  const [suspicious, setSuspicious] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  // Un archivo nuevo arranca de cero: la página, los filtros y las filas del
  // análisis anterior no deben colarse en el nuevo (si la tabla anterior
  // estaba en la página 5 y el archivo nuevo solo tiene 4, se quedaba vacía).
  useEffect(() => {
    abortRef.current?.abort()
    setData(null)
    setError('')
    setPage(1)
    setQ('')
    setQInput('')
    setLevel('')
    setResult('')
    setSuspicious(false)
  }, [analysisId])

  // Búsqueda con debounce: una petición por pausa de tecleo, no por tecla.
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(qInput), 300)
    return () => window.clearTimeout(timer)
  }, [qInput])

  const fetchRows = useCallback(async () => {
    if (!analysisId || status !== 'done') return
    // Se cancela la petición en vuelo: si no, una respuesta lenta de un
    // filtro anterior puede pintar la tabla con los filtros actuales.
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setLoading(true)
    setError('')
    try {
      const res = await getRows(
        analysisId,
        { page, pageSize: 25, q, level, suspicious, result, signal: ctrl.signal },
      )
      if (ctrl.signal.aborted) return
      setData(res)
      // El backend recorta páginas fuera de rango y devuelve la última válida.
      setPage(res.page)
    } catch {
      if (ctrl.signal.aborted) return
      setError('No se pudieron cargar los registros')
      setData(null)
    } finally {
      if (!ctrl.signal.aborted) setLoading(false)
    }
  }, [analysisId, status, page, q, level, suspicious, result])

  useEffect(() => {
    fetchRows()
  }, [fetchRows])

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1)
  }, [q, level, suspicious, result])

  if (!analysisId || status !== 'done') {
    return null
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  const visibleColumns = orderColumns(data?.columns ?? [])
  // Valores reales del campo Result en el archivo, con su recuento.
  // Se ordenan por frecuencia: en JS las claves numéricas ("200", "404")
  // saldrían antes que las de texto si respetamos el orden del objeto.
  const resultOptions = Object.entries(summary?.result_counts ?? {})
    .sort((a, b) => b[1] - a[1])

  return (
    <DashboardSection title="Registros importados (visualización del Excel)">
      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input
          type="text"
          value={qInput}
          onChange={e => setQInput(e.target.value)}
          placeholder="Buscar en las columnas del archivo…"
          className="hud-input w-64"
        />
        <div className="flex overflow-hidden border border-hud-soft">
          {LEVELS.map(l => {
            const tone = LEVEL_TONE[l.id] ?? '#1fc8ff'
            const active = level === l.id
            return (
              <button
                key={l.id}
                onClick={() => setLevel(l.id)}
                className="border-r border-hud-soft px-3 py-2 font-mono text-[12px] tracking-wide transition-colors last:border-r-0"
                style={{
                  color: active ? tone : 'var(--text-dim)',
                  background: active ? `${tone}22` : 'transparent',
                  boxShadow: active ? `inset 0 -2px 0 0 ${tone}` : undefined,
                }}
              >
                {l.label}
              </button>
            )
          })}
        </div>
        {resultOptions.length > 0 && (
          <label className="flex cursor-pointer items-center gap-2 font-mono text-[12px] text-[var(--text-dim)]">
            Resultado
            <select
              value={result}
              onChange={e => setResult(e.target.value)}
              className="hud-input py-2"
            >
              <option value="">Todos</option>
              {resultOptions.map(([value, count]) => (
                <option key={value} value={value}>
                  {value} ({count})
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex cursor-pointer items-center gap-2 font-mono text-[12px] text-[var(--text-dim)]">
          <input
            type="checkbox"
            checked={suspicious}
            onChange={e => setSuspicious(e.target.checked)}
            className="accent-[#1fc8ff]"
          />
          Solo sospechosos
        </label>
        {loading && <span className="hud-label text-hud-line">Cargando…</span>}
      </div>

      {error && <p className="mb-2 font-mono text-sm text-[#f97316]">{error}</p>}

      {/* Table */}
      <div className="max-h-96 overflow-auto border border-hud-soft bg-inset/70">
        <table className="hud-table min-w-full">
          <caption className="sr-only">Registros del archivo importado, con su nivel de riesgo</caption>
          <thead className="sticky top-0 z-[1]">
            <tr>
              {visibleColumns.map(col => (
                <th key={col} scope="col" className="whitespace-nowrap">
                  {col}
                </th>
              ))}
              <th scope="col" className="whitespace-nowrap">Nivel</th>
            </tr>
          </thead>
          <tbody>
            {data?.rows.map((row, idx) => (
              <tr key={idx}>
                {visibleColumns.map(col => (
                  <td
                    key={col}
                    className={`whitespace-nowrap truncate ${
                      LONG_COLUMNS.has(col) ? 'max-w-sm' : 'max-w-56'
                    }`}
                    title={String(row[col] ?? '')}
                  >
                    {String(row[col] ?? '—')}
                  </td>
                ))}
                <td className="whitespace-nowrap">
                  <span
                    className="chip"
                    style={{
                      color: LEVEL_BADGE[row.risk_level]?.color ?? 'var(--text-dim)',
                      background: LEVEL_BADGE[row.risk_level]?.bg ?? 'transparent',
                    }}
                    title={row.risk_reason}
                  >
                    {LEVEL_LABELS[row.risk_level] ?? row.risk_level}
                  </span>
                </td>
              </tr>
            ))}
            {data && data.rows.length === 0 && (
              <tr>
                <td colSpan={visibleColumns.length + 1} className="px-3 py-6 text-center text-[var(--text-dim)]">
                  Sin resultados para los filtros aplicados
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="mt-3 flex items-center justify-between font-mono text-[12px] text-[var(--text-dim)]">
        <span>
          {data ? `${data.total} de ${data.total_rows} registros` : ''}
          {visibleColumns.length > 0 &&
            ` · ${visibleColumns.length} columnas (desplazá la tabla en horizontal para verlas todas)`}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page <= 1 || loading}
            className="btn-hud"
          >
            ← Anterior
          </button>
          <span className="text-hud-fg">
            Página {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages || loading}
            className="btn-hud"
          >
            Siguiente →
          </button>
        </div>
      </div>
    </DashboardSection>
  )
}

export default DataTable
