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

const LEVEL_BADGE: Record<string, string> = {
  critico: 'bg-red-600 text-white',
  alto: 'bg-orange-500 text-white',
  medio: 'bg-yellow-400 text-yellow-900',
  bajo: 'bg-emerald-500 text-white',
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
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <input
          type="text"
          value={qInput}
          onChange={e => setQInput(e.target.value)}
          placeholder="Buscar en las columnas del archivo…"
          className="border border-gray-300 rounded px-3 py-2 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-primary"
        />
        <div className="flex rounded overflow-hidden border border-gray-300">
          {LEVELS.map(l => (
            <button
              key={l.id}
              onClick={() => setLevel(l.id)}
              className={`px-3 py-2 text-sm ${
                level === l.id ? 'bg-primary text-white' : 'bg-white text-gray-700 hover:bg-gray-100'
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
        {resultOptions.length > 0 && (
          <label className="flex items-center gap-2 text-sm text-gray-700">
            Resultado
            <select
              value={result}
              onChange={e => setResult(e.target.value)}
              className="border border-gray-300 rounded px-2 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-primary"
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
        <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
          <input
            type="checkbox"
            checked={suspicious}
            onChange={e => setSuspicious(e.target.checked)}
            className="accent-primary"
          />
          Solo sospechosos
        </label>
        {loading && <span className="text-sm text-gray-500">Cargando…</span>}
      </div>

      {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

      {/* Table */}
      <div className="overflow-auto border border-gray-200 rounded max-h-96">
        <table className="min-w-full text-sm">
          <caption className="sr-only">Registros del archivo importado, con su nivel de riesgo</caption>
          <thead className="bg-gray-100 sticky top-0">
            <tr>
              {visibleColumns.map(col => (
                <th key={col} scope="col" className="px-3 py-2 text-left font-semibold whitespace-nowrap">
                  {col}
                </th>
              ))}
              <th scope="col" className="px-3 py-2 text-left font-semibold whitespace-nowrap">Nivel</th>
            </tr>
          </thead>
          <tbody>
            {data?.rows.map((row, idx) => (
              <tr key={idx} className={`${idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'} hover:bg-blue-50`}>
                {visibleColumns.map(col => (
                  <td
                    key={col}
                    className={`px-3 py-2 whitespace-nowrap truncate ${
                      LONG_COLUMNS.has(col) ? 'max-w-sm' : 'max-w-56'
                    }`}
                    title={String(row[col] ?? '')}
                  >
                    {String(row[col] ?? '—')}
                  </td>
                ))}
                <td className="px-3 py-2 whitespace-nowrap">
                  <span
                    className={`px-2 py-0.5 rounded text-xs font-semibold ${LEVEL_BADGE[row.risk_level] ?? 'bg-gray-200'}`}
                    title={row.risk_reason}
                  >
                    {LEVEL_LABELS[row.risk_level] ?? row.risk_level}
                  </span>
                </td>
              </tr>
            ))}
            {data && data.rows.length === 0 && (
              <tr>
                <td colSpan={visibleColumns.length + 1} className="px-3 py-6 text-center text-gray-500">
                  Sin resultados para los filtros aplicados
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between mt-3 text-sm text-gray-600">
        <span>
          {data ? `${data.total} de ${data.total_rows} registros` : ''}
          {visibleColumns.length > 0 &&
            ` · ${visibleColumns.length} columnas (desplazá la tabla en horizontal para verlas todas)`}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page <= 1 || loading}
            className="px-3 py-1 border rounded disabled:opacity-40 hover:bg-gray-100"
          >
            ← Anterior
          </button>
          <span>Página {page} / {totalPages}</span>
          <button
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages || loading}
            className="px-3 py-1 border rounded disabled:opacity-40 hover:bg-gray-100"
          >
            Siguiente →
          </button>
        </div>
      </div>
    </DashboardSection>
  )
}

export default DataTable
