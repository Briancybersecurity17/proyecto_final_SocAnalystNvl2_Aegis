import React, { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { useAnalysisStore } from '../store'
import { getRows, RowsResponse } from '../api/client'
import { DashboardSection } from './DashboardSection'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'

const LEVEL_BADGE: Record<string, string> = {
  critico: 'bg-red-600 text-white',
  alto: 'bg-orange-500 text-white',
  medio: 'bg-yellow-400 text-yellow-900',
  bajo: 'bg-emerald-500 text-white',
}

export const SecurityAnalysis: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)
  const analysisId = useAnalysisStore(state => state.analysisId)
  const status = useAnalysisStore(state => state.status)

  const [data, setData] = useState<RowsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const pageSize = 10

  const fetchSuspicious = useCallback(async () => {
    if (!analysisId || status !== 'done') return
    setLoading(true)
    try {
      const res = await getRows(analysisId, { page, pageSize, suspicious: true })
      setData(res)
    } catch {
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [analysisId, status, page])

  useEffect(() => {
    fetchSuspicious()
  }, [fetchSuspicious])

  if (!summary) {
    return (
      <div className="p-8 text-center text-gray-500 bg-white rounded shadow">
        <p className="text-lg font-medium mb-2">Sin datos todavía</p>
        <p>Sube un archivo Excel en la sección <strong>Carga de archivos</strong> para ver el análisis de seguridad.</p>
      </div>
    )
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1
  const ipData = (summary.suspicious_by_ip ?? []).map(item => ({
    ip: item.ip,
    hosts: item.hosts,
    events: item.events,
  }))

  return (
    <section className="space-y-8">
      {/* KPIs */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-white rounded shadow p-4 text-center">
          <h3 className="text-sm font-medium text-gray-600">Eventos sospechosos</h3>
          <p className="text-3xl font-bold text-orange-600">{summary.suspicious_events}</p>
          <p className="text-xs text-gray-500">de {summary.rows} registros</p>
        </div>
        <div className="bg-white rounded shadow p-4 text-center">
          <h3 className="text-sm font-medium text-gray-600">Hosts afectados</h3>
          <p className="text-3xl font-bold text-red-600">
            {Object.keys(summary.suspicious_correlations ?? {}).length}
          </p>
          <p className="text-xs text-gray-500">con eventos sospechosos</p>
        </div>
        <div className="bg-white rounded shadow p-4 text-center">
          <h3 className="text-sm font-medium text-gray-600">IPs de origen</h3>
          <p className="text-3xl font-bold text-blue-600">{ipData.length}</p>
          <p className="text-xs text-gray-500">top involucradas</p>
        </div>
      </div>

      {/* Correlation: IP -> hosts */}
      <DashboardSection title="Correlación: IP de origen → hosts impactados">
        <p className="text-sm text-gray-600 mb-3">
          Una IP que alcanza varios hosts indica posible barrido o movimiento lateral.
        </p>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={ipData} margin={{ top: 10, right: 30, left: 20, bottom: 10 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="ip" stroke="#555" />
            <YAxis allowDecimals={false} />
            <Tooltip />
            <Legend />
            <Bar dataKey="hosts" name="Hosts distintos" fill="#dc2626" />
            <Bar dataKey="events" name="Eventos sospechosos" fill="#f97316" />
          </BarChart>
        </ResponsiveContainer>
      </DashboardSection>

      {/* Correlation: suspicious per host */}
      <DashboardSection title="Eventos sospechosos por host">
        <ResponsiveContainer width="100%" height={280}>
          <BarChart
            data={Object.entries(summary.suspicious_correlations ?? {}).map(([host, count]) => ({ host, count }))}
            layout="vertical"
            margin={{ top: 10, right: 30, left: 80, bottom: 10 }}
          >
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis type="number" allowDecimals={false} />
            <YAxis dataKey="host" type="category" width={70} />
            <Tooltip />
            <Bar dataKey="count" name="Sospechosos" fill="#f97316" />
          </BarChart>
        </ResponsiveContainer>
      </DashboardSection>

      {/* Full suspicious events table */}
      <DashboardSection title="Eventos sospechosos identificados">
        <div className="overflow-auto border border-gray-200 rounded max-h-96">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-100 sticky top-0">
              <tr>
                <th className="px-3 py-2 text-left">Timestamp</th>
                <th className="px-3 py-2 text-left">Host</th>
                <th className="px-3 py-2 text-left">Event ID</th>
                <th className="px-3 py-2 text-left">IP origen</th>
                <th className="px-3 py-2 text-left">Descripción</th>
                <th className="px-3 py-2 text-left">Nivel</th>
                <th className="px-3 py-2 text-left">Justificación</th>
              </tr>
            </thead>
            <tbody>
              {data?.rows.map((row, idx) => (
                <motion.tr
                  key={`${data.page}:${idx}`}
                  initial={{ opacity: 0, y: 5 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.2, delay: Math.min(idx, 12) * 0.035, ease: 'easeOut' }}
                  className={`${idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'} hover:bg-blue-50`}
                >
                  <td className="px-3 py-2 whitespace-nowrap">{String(row.Timestamp ?? '—')}</td>
                  <td className="px-3 py-2 whitespace-nowrap font-medium">{String(row.Hostname ?? '—')}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{String(row.Event_ID ?? '—')}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{String(row.Source_IP ?? '—')}</td>
                  <td className="px-3 py-2 max-w-xs truncate" title={String(row.Event_Description ?? '')}>
                    {String(row.Event_Description ?? '—')}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className={`px-2 py-0.5 rounded text-xs font-semibold ${LEVEL_BADGE[row.risk_level] ?? 'bg-gray-200'}`}>
                      {row.risk_level}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-600 max-w-sm" title={row.risk_reason}>
                    {row.risk_reason}
                  </td>
                </motion.tr>
              ))}
              {loading && (
                <tr>
                  <td colSpan={7} className="px-3 py-6 text-center text-gray-500">Cargando…</td>
                </tr>
              )}
              {data && !loading && data.rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-3 py-6 text-center text-gray-500">
                    No se encontraron eventos sospechosos
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between mt-3 text-sm text-gray-600">
          <span>{data ? `${data.total} eventos sospechosos` : ''}</span>
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
    </section>
  )
}

export default SecurityAnalysis
