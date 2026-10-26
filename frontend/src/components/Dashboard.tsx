import React, { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { SummaryCard } from './SummaryCard'
import { DashboardSection } from './DashboardSection'
import { EmptyState } from './EmptyState'
import { ChartInsight, selectionCells } from './ChartTools'
import { useAnalysisStore } from '../store'
import { stagger, cardIn, useCountUp } from '../motion'
import {
  LEVEL_COLORS,
  LEVEL_ORDER,
  LEVEL_LABELS,
  AXIS_TICK,
  GRID,
  TOOLTIP_STYLE,
  BAR_CURSOR,
  LINE_CURSOR,
  CHART_STAGGER,
  glowVars,
  toCount,
  pct,
  useChartMotion,
  ChartClickState,
} from '../charts'
import {
  Tooltip,
  Legend,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  AreaChart,
  Area,
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  ReferenceLine,
  Sector,
} from 'recharts'

/** Contador animado para las cifras de las tarjetas de criticidad. */
const Counter: React.FC<{ value: number }> = ({ value }) => <>{useCountUp(value)}</>

/** "2026-10-23 07:00" → "23/10 07:00" (etiqueta corta para el eje). */
const hourLabel = (stamp: string): string => {
  const [date, time] = stamp.split(' ')
  const [, month, day] = date.split('-')
  return `${Number(day)}/${Number(month)} ${time}`
}

/** Un punto de la serie temporal, ya por hora ya por día. */
type TimePoint = {
  label: string
  detail: string
  total: number
  critico: number
  alto: number
  medio: number
  bajo: number
}

/** Fila que entrega Recharts al pulsar los gráficos del dashboard. */
type DashboardClick = ChartClickState<TimePoint & { host?: string; severity?: string }>

const Dashboard: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)
  const [hostLevel, setHostLevel] = useState<string>('todos')
  // Selección persistente: lo que queda marcado (brillo + contorno) y de lo
  // que la tarjeta de debajo muestra el desglose completo.
  const [focusLevel, setFocusLevel] = useState<string | null>(null)
  const [focusSlot, setFocusSlot] = useState<string | null>(null)
  const [focusHost, setFocusHost] = useState<string | null>(null)
  const [focusRaw, setFocusRaw] = useState<string | null>(null)
  const chartMotion = useChartMotion()

  if (!summary) {
    return (
      <EmptyState hint="Sube un archivo Excel para ver las estadísticas y los gráficos por criticidad." />
    )
  }

  const rows = toCount(summary.rows)
  const suspicious = toCount(summary.suspicious_events)
  const levelCounts = summary.level_counts ?? {}

  // Distribución por criticidad (clasificación derivada del análisis)
  const severityData = LEVEL_ORDER.map(level => ({
    name: LEVEL_LABELS[level],
    value: toCount(levelCounts[level]),
    level,
  }))

  // Valores originales de la columna Severity del archivo, sin convertir
  const rawSeverityData = Object.entries(summary.severity_counts ?? {}).map(
    ([severity, count]) => ({ severity, count: toCount(count) })
  )

  // Serie horaria real (fecha+hora presentes en los timestamps del archivo).
  // Si hay varios tramos se usa como eje principal: da mucha más resolución
  // que la agregación diaria y no se inventa ningún dato.
  const hourlyData: TimePoint[] = Object.entries(summary.events_per_hour_by_level ?? {})
    .map(([stamp, levels]) => {
      const critico = toCount(levels.critico)
      const alto = toCount(levels.alto)
      const medio = toCount(levels.medio)
      const bajo = toCount(levels.bajo)
      return {
        label: hourLabel(stamp),
        detail: stamp,
        critico,
        alto,
        medio,
        bajo,
        total: critico + alto + medio + bajo,
      }
    })
    .sort((a, b) => a.detail.localeCompare(b.detail))

  const dailyData: TimePoint[] = Object.entries(summary.events_per_day_by_level ?? {})
    .map(([date, levels]) => {
      const critico = toCount(levels.critico)
      const alto = toCount(levels.alto)
      const medio = toCount(levels.medio)
      const bajo = toCount(levels.bajo)
      return {
        label: date,
        detail: date,
        critico,
        alto,
        medio,
        bajo,
        total: critico + alto + medio + bajo,
      }
    })
    .sort((a, b) => a.detail.localeCompare(b.detail))

  // Granularidad efectiva: hora si el backend la aporta con varios tramos;
  // si no, día (y si solo hay un día, se avisa en vez de fingir una tendencia)
  const hourly = hourlyData.length > 1
  const timeData = hourly ? hourlyData : dailyData
  const totalByLabel = new Map(timeData.map(p => [p.label, p.total]))

  const hostsData = Object.entries(summary.top_hosts_by_level ?? {})
    .map(([host, levels]) => ({
      host,
      critico: toCount(levels.critico),
      alto: toCount(levels.alto),
      medio: toCount(levels.medio),
      bajo: toCount(levels.bajo),
    }))
    .map(host => ({
      ...host,
      total:
        hostLevel === 'todos'
          ? host.critico + host.alto + host.medio + host.bajo
          : toCount(host[hostLevel as 'critico' | 'alto' | 'medio' | 'bajo']),
    }))
    .sort((a, b) => b.total - a.total)

  const shownLevels = hostLevel === 'todos' ? [...LEVEL_ORDER] : [hostLevel]

  // --- selección activa (lo que queda marcado en cada gráfico) -------------
  const selectedIndex = focusLevel ? severityData.findIndex(item => item.level === focusLevel) : -1
  const selectedLevel = selectedIndex >= 0 ? severityData[selectedIndex] : null
  const riskBlock = focusLevel ? summary.risk_levels?.[focusLevel] : undefined

  // Hosts con más eventos en el nivel seleccionado (misma fuente que la
  // sección de hosts: top_hosts_by_level del resumen).
  const topHostsForLevel = focusLevel
    ? Object.entries(summary.top_hosts_by_level ?? {})
        .map(([host, levels]) => ({ host, value: toCount(levels[focusLevel]) }))
        .filter(item => item.value > 0)
        .sort((a, b) => b.value - a.value)
        .slice(0, 4)
    : []

  const totalSeries = timeData.reduce((sum, point) => sum + point.total, 0)
  const selectedSlot = focusSlot ? timeData.find(point => point.label === focusSlot) ?? null : null

  const selectedHostData = focusHost ? hostsData.find(host => host.host === focusHost) ?? null : null

  /**
   * Marcador del tramo horario/diario seleccionado: una línea vertical con
   * halo sobre el punto elegido. Se usa la etiqueta (y no un rango) porque el
   * eje es de categorías: un `ReferenceArea` entre ticks vecinos acabaría
   * resaltando dos tramos en vez de uno.
   */
  const slotBandElement = selectedSlot ? (
    <ReferenceLine
      x={selectedSlot.label}
      stroke="#1e40af"
      strokeWidth={2}
      strokeDasharray="6 4"
      ifOverflow="extendDomain"
      className="chart-glow"
    />
  ) : null

  /** Clic en la serie: fija o libera el tramo pulsado. */
  const pickSlot = (state: DashboardClick): void => {
    const label = state.activePayload?.[0]?.payload?.label ?? state.activeLabel ?? null
    if (!label) return
    setFocusSlot(prev => (prev === label ? null : label))
  }

  /** Clic sobre una barra de host: fija o libera ese host. */
  const pickHost = (state: DashboardClick): void => {
    const host = state.activePayload?.[0]?.payload?.host
    if (typeof host !== 'string') return
    setFocusHost(prev => (prev === host ? null : host))
  }

  /** Clic sobre una barra de Severity original: fija o libera ese valor. */
  const pickRaw = (state: DashboardClick): void => {
    const severity = state.activePayload?.[0]?.payload?.severity
    if (typeof severity !== 'string') return
    setFocusRaw(prev => (prev === severity ? null : severity))
  }

  const totalHostEvents = hostsData.reduce((sum, host) => sum + host.total, 0)
  const selectedRaw = focusRaw ? rawSeverityData.find(item => item.severity === focusRaw) ?? null : null

  return (
    <section className="space-y-8">
      {/* Archivo analizado y momento del resumen */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500">
        <span>
          Conjunto analizado:{' '}
          <strong className="text-gray-700">{summary.file_name || 'archivo importado'}</strong>
        </span>
        <span>
          {summary.generated_at
            ? `Resumen generado: ${summary.generated_at.replace('T', ' ')}`
            : 'Datos del análisis actual'}
        </span>
      </div>

      {/* KPIs */}
      <motion.div
        variants={stagger(0.06)}
        initial="hidden"
        animate="show"
        className="grid grid-cols-2 lg:grid-cols-5 gap-4"
      >
        <SummaryCard label="Registros totales" value={rows} />
        <SummaryCard label="Eventos sospechosos" value={suspicious} />
        <SummaryCard label="Sospechosos del total" value={`${pct(suspicious, rows)}%`} />
        <SummaryCard label="Hosts únicos" value={summary.unique_hosts ?? '—'} />
        <SummaryCard
          label="Riesgo global"
          value={<span>{LEVEL_LABELS[summary.risk] ?? summary.risk ?? '—'}</span>}
        />
      </motion.div>

      {/* Desglose por nivel */}
      <motion.div
        variants={stagger(0.06)}
        initial="hidden"
        animate="show"
        className="grid grid-cols-2 lg:grid-cols-4 gap-4"
      >
        {LEVEL_ORDER.map(level => {
          const count = toCount(levelCounts[level])
          const color = LEVEL_COLORS[level]
          return (
            <motion.div
              key={level}
              variants={cardIn}
              whileHover={{ y: -3, transition: { duration: 0.18 } }}
              className="bg-white rounded shadow p-4 border-l-4 transition-shadow duration-200 hover:shadow-md"
              style={{ borderLeftColor: color }}
            >
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-600">{LEVEL_LABELS[level]}</h3>
                <span className="w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
              </div>
              <p className="text-3xl font-bold mt-1" style={{ color }}>
                <Counter value={count} />
              </p>
              <p className="text-xs text-gray-500 mt-1">{pct(count, rows)}% de {rows} registros</p>
            </motion.div>
          )
        })}
      </motion.div>

      {/* AI observations over the imported data */}
      {summary.ai_insights && summary.ai_insights.length > 0 && (
        <DashboardSection title="Observaciones del análisis asistido por IA">
          <div className="flex flex-wrap gap-2 mb-4">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 border border-blue-200 px-3 py-1 text-xs font-medium text-blue-800">
              <span className="h-1.5 w-1.5 rounded-full bg-blue-500" />
              Redactadas solo con cifras calculadas de tus registros
            </span>
            <span className="inline-flex items-center rounded-full bg-gray-100 border border-gray-200 px-3 py-1 text-xs text-gray-600">
              {summary.generated_at ? `Generado ${summary.generated_at.replace('T', ' ')}` : ''}
            </span>
          </div>
          <ul className="space-y-3">
            {summary.ai_insights.map((text, i) => (
              <li key={i} className="flex gap-3 text-sm text-gray-700 leading-relaxed">
                <span className="shrink-0 h-6 w-6 rounded-full bg-primary text-white text-xs font-bold flex items-center justify-center">
                  {i + 1}
                </span>
                <p>{text}</p>
              </li>
            ))}
          </ul>
        </DashboardSection>
      )}

      {/* Criticality distribution: donut + list (labels never overlap) */}
      <DashboardSection title="Distribución por criticidad">
        <p className="text-sm text-gray-600 mb-4">
          Clasificación calculada por el análisis; no equivale al campo original <code>Severity</code>.
          <span className="ml-2 text-gray-500">Pulsá un sector —o su fila— para ver su desglose.</span>
        </p>
        {severityData.every(item => item.value === 0) ? (
          <p className="py-10 text-center text-sm text-gray-500">
            No hay datos de clasificación para representar.
          </p>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-6 items-center lg:grid-cols-[minmax(0,1fr)_240px]">
              <ResponsiveContainer width="100%" height={320}>
                <PieChart>
                  <Pie
                    data={severityData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius="45%"
                    outerRadius="76%"
                    paddingAngle={2}
                    activeIndex={selectedIndex}
                    activeShape={
                      focusLevel && selectedLevel ? (
                        <Sector
                          className="chart-glow"
                          style={glowVars(LEVEL_COLORS[selectedLevel.level])}
                          stroke="#ffffff"
                          strokeWidth={2.5}
                        />
                      ) : undefined
                    }
                    inactiveShape={focusLevel ? { fillOpacity: 0.32 } : undefined}
                    onClick={(entry: { level?: string }) =>
                      setFocusLevel(prev =>
                        entry?.level && prev === entry.level ? null : entry?.level ?? null
                      )
                    }
                    isAnimationActive={chartMotion.isAnimationActive}
                    animationDuration={chartMotion.duration}
                  >
                    {severityData.map(entry => (
                      <Cell key={entry.level} fill={LEVEL_COLORS[entry.level]} stroke="#fff" strokeWidth={2} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(value: number, _name, item) => [
                      `${toCount(value).toLocaleString()} registros (${pct(toCount(value), rows)}%)`,
                      item?.payload?.name ?? _name,
                    ]}
                  />
                  {/* Sin <Legend />: la lista de la derecha ya es la leyenda
                      (mismo color, nombre, recuento y %) y no se duplica */}
                </PieChart>
              </ResponsiveContainer>

              {/* Leyenda con cifras exactas: sustituye las etiquetas que se
                  solapaban sobre los sectores pequeños del donut. Además
                  selecciona el nivel al pulsarla. */}
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 lg:grid-cols-1">
                {severityData.map(item => {
                  const active = focusLevel === item.level
                  const dimmed = focusLevel !== null && !active
                  return (
                    <button
                      key={item.level}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setFocusLevel(prev => (prev === item.level ? null : item.level))}
                      className={`flex items-center justify-between gap-3 border-b border-gray-100 pb-2 text-left transition-all duration-200 hover:opacity-100 ${
                        dimmed ? 'opacity-50' : 'opacity-100'
                      }`}
                    >
                      <span className="flex items-center gap-2 text-sm text-gray-700">
                        <span
                          className="inline-block h-3 w-3 rounded-sm"
                          style={{
                            background: LEVEL_COLORS[item.level],
                            boxShadow: active ? `0 0 8px ${LEVEL_COLORS[item.level]}` : undefined,
                          }}
                        />
                        {item.name}
                      </span>
                      <span className="text-right">
                        <strong className="text-sm text-gray-900">{item.value.toLocaleString()}</strong>
                        <span className="block text-xs text-gray-500">{pct(item.value, rows)}%</span>
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>

            <AnimatePresence mode="wait">
              {selectedLevel && (
                <ChartInsight
                  key={`nivel-${selectedLevel.level}`}
                  title={`Nivel ${selectedLevel.name}`}
                  color={LEVEL_COLORS[selectedLevel.level]}
                  value={`${selectedLevel.value.toLocaleString()} registros`}
                  caption={`${pct(selectedLevel.value, rows)}% de los ${rows} registros del archivo`}
                  rows={topHostsForLevel.map(host => ({
                    label: host.host,
                    value: `${host.value} eventos`,
                  }))}
                  note={
                    riskBlock?.justification
                      ? `Justificación de este nivel: ${riskBlock.justification}`
                      : 'Hosts con más eventos clasificados en este nivel.'
                  }
                  onClose={() => setFocusLevel(null)}
                />
              )}
            </AnimatePresence>
          </>
        )}
      </DashboardSection>

      {/* Actividad temporal: hora si el backend la aporta, si no día */}
      <DashboardSection title="Actividad temporal por criticidad">
        {timeData.length === 0 ? (
          <p className="py-10 text-center text-sm text-gray-500">
            El backend no devolvió agrupaciones temporales para este análisis.
          </p>
        ) : (
          <>
            <p className="mb-3 text-sm text-gray-600">
              {hourly
                ? 'Agrupación por hora a partir de los timestamps reales del archivo; los tramos sin actividad no aparecen.'
                : 'Agrupación por día a partir de los timestamps reales del archivo.'}
            </p>

            {!hourly && timeData.length === 1 && (
              <p className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                Los datos cubren un solo día ({timeData[0].detail}) y la respuesta no incluye
                desglose horario: se muestra el total del día. Con una sola fecha no hay
                tendencia que graficar.
              </p>
            )}

            {hourly && dailyData.length === 1 && (
              <p className="mb-3 rounded border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
                Los datos cubren un solo día ({dailyData[0].detail}): la serie desglosa las
                horas con actividad de ese día.
              </p>
            )}

            <ResponsiveContainer width="100%" height={340}>
              {hourly ? (
                <AreaChart
                  data={timeData}
                  margin={{ top: 14, right: 20, left: 8, bottom: 8 }}
                  onClick={pickSlot}
                >
                  <defs>
                    {LEVEL_ORDER.map(level => (
                      <linearGradient key={level} id={`grad-${level}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={LEVEL_COLORS[level]} stopOpacity={0.75} />
                        <stop offset="100%" stopColor={LEVEL_COLORS[level]} stopOpacity={0.18} />
                      </linearGradient>
                    ))}
                  </defs>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="label" tick={AXIS_TICK} />
                  <YAxis allowDecimals={false} tick={AXIS_TICK} />
                  <Tooltip
                    cursor={LINE_CURSOR}
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(value: number, name) => [`${toCount(value).toLocaleString()} eventos`, name]}
                    labelFormatter={label => `${label} · ${totalByLabel.get(String(label)) ?? 0} eventos`}
                  />
                  <Legend />
                  {slotBandElement}
                  {LEVEL_ORDER.map((level, index) => (
                    <Area
                      key={level}
                      type="monotone"
                      dataKey={level}
                      name={LEVEL_LABELS[level]}
                      stackId="t"
                      stroke={LEVEL_COLORS[level]}
                      fill={`url(#grad-${level})`}
                      strokeWidth={2}
                      activeDot={{ r: 4 }}
                      isAnimationActive={chartMotion.isAnimationActive}
                      animationDuration={chartMotion.duration}
                      animationBegin={index * CHART_STAGGER}
                    />
                  ))}
                </AreaChart>
              ) : timeData.length === 1 ? (
                <BarChart
                  data={timeData}
                  margin={{ top: 14, right: 20, left: 8, bottom: 8 }}
                  onClick={pickSlot}
                >
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="label" tick={AXIS_TICK} />
                  <YAxis allowDecimals={false} tick={AXIS_TICK} />
                  <Tooltip
                    cursor={BAR_CURSOR}
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(value: number, name) => [`${toCount(value).toLocaleString()} eventos`, name]}
                    labelFormatter={label => `${label} · ${totalByLabel.get(String(label)) ?? 0} eventos`}
                  />
                  <Legend />
                  {slotBandElement}
                  {LEVEL_ORDER.map((level, index) => (
                    <Bar
                      key={level}
                      dataKey={level}
                      name={LEVEL_LABELS[level]}
                      stackId="daily"
                      fill={LEVEL_COLORS[level]}
                      isAnimationActive={chartMotion.isAnimationActive}
                      animationDuration={chartMotion.duration}
                      animationBegin={index * CHART_STAGGER}
                    />
                  ))}
                </BarChart>
              ) : (
                <LineChart
                  data={timeData}
                  margin={{ top: 8, right: 20, left: 8, bottom: 8 }}
                  onClick={pickSlot}
                >
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
                  <XAxis dataKey="label" tick={AXIS_TICK} />
                  <YAxis allowDecimals={false} tick={AXIS_TICK} />
                  <Tooltip
                    cursor={LINE_CURSOR}
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(value: number, name) => [`${toCount(value).toLocaleString()} eventos`, name]}
                    labelFormatter={label => `${label} · ${totalByLabel.get(String(label)) ?? 0} eventos`}
                  />
                  <Legend />
                  {slotBandElement}
                  {LEVEL_ORDER.map((level, index) => (
                    <Line
                      key={level}
                      type="monotone"
                      dataKey={level}
                      name={LEVEL_LABELS[level]}
                      stroke={LEVEL_COLORS[level]}
                      strokeWidth={2}
                      dot={{ r: 3 }}
                      activeDot={{ r: 5 }}
                      isAnimationActive={chartMotion.isAnimationActive}
                      animationDuration={chartMotion.duration}
                      animationBegin={index * CHART_STAGGER}
                    />
                  ))}
                </LineChart>
              )}
            </ResponsiveContainer>

            <p className="mt-2 text-xs text-gray-500">
              Pulsá un tramo de la serie para fijarlo y ver su desglose por criticidad.
            </p>

            <AnimatePresence mode="wait">
              {selectedSlot && (
                <ChartInsight
                  key={`slot-${selectedSlot.label}`}
                  title={`${selectedSlot.detail} · ${hourly ? 'hora' : 'día'}`}
                  color="#1e40af"
                  value={`${selectedSlot.total.toLocaleString()} eventos`}
                  caption={`${pct(selectedSlot.total, totalSeries)}% de los ${totalSeries.toLocaleString()} eventos del periodo`}
                  rows={LEVEL_ORDER.map(level => ({
                    label: LEVEL_LABELS[level],
                    value: `${selectedSlot[level].toLocaleString()} eventos`,
                  }))}
                  note="Cifras tomadas de los timestamps del archivo: los tramos sin actividad no aparecen en la serie."
                  onClose={() => setFocusSlot(null)}
                />
              )}
            </AnimatePresence>
          </>
        )}
      </DashboardSection>

      {/* Top hosts, con filtro por nivel */}
      <DashboardSection title="Hosts con mayor volumen de eventos">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <p className="text-sm text-gray-600">
            Registros por host; podés enfocar un nivel de riesgo concreto.
          </p>
          <label className="flex items-center gap-2 text-sm text-gray-700">
            Nivel
            <select
              value={hostLevel}
              onChange={e => setHostLevel(e.target.value)}
              className="rounded border border-gray-300 bg-white px-3 py-2 focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="todos">Todos</option>
              {LEVEL_ORDER.map(level => (
                <option key={level} value={level}>
                  {LEVEL_LABELS[level]}
                </option>
              ))}
            </select>
          </label>
        </div>

        {hostsData.length ? (
          <ResponsiveContainer width="100%" height={Math.max(300, hostsData.length * 46)}>
            <BarChart
              data={hostsData}
              layout="vertical"
              margin={{ top: 8, right: 44, left: 12, bottom: 8 }}
              onClick={pickHost}
            >
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" allowDecimals={false} tick={AXIS_TICK} />
              <YAxis dataKey="host" type="category" width={88} tick={AXIS_TICK} />
              <Tooltip
                cursor={BAR_CURSOR}
                contentStyle={TOOLTIP_STYLE}
                formatter={(value: number, name) => [`${toCount(value).toLocaleString()} eventos`, name]}
              />
              <Legend />
              {shownLevels.map((level, index) => (
                <Bar
                  key={level}
                  dataKey={level}
                  name={LEVEL_LABELS[level]}
                  stackId={hostLevel === 'todos' ? 'hosts' : undefined}
                  fill={LEVEL_COLORS[level]}
                  isAnimationActive={chartMotion.isAnimationActive}
                  animationDuration={chartMotion.duration}
                  animationBegin={index * CHART_STAGGER}
                  label={{ position: 'right', fontSize: 10, fill: '#475569', formatter: (value: number) => (value > 0 ? value : '') }}
                >
                  {/* Las celdas viven dentro de <Bar>: Recharts las lee como
                      hijos de la serie y aplica el marcado a cada host. */}
                  {selectionCells(hostsData, focusHost, h => h.host, '#1e40af', true)}
                </Bar>
              ))}
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-8 text-center text-sm text-gray-500">No hay hosts para mostrar.</p>
        )}

        <p className="mt-2 text-xs text-gray-500">
          Pulsá una barra para fijar el host y ver su reparto por criticidad.
        </p>

        <AnimatePresence mode="wait">
          {selectedHostData && (
            <ChartInsight
              key={`host-${selectedHostData.host}`}
              title={`Host ${selectedHostData.host}`}
              color="#1e40af"
              value={`${selectedHostData.total.toLocaleString()} eventos`}
              caption={
                totalHostEvents > 0
                  ? `${pct(selectedHostData.total, totalHostEvents)}% de los ${totalHostEvents.toLocaleString()} eventos contabilizados por host`
                  : undefined
              }
              rows={LEVEL_ORDER.map(level => ({
                label: LEVEL_LABELS[level],
                value: `${selectedHostData[level].toLocaleString()} eventos`,
              }))}
              note={
                hostLevel === 'todos'
                  ? 'Reparto calculado de los registros agrupados por host en el resumen del análisis.'
                  : `El gráfico está filtrado por «${LEVEL_LABELS[hostLevel]}»; la tarjeta muestra el reparto completo del host.`
              }
              onClose={() => setFocusHost(null)}
            />
          )}
        </AnimatePresence>
      </DashboardSection>

      {/* Original Severity column, kept separate from the derived levels */}
      <DashboardSection title="Valores originales del campo Severity">
        <p className="text-sm text-gray-600 mb-4">
          Tal como vienen en el archivo, sin convertir a Crítico / Alto / Medio / Bajo.
        </p>
        {rawSeverityData.length ? (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart
              data={rawSeverityData}
              margin={{ top: 8, right: 20, left: 8, bottom: 8 }}
              onClick={pickRaw}
            >
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="severity" tick={AXIS_TICK} />
              <YAxis allowDecimals={false} tick={AXIS_TICK} />
              <Tooltip
                cursor={BAR_CURSOR}
                contentStyle={TOOLTIP_STYLE}
                formatter={(value: number) => [`${toCount(value).toLocaleString()} registros`, 'Cantidad']}
              />
              <Bar
                dataKey="count"
                name="Registros originales"
                fill="#0891b2"
                radius={[5, 5, 0, 0]}
                isAnimationActive={chartMotion.isAnimationActive}
                animationDuration={chartMotion.duration}
                label={{ position: 'top', fontSize: 11, fill: '#475569' }}
              >
                {/* Recharts lee las celdas desde los hijos de la serie. */}
                {selectionCells(rawSeverityData, focusRaw, r => r.severity, '#0891b2')}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-8 text-center text-sm text-gray-500">
            La respuesta no incluye conteos del campo Severity original.
          </p>
        )}

        <AnimatePresence mode="wait">
          {selectedRaw && (
            <ChartInsight
              key={`raw-${selectedRaw.severity}`}
              title={`Severity «${selectedRaw.severity}»`}
              color="#0891b2"
              value={`${selectedRaw.count.toLocaleString()} registros`}
              caption={`${pct(selectedRaw.count, rows)}% de los ${rows} registros, tal como vienen en el archivo`}
              note="Este es el valor original de la columna; la clasificación por criticidad del dashboard es un cálculo aparte."
              onClose={() => setFocusRaw(null)}
            />
          )}
        </AnimatePresence>
      </DashboardSection>

      {/* Overall risk justification */}
      <DashboardSection title="Justificación del nivel de riesgo">
        <p className="text-gray-700 leading-relaxed">
          {summary.risk_justification || 'El backend no incluyó una justificación para este análisis.'}
        </p>
      </DashboardSection>
    </section>
  )
}

export default Dashboard
