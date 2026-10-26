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
  fmtCount,
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

const LEVEL_BADGE: Record<string, { color: string; bg: string }> = {
  critico: { color: '#dc2626', bg: 'rgba(220,38,38,0.16)' },
  alto: { color: '#f97316', bg: 'rgba(249,115,22,0.16)' },
  medio: { color: '#facc15', bg: 'rgba(250,204,21,0.16)' },
  bajo: { color: '#10b981', bg: 'rgba(16,185,129,0.16)' },
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
  // La columna «Resultado» solo existe si el archivo la trae: si no, no se
  // muestra, para no dar a entender que todos los eventos carecen de resultado.
  const hasResult = summary.columns.includes('Result')

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
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="border border-hud-soft bg-inset/80 p-4 text-center [clip-path:var(--chamfer-sm)]">
          <h3 className="hud-label mb-2">Eventos sospechosos</h3>
          <p className="font-display text-3xl font-bold text-[#f97316]">
            {toCount(summary.suspicious_events)}
          </p>
          <p className="mt-1 font-mono text-[12px] text-[var(--text-dim)]">
            {pct(toCount(summary.suspicious_events), toCount(summary.rows))}% de {summary.rows} registros
          </p>
        </div>
        <div className="border border-hud-soft bg-inset/80 p-4 text-center [clip-path:var(--chamfer-sm)]">
          <h3 className="hud-label mb-2">Hosts afectados</h3>
          <p className="font-display text-3xl font-bold text-[#dc2626]">{hostData.length}</p>
          <p className="mt-1 font-mono text-[12px] text-[var(--text-dim)]">con eventos sospechosos</p>
        </div>
        <div className="border border-hud-soft bg-inset/80 p-4 text-center [clip-path:var(--chamfer-sm)]">
          <h3 className="hud-label mb-2">IPs de origen</h3>
          <p className="font-display text-3xl font-bold text-hud-line">{ipData.length}</p>
          <p className="mt-1 font-mono text-[12px] text-[var(--text-dim)]">
            con actividad marcada para revisión
          </p>
        </div>
      </div>

      {/* Correlation: IP -> hosts */}
      <DashboardSection title="Correlación: IP de origen → hosts impactados">
        <p className="mb-3 font-mono text-[12.5px] leading-relaxed text-[var(--text-dim)]">
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
                  `${fmtCount(value)} ${name === 'Hosts distintos' ? 'hosts' : 'eventos'}`,
                  name,
                ]}
              />
              <Legend />
              <Bar
                dataKey="hosts"
                name="Hosts distintos"
                fill="#06b6d4"
                isAnimationActive={chartMotion.isAnimationActive}
                animationDuration={chartMotion.duration}
                label={{ position: 'right', fontSize: 10, fill: '#bfefff' }}
              >
                {/* Recharts lee las celdas desde los hijos de cada serie; el i-ésimo
                    <Cell> se aplica al i-ésimo segmento (IP) de cada barra. */}
                {selectionCells(ipData, focusIp, i => i.ip, '#f97316', true, '#06b6d4')}
              </Bar>
              <Bar
                dataKey="events"
                name="Eventos sospechosos"
                fill="#f97316"
                isAnimationActive={chartMotion.isAnimationActive}
                animationDuration={chartMotion.duration}
                label={{ position: 'right', fontSize: 10, fill: '#bfefff' }}
              >
                {selectionCells(ipData, focusIp, i => i.ip, '#f97316', true, '#f97316')}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-8 text-center font-mono text-[13px] text-[var(--text-dim)]">
            No hay IP sospechosas disponibles en este análisis.
          </p>
        )}

        <p className="mt-2 font-mono text-[12px] text-[var(--text-dim)]">
          Pulsá una barra para fijar la IP y ver su alcance completo.
        </p>

        <AnimatePresence mode="wait">
          {selectedIp && (
            <ChartInsight
              key={`ip-${selectedIp.ip}`}
              title={`IP de origen ${selectedIp.ip}`}
              color="#f97316"
              value={`${fmtCount(selectedIp.events)} eventos`}
              caption={`${pct(selectedIp.events, totalSuspicious)}% de los ${fmtCount(totalSuspicious)} eventos sospechosos`}
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
                formatter={(value: number) => [`${fmtCount(value)} eventos`, 'Eventos sospechosos']}
              />
              <Bar
                dataKey="count"
                name="Eventos sospechosos"
                fill="#f97316"
                isAnimationActive={chartMotion.isAnimationActive}
                animationDuration={chartMotion.duration}
                label={{ position: 'right', fontSize: 11, fill: '#bfefff' }}
              >
                {selectionCells(hostData, focusSuspHost, h => h.host, '#f97316')}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-8 text-center font-mono text-[13px] text-[var(--text-dim)]">
            El análisis no registró eventos sospechosos por host.
          </p>
        )}

        <p className="mt-2 font-mono text-[12px] text-[var(--text-dim)]">
          Pulsá una barra para fijar el host y ver cómo se reparte su actividad.
        </p>

        <AnimatePresence mode="wait">
          {selectedSuspHost && (
            <ChartInsight
              key={`susp-host-${selectedSuspHost.host}`}
              title={`Host ${selectedSuspHost.host}`}
              color="#f97316"
              value={`${fmtCount(selectedSuspHost.count)} eventos`}
              caption={`${pct(selectedSuspHost.count, totalSuspicious)}% de los ${fmtCount(totalSuspicious)} eventos marcados como sospechosos`}
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
          <div role="alert" className="mb-3 border border-[#dc2626]/60 bg-[#dc2626]/10 px-3 py-2 font-mono text-[13px] text-[#f87171]">
            {fetchError}
          </div>
        )}
        <div className="max-h-96 overflow-auto border border-hud-soft bg-inset/70">
          <table className="hud-table min-w-full">
            <caption className="sr-only">Eventos sospechosos identificados en el archivo</caption>
            <thead className="sticky top-0 z-[1]">
              <tr>
                <th scope="col" className="whitespace-nowrap">Timestamp</th>
                <th scope="col" className="whitespace-nowrap">Host</th>
                <th scope="col" className="whitespace-nowrap">Event ID</th>
                <th scope="col" className="whitespace-nowrap">IP origen</th>
                <th scope="col">Descripción</th>
                {hasResult && <th scope="col" className="whitespace-nowrap">Resultado</th>}
                <th scope="col" className="whitespace-nowrap">Nivel</th>
                <th scope="col">Justificación</th>
              </tr>
            </thead>
            <tbody>
              {data?.rows.map((row, idx) => (
                <motion.tr
                  key={`${data.page}:${idx}`}
                  initial={{ opacity: 0, y: 5 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.2, delay: Math.min(idx, 12) * 0.035, ease: 'easeOut' }}
                >
                  <td className="whitespace-nowrap">{String(row.Timestamp ?? '—')}</td>
                  <td className="whitespace-nowrap font-semibold text-hud-fg">{String(row.Hostname ?? '—')}</td>
                  <td className="whitespace-nowrap">{String(row.Event_ID ?? '—')}</td>
                  <td className="whitespace-nowrap">{String(row.Source_IP ?? '—')}</td>
                  <td className="max-w-xs truncate" title={String(row.Event_Description ?? '')}>
                    {String(row.Event_Description ?? '—')}
                  </td>
                  {hasResult && (
                    <td className="whitespace-nowrap">
                      {String(row.Result ?? '—')}
                    </td>
                  )}
                  <td className="whitespace-nowrap">
                    <span
                      className="chip"
                      style={{
                        color: LEVEL_BADGE[row.risk_level]?.color ?? 'var(--text-dim)',
                        background: LEVEL_BADGE[row.risk_level]?.bg ?? 'transparent',
                      }}
                    >
                      {LEVEL_LABELS[row.risk_level] ?? row.risk_level}
                    </span>
                  </td>
                  <td className="max-w-sm text-[11.5px] text-[var(--text-dim)]" title={row.risk_reason}>
                    {row.risk_reason}
                  </td>
                </motion.tr>
              ))}
              {loading && (
                <tr>
                  <td colSpan={hasResult ? 8 : 7} className="px-3 py-6 text-center text-[var(--text-dim)]">
                    Cargando…
                  </td>
                </tr>
              )}
              {data && !loading && data.rows.length === 0 && (
                <tr>
                  <td colSpan={hasResult ? 8 : 7} className="px-3 py-6 text-center text-[var(--text-dim)]">
                    No se encontraron eventos sospechosos
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 font-mono text-[12px] text-[var(--text-dim)]">
          <span className="max-w-md">{data ? `${data.total} eventos sospechosos` : ''}</span>
          <div className="flex shrink-0 items-center gap-2">
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page <= 1 || loading}
              className="btn-hud whitespace-nowrap"
            >
              ← Anterior
            </button>
            <span className="whitespace-nowrap text-hud-fg">
              Página {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages || loading}
              className="btn-hud whitespace-nowrap"
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
