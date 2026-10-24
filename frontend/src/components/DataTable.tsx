import React, { useCallback, useEffect, useState } from 'react'
import { getRows, RowsResponse } from '../api/client'
import { useAnalysisStore } from '../store'
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

const MAX_COLS = 8

const DataTable: React.FC = () => {
  const analysisId = useAnalysisStore(state => state.analysisId)
  const status = useAnalysisStore(state => state.status)

  const [data, setData] = useState<RowsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [level, setLevel] = useState('')
  const [suspicious, setSuspicious] = useState(false)

  const fetchRows = useCallback(async () => {
    if (!analysisId || status !== 'done') return
    setLoading(true)
    setError('')
    try {
      const res = await getRows(analysisId, { page, pageSize: 25, q, level, suspicious })
      setData(res)
    } catch (err) {
      setError('No se pudieron cargar los registros')
    } finally {
      setLoading(false)
    }
  }, [analysisId, status, page, q, level, suspicious])

  useEffect(() => {
    fetchRows()
  }, [fetchRows])

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1)
  }, [q, level, suspicious])

  if (!analysisId || status !== 'done') {
    return null
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  const visibleColumns = (data?.columns ?? []).slice(0, MAX_COLS)

  return (
    <DashboardSection title="Registros importados (visualización del Excel)">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <input
          type="text"
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="Buscar en todos los campos…"
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
          <thead className="bg-gray-100 sticky top-0">
            <tr>
              {visibleColumns.map(col => (
                <th key={col} className="px-3 py-2 text-left font-semibold whitespace-nowrap">
                  {col}
                </th>
              ))}
              <th className="px-3 py-2 text-left font-semibold whitespace-nowrap">Nivel</th>
            </tr>
          </thead>
          <tbody>
            {data?.rows.map((row, idx) => (
              <tr key={idx} className={`${idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'} hover:bg-blue-50`}>
                {visibleColumns.map(col => (
                  <td key={col} className="px-3 py-2 whitespace-nowrap max-w-56 truncate" title={String(row[col] ?? '')}>
                    {String(row[col] ?? '—')}
                  </td>
                ))}
                <td className="px-3 py-2 whitespace-nowrap">
                  <span
                    className={`px-2 py-0.5 rounded text-xs font-semibold ${LEVEL_BADGE[row.risk_level] ?? 'bg-gray-200'}`}
                    title={row.risk_reason}
                  >
                    {row.risk_level}
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
