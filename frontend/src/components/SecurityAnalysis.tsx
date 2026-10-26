import React, { useCallback, useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useAnalysisStore } from '../store'
import { getRows, RowsResponse } from '../api/client'
import { DashboardSection } from './DashboardSection'
import { EmptyState } from './EmptyState'
import { ChartInsight, selectionCells } from './ChartTools'
import {
  AXIS_TICK,
  BAR_CURSOR,
  GRID,
  LEVEL_LABELS,
  LEVEL_ORDER,
  TOOLTIP_STYLE,
  toCount,
  pct,
  useChartMotion,
  ChartClickState,
} from '../charts'
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
  const chartMotion = useChartMotion()

  const [data, setData] = useState<RowsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const pageSize = 10
  // Selección persistente de estas barras: la elegida gana contorno y halo y
  // el resto se atenúa, con su desglose en la tarjeta de debajo.
  const [focusIp, setFocusIp] = useState<string | null>(null)
  const [focusSuspHost, setFocusSuspHost] = useState<string | null>(null)

  const fetchSuspicious = useCallback(async () => {
    if (!analysisId || status !== 'done') return
    setLoading(true)
    try {
      setFetchError(null)
      const res = await getRows(analysisId, { page, pageSize, suspicious: true })
      setData(res)
    } catch {
      setData(null)
      setFetchError('No se pudieron cargar las filas sospechosas desde la API. Reintenta o revisa la conexión con el backend.')
    } finally {
      setLoading(false)
    }
  }, [analysisId, status, page])

  useEffect(() => {
    fetchSuspicious()
  }, [fetchSuspicious])

  if (!summary) {
    return (
      <EmptyState hint="Sube un archivo Excel para ver los eventos sospechosos y su correlación." />
    )
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1
  // En horizontal las IP se leen completas; se ordena por volumen para que la
  // comparación de arriba a abajo sea inmediata.
  const ipData = [...(summary.suspicious_by_ip ?? [])]
    .map(item => ({ ip: item.ip, hosts: toCount(item.hosts), events: toCount(item.events) }))
    .sort((a, b) => b.events - a.events)

  const hostData = Object.entries(summary.suspicious_correlations ?? {})
    .map(([host, count]) => ({ host, count: toCount(count) }))
    .sort((a, b) => b.count - a.count)

  const selectedIp = focusIp ? ipData.find(item => item.ip === focusIp) ?? null : null
  const selectedSuspHost = focusSuspHost
    ? hostData.find(item => item.host === focusSuspHost) ?? null
    : null
  const totalSuspicious = toCount(summary.suspicious_events)

  const pickIp = (state: ChartClickState<{ ip?: string }>): void => {
    const ip = state.activePayload?.[0]?.payload?.ip
    if (typeof ip !== 'string') return
    setFocusIp(prev => (prev === ip ? null : ip))
  }

  const pickSuspHost = (state: ChartClickState<{ host?: string }>): void => {
    const host = state.activePayload?.[0]?.payload?.host
    if (typeof host !== 'string') return
    setFocusSuspHost(prev => (prev === host ? null : host))
  }

  return (
    <section className="space-y-8">
      {/* KPIs */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-white rounded shadow p-4 text-center">
          <h3 className="text-sm font-medium text-gray-600">Eventos sospechosos</h3>
          <p className="text-3xl font-bold text-orange-600">{toCount(summary.suspicious_events)}</p>
          <p className="text-xs text-gray-500">
            {pct(toCount(summary.suspicious_events), toCount(summary.rows))}% de {summary.rows} registros
          </p>
        </div>
        <div className="bg-white rounded shadow p-4 text-center">
          <h3 className="text-sm font-medium text-gray-600">Hosts afectados</h3>
          <p className="text-3xl font-bold text-red-600">{hostData.length}</p>
          <p className="text-xs text-gray-500">con eventos sospechosos</p>
        </div>
        <div className="bg-white rounded shadow p-4 text-center">
          <h3 className="text-sm font-medium text-gray-600">IPs de origen</h3>
          <p className="text-3xl font-bold text-blue-600">{ipData.length}</p>
          <p className="text-xs text-gray-500">con actividad marcada para revisión</p>
        </div>
      </div>

      {/* Correlation: IP -> hosts */}
      <DashboardSection title="Correlación: IP de origen → hosts impactados">
        <p className="text-sm text-gray-600 mb-3">
          El alcance entre hosts ayuda a priorizar la investigación, pero el volumen por sí
          solo no demuestra que una IP sea maliciosa.
        </p>
        {ipData.length ? (
          <ResponsiveContainer width="100%" height={Math.max(280, ipData.length * 48)}>
            <BarChart
              data={ipData}
              layout="vertical"
              margin={{ top: 8, right: 44, left: 12, bottom: 8 }}
              onClick={pickIp}
            >
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" allowDecimals={false} tick={AXIS_TICK} />
              <YAxis dataKey="ip" type="category" width={112} tick={AXIS_TICK} />
              <Tooltip
                cursor={BAR_CURSOR}
                contentStyle={TOOLTIP_STYLE}
                formatter={(value: number, name: string) => [
                  `${toCount(value).toLocaleString()} ${name === 'Hosts distintos' ? 'hosts' : 'eventos'}`,
                  name,
                ]}
              />
              <Legend />
              <Bar
                dataKey="hosts"
                name="Hosts distintos"
                fill="#0891b2"
                isAnimationActive={chartMotion.isAnimationActive}
                animationDuration={chartMotion.duration}
                label={{ position: 'right', fontSize: 10, fill: '#475569' }}
              >
                {/* Recharts lee las celdas desde los hijos de cada serie; el i-ésimo
                    <Cell> se aplica al i-ésimo segmento (IP) de cada barra. */}
                {selectionCells(ipData, focusIp, i => i.ip, '#f97316', true)}
              </Bar>
              <Bar
                dataKey="events"
                name="Eventos sospechosos"
                fill="#f97316"
                isAnimationActive={chartMotion.isAnimationActive}
                animationDuration={chartMotion.duration}
                label={{ position: 'right', fontSize: 10, fill: '#475569' }}
              >
                {selectionCells(ipData, focusIp, i => i.ip, '#f97316', true)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-8 text-center text-sm text-gray-500">
            No hay IP sospechosas disponibles en este análisis.
          </p>
        )}

        <p className="mt-2 text-xs text-gray-500">
          Pulsá una barra para fijar la IP y ver su alcance completo.
        </p>

        <AnimatePresence mode="wait">
          {selectedIp && (
            <ChartInsight
              key={`ip-${selectedIp.ip}`}
              title={`IP de origen ${selectedIp.ip}`}
              color="#f97316"
              value={`${selectedIp.events.toLocaleString()} eventos`}
              caption={`${pct(selectedIp.events, totalSuspicious)}% de los ${totalSuspicious.toLocaleString()} eventos sospechosos`}
              rows={[
                { label: 'Hosts distintos', value: `${selectedIp.hosts} hosts` },
                { label: 'Del total sospechoso', value: `${pct(selectedIp.events, totalSuspicious)}%` },
              ]}
              note="Los volúmenes por sí solos no prueban malicia: sirven para priorizar la revisión de esos hosts."
              onClose={() => setFocusIp(null)}
            />
          )}
        </AnimatePresence>
      </DashboardSection>

      {/* Correlation: suspicious per host */}
      <DashboardSection title="Eventos sospechosos por host">
        {hostData.length ? (
          <ResponsiveContainer width="100%" height={Math.max(280, hostData.length * 46)}>
            <BarChart
              data={hostData}
              layout="vertical"
              margin={{ top: 8, right: 44, left: 12, bottom: 8 }}
              onClick={pickSuspHost}
            >
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" allowDecimals={false} tick={AXIS_TICK} />
              <YAxis dataKey="host" type="category" width={112} tick={AXIS_TICK} />
              <Tooltip
                cursor={BAR_CURSOR}
                contentStyle={TOOLTIP_STYLE}
                formatter={(value: number) => [`${toCount(value).toLocaleString()} eventos`, 'Eventos sospechosos']}
              />
              <Bar
                dataKey="count"
                name="Eventos sospechosos"
                fill="#f97316"
                isAnimationActive={chartMotion.isAnimationActive}
                animationDuration={chartMotion.duration}
                label={{ position: 'right', fontSize: 11, fill: '#475569' }}
              >
                {selectionCells(hostData, focusSuspHost, h => h.host, '#f97316')}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-8 text-center text-sm text-gray-500">
            El análisis no registró eventos sospechosos por host.
          </p>
        )}

        <p className="mt-2 text-xs text-gray-500">
          Pulsá una barra para fijar el host y ver cómo se reparte su actividad.
        </p>

        <AnimatePresence mode="wait">
          {selectedSuspHost && (
            <ChartInsight
              key={`susp-host-${selectedSuspHost.host}`}
              title={`Host ${selectedSuspHost.host}`}
              color="#f97316"
              value={`${selectedSuspHost.count.toLocaleString()} eventos`}
              caption={`${pct(selectedSuspHost.count, totalSuspicious)}% de los ${totalSuspicious.toLocaleString()} eventos marcados como sospechosos`}
              rows={LEVEL_ORDER.map(level => ({
                label: LEVEL_LABELS[level],
                value: `${toCount(summary.top_hosts_by_level?.[selectedSuspHost.host]?.[level])} eventos`,
              }))}
              note="El reparto por nivel procede de la misma clasificación que usa el dashboard."
              onClose={() => setFocusSuspHost(null)}
            />
          )}
        </AnimatePresence>
      </DashboardSection>

      {/* Full suspicious events table */}
      <DashboardSection title="Eventos sospechosos identificados">
        {fetchError && (
          <div role="alert" className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {fetchError}
          </div>
        )}
        <div className="overflow-auto border border-gray-200 rounded max-h-96">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-100 sticky top-0">
              <tr>
                <th className="px-3 py-2 text-left">Timestamp</th>
                <th className="px-3 py-2 text-left">Host</th>
                <th className="px-3 py-2 text-left">Event ID</th>
                <th className="px-3 py-2 text-left">IP origen</th>
                <th className="px-3 py-2 text-left">Descripción</th>
                <th className="px-3 py-2 text-left">Resultado</th>
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
                    {String(row.Result ?? '—')}
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
                  <td colSpan={8} className="px-3 py-6 text-center text-gray-500">Cargando…</td>
                </tr>
              )}
              {data && !loading && data.rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3 py-6 text-center text-gray-500">
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
