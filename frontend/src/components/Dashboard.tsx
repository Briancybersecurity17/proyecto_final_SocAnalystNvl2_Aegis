import React, { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { SummaryCard } from './SummaryCard'
import { DashboardSection } from './DashboardSection'
import DataTable from './DataTable'
import { EmptyState } from './EmptyState'
import { TimelineBrush } from './TimelineBrush'
import { ChartInsight, selectionCells } from './ChartTools'
import { useScopedSummary, type OmitSpec } from '../hooks/useScopedSummary'
import { selectIsFiltered, useFilterStore } from '../store/filters'
import { stagger, cardIn, useCountUp } from '../motion'
import {
  LEVEL_COLORS,
  LEVEL_ORDER,
  LEVEL_LABELS,
  AXIS_TICK,
  GRID,
  TOOLTIP_STYLE,
  TOOLTIP_ITEM_STYLE,
  BAR_CURSOR,
  LINE_CURSOR,
  CHART_STAGGER,
  glowVars,
  toCount,
  pct,
  fmtCount,
  useChartMotion,
  ChartClickState,
  TimePoint,
  dayMonth,
  buildTimeBuckets,
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
const Counter: React.FC<{ value: number }> = ({ value }) => <>{fmtCount(useCountUp(value))}</>

/** Un filtro activo de la barra de chips. */
type ActiveFilterChip = {
  key: string
  label: string
  onRemove: () => void
}

/** Fila que entrega Recharts al pulsar los gráficos del dashboard. */
type DashboardClick = ChartClickState<TimePoint & { host?: string; severity?: string }>

// Qué filtra ignora cada vista (facet-exclusion, spec sub-3 D2/D3).
// Constantes de módulo: la identidad del `omit` no cambia entre renders.
const OMIT_LEVEL: OmitSpec = { level: true }
const OMIT_HOSTS: OmitSpec = { facets: ['Hostname'] }

const Dashboard: React.FC = () => {
  // Tres vistas, tres resúmenes: el global sin filtros (cero peticiones) o el
  // acotado por el motor de filtros. KPIs, tabla, IA, actividad, justificación
  // y chips leen el completo; el donut y las tarjetas ignoran `level` para
  // seguir viendo los 4 niveles aunque ese filtro esté activo, y las barras
  // de hosts ignoran la faceta «Hostname» para no colapsar el gráfico.
  const { summary, total, filteredTotal, loading, error } = useScopedSummary()
  const { summary: summaryNoLevel } = useScopedSummary(OMIT_LEVEL)
  const { summary: summaryNoHosts } = useScopedSummary(OMIT_HOSTS)
  // El store se lee completo porque la barra de chips necesita todos los
  // filtros activos y sus acciones para poder quitar cada uno.
  const filters = useFilterStore()
  const isFiltered = selectIsFiltered(filters)
  const chartMotion = useChartMotion()

  // Selección de la tira temporal: el tramo marcado (brillo + contorno) del
  // que la tarjeta de debajo muestra el desglose completo. El nivel y el host
  // ya no guardan estado local: alternan los filtros globales del store (D7).
  const [focusSlot, setFocusSlot] = useState<string | null>(null)
  // Nivel activo: lo alternan las tarjetas, los sectores y la leyenda.
  const activeLevel = filters.level || null
  const toggleLevel = (nivel: string) => filters.setLevel(filters.level === nivel ? '' : nivel)
  // Hosts activos: las barras alternan la faceta global «Hostname». Con una
  // sola selección se resalta la barra y se abre el desglose; con varias, la
  // selección vive en los chips y el resaltado se apaga (simplificación
  // aceptada).
  const activeHosts = filters.facets['Hostname'] ?? []
  const focusHost = activeHosts.length === 1 ? activeHosts[0] : null

  if (!summary || !summaryNoLevel || !summaryNoHosts) {
    return (
      <EmptyState hint="Sube un archivo Excel para ver las estadísticas y los gráficos por criticidad." />
    )
  }

  // --- barra de filtros activos ------------------------------------------
  // Un chip por filtro; las facetas se abren en un chip por valor para poder
  // quitar un host concreto sin borrar el resto de la selección del campo.
  const activeChips: ActiveFilterChip[] = []
  Object.entries(filters.facets).forEach(([field, values]) => {
    values.forEach(value => {
      activeChips.push({
        key: `facet:${field}:${value}`,
        label: `${field}: ${value}`,
        onRemove: () => filters.toggleFacet(field, value),
      })
    })
  })
  if (filters.from || filters.to) {
    const from = filters.from ? dayMonth(filters.from) : null
    const to = filters.to ? dayMonth(filters.to) : null
    let label = ''
    if (from && to) label = `${from}\u2013${to}`
    else if (from) label = `desde ${from}`
    else if (to) label = `hasta ${to}`
    activeChips.push({ key: 'range', label, onRemove: () => filters.setRange(null, null) })
  }
  if (filters.level) {
    activeChips.push({
      key: 'level',
      label: `Nivel: ${LEVEL_LABELS[filters.level] ?? filters.level}`,
      onRemove: () => filters.setLevel(''),
    })
  }
  if (filters.suspicious) {
    activeChips.push({
      key: 'suspicious',
      label: 'Solo sospechosos',
      onRemove: () => filters.setSuspicious(false),
    })
  }
  if (filters.result) {
    activeChips.push({
      key: 'result',
      label: `Resultado: ${filters.result}`,
      onRemove: () => filters.setResult(''),
    })
  }
  if (filters.q) {
    activeChips.push({
      key: 'q',
      label: `Búsqueda: \u201c${filters.q}\u201d`,
      onRemove: () => filters.setQ(''),
    })
  }

  // Sin coincidencias: solo ceden los 4 gráficos del bloque inferior. La
  // cabecera, los KPIs, las tarjetas, los chips, la IA, la tira y la tabla
  // siguen a la vista: son los controles con los que se sale del callejón.
  // Un `error` de red nunca cae aquí: el hook vuelve al global.
  const noMatches = isFiltered && filteredTotal === 0 && !loading && !error

  const rows = toCount(summary.rows)
  const suspicious = toCount(summary.suspicious_events)
  // Tarjetas y donut: ignoran el filtro de nivel, así que muestran SIEMPRE
  // los 4 niveles y sus porcentajes dividen sobre los registros sin ese filtro.
  const rowsNoLevel = toCount(summaryNoLevel.rows)
  const levelCounts = summaryNoLevel.level_counts ?? {}

  // Distribución por criticidad (clasificación derivada del análisis)
  const severityData = LEVEL_ORDER.map(level => ({
    name: LEVEL_LABELS[level],
    value: toCount(levelCounts[level]),
    level,
  }))

  const { hourlyData, dailyData, hourly, hourlyTooLong, points: timeData } =
    buildTimeBuckets(summary)
  const totalByLabel = new Map(timeData.map(p => [p.label, p.total]))

  // Barras de hosts: ignoran la faceta «Hostname» para seguir mostrando
  // todos los hosts aunque ese filtro esté activo.
  const hostsData = Object.entries(summaryNoHosts.top_hosts_by_level ?? {})
    .map(([host, levels]) => ({
      host,
      critico: toCount(levels.critico),
      alto: toCount(levels.alto),
      medio: toCount(levels.medio),
      bajo: toCount(levels.bajo),
    }))
    .map(host => ({
      ...host,
      total: host.critico + host.alto + host.medio + host.bajo,
    }))
    .sort((a, b) => b.total - a.total)

  // --- selección activa (lo que queda marcado en cada gráfico) -------------
  const selectedIndex = activeLevel ? severityData.findIndex(item => item.level === activeLevel) : -1
  const selectedLevel = selectedIndex >= 0 ? severityData[selectedIndex] : null
  // La justificación del riesgo es metadato del análisis: no se acota.
  const riskBlock = activeLevel ? summary.risk_levels?.[activeLevel] : undefined

  // Hosts con más eventos en el nivel seleccionado: salen del resumen que
  // ignora el filtro de nivel, para no colapsar el desglose con él activo.
  const topHostsForLevel = activeLevel
    ? Object.entries(summaryNoLevel.top_hosts_by_level ?? {})
        .map(([host, levels]) => ({ host, value: toCount(levels[activeLevel]) }))
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
      stroke="#3b82f6"
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

  /** Clic sobre una barra de host: alterna ese host en la faceta global. */
  const pickHost = (state: DashboardClick): void => {
    const host = state.activePayload?.[0]?.payload?.host
    if (typeof host !== 'string') return
    filters.toggleFacet('Hostname', host)
  }

  const totalHostEvents = hostsData.reduce((sum, host) => sum + host.total, 0)

  return (
    <section
      className={`space-y-8 transition-opacity duration-200 ${loading ? 'opacity-[0.45]' : ''}`}
    >
      {/* Archivo analizado y momento del resumen */}
      <div className="hud-label flex flex-wrap items-center justify-between gap-2">
        <span>
          Conjunto analizado:{' '}
          <strong className="text-hud-fg">{summary.file_name || 'archivo importado'}</strong>
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
          // Clic (o Enter/Espacio) sobre la tarjeta: alterna el filtro global.
          const active = filters.level === level
          return (
            <motion.div
              key={level}
              variants={cardIn}
              whileHover={{ y: -3, transition: { duration: 0.18 } }}
              onClick={() => toggleLevel(level)}
              onKeyDown={event => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  toggleLevel(level)
                }
              }}
              role="button"
              aria-pressed={active}
              tabIndex={0}
              className={`cursor-pointer border border-hud-soft bg-inset/80 p-4 shadow-[0_0_12px_rgba(31,200,255,0.14)] transition-shadow duration-200 hover:border-hud-line/50 hover:shadow-[0_0_20px_rgba(31,200,255,0.35)] [clip-path:var(--chamfer-sm)]${active ? ' border-hud-line/70 shadow-[0_0_18px_rgba(31,200,255,0.45)]' : ''}`}
              style={{ borderLeft: `4px solid ${color}` }}
            >
              <div className="flex items-center justify-between">
                <h3 className="hud-label">{LEVEL_LABELS[level]}</h3>
                <span
                  className="h-3 w-3 rounded-full"
                  style={{ backgroundColor: color, boxShadow: `0 0 8px ${color}` }}
                />
              </div>
              <p className="mt-1 font-display text-3xl font-bold" style={{ color }}>
                <Counter value={count} />
              </p>
              <p className="mt-1 font-mono text-[12px] text-[var(--text-dim)]">
                {pct(count, rowsNoLevel)}% de {rowsNoLevel} registros
              </p>
            </motion.div>
          )
        })}
      </motion.div>

      {/* Filtros activos (spec §5.3): chips removibles, contador y limpieza
          global. Sin esta barra el usuario no sabría que las cifras están
          acotadas por un filtro. */}
      {isFiltered && (
        <div role="group" aria-label="Filtros activos" className="flex flex-wrap items-center gap-2">
          {activeChips.map(chip => (
            <span key={chip.key} className="chip normal-case text-hud-line">
              {chip.label}
              <button
                type="button"
                onClick={chip.onRemove}
                aria-label={`Quitar filtro ${chip.label}`}
                title={`Quitar filtro ${chip.label}`}
                className="ml-1 leading-none opacity-70 transition-opacity hover:opacity-100"
              >
                ×
              </button>
            </span>
          ))}

          <span className="hud-label">
            {fmtCount(toCount(filteredTotal))} de {fmtCount(toCount(total))} registros
          </span>

          <button type="button" onClick={filters.clearAll} className="btn-hud">
            Limpiar filtros
          </button>

          {error && (
            <span className="hud-label basis-full text-[#f97316]">
              No se pudo aplicar el filtro — se muestra el resumen global
            </span>
          )}
        </div>
      )}

      {/* AI observations over the imported data */}
      {summary.ai_insights && summary.ai_insights.length > 0 && (
        <DashboardSection title="Observaciones del análisis asistido por IA" collapsible>
          <div className="mb-4 flex flex-wrap gap-2">
            <span className="chip whitespace-normal normal-case text-hud-line">
              <span className="h-1.5 w-1.5 rounded-full bg-hud-line" />
              Redactadas solo con cifras calculadas de tus registros
            </span>
            <span className="chip whitespace-normal normal-case text-[var(--text-dim)]">
              {summary.generated_at ? `Generado ${summary.generated_at.replace('T', ' ')}` : ''}
            </span>
          </div>
          <ul className="space-y-3">
            {summary.ai_insights.map((text, i) => (
              <li key={i} className="flex gap-3 font-mono text-[13px] leading-relaxed text-[var(--text)]">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center bg-primary text-xs font-bold text-white">
                  {i + 1}
                </span>
                <p>{text}</p>
              </li>
            ))}
          </ul>
        </DashboardSection>
      )}

      {/* Tira temporal: histograma global (control) sobre la serie */}
      <TimelineBrush />

      {/* Registros importados: la tabla vive en el Dashboard desde el Task 5
          (antes estaba en «Carga de archivos»: misma pieza, otro sitio). */}
      <DataTable />

      {/* Sin coincidencias: solo ceden los 4 gráficos. Cabecera, KPIs, tarjetas,
          chips, IA, tira y tabla siguen a la vista: son los controles con los que
          se sale del callejón (spec sub-3 D12). */}
      {noMatches ? (
        <EmptyState hint="Ningún registro coincide con los filtros activos" cta={false} />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            {/* Criticality distribution: donut + list (labels never overlap) */}
            <DashboardSection title="Distribución por criticidad">
              <p className="mb-4 font-mono text-[12.5px] leading-relaxed text-[var(--text-dim)]">
                Clasificación calculada por el análisis; no equivale al campo original{' '}
                <code className="border border-hud-soft bg-[var(--bg-void)] px-1">Severity</code>.
                <span className="ml-2 text-hud-fg">Pulsá un nivel —o su fila— para filtrar toda la pantalla por él.</span>
              </p>
              {severityData.every(item => item.value === 0) ? (
                <p className="py-10 text-center font-mono text-[13px] text-[var(--text-dim)]">
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
                            activeLevel && selectedLevel ? (
                              <Sector
                                className="chart-glow"
                                style={glowVars(LEVEL_COLORS[selectedLevel.level])}
                                stroke="#050212"
                                strokeWidth={2.5}
                              />
                            ) : undefined
                          }
                          inactiveShape={activeLevel ? { fillOpacity: 0.32 } : undefined}
                          onClick={(entry: { level?: string }) =>
                            entry?.level && toggleLevel(entry.level)
                          }
                          isAnimationActive={chartMotion.isAnimationActive}
                          animationDuration={chartMotion.duration}
                        >
                          {severityData.map(entry => (
                            <Cell
                              key={entry.level}
                              fill={LEVEL_COLORS[entry.level]}
                              stroke="#050212"
                              strokeWidth={2}
                            />
                          ))}
                        </Pie>
                        <Tooltip
                          contentStyle={TOOLTIP_STYLE}
                          itemStyle={TOOLTIP_ITEM_STYLE}
                          formatter={(value: number, _name, item) => [
                            `${fmtCount(value)} registros (${pct(toCount(value), rowsNoLevel)}%)`,
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
                        const active = activeLevel === item.level
                        const dimmed = activeLevel !== null && !active
                        return (
                          <button
                            key={item.level}
                            type="button"
                            aria-pressed={active}
                            onClick={() => toggleLevel(item.level)}
                            className={`flex items-center justify-between gap-3 border-b border-hud-soft pb-2 text-left transition-all duration-200 hover:opacity-100 ${
                              dimmed ? 'opacity-50' : 'opacity-100'
                            }`}
                          >
                            <span className="flex items-center gap-2 font-mono text-[13px] text-[var(--text)]">
                              <span
                                className="inline-block h-3 w-3"
                                style={{
                                  background: LEVEL_COLORS[item.level],
                                  boxShadow: active ? `0 0 8px ${LEVEL_COLORS[item.level]}` : undefined,
                                }}
                              />
                              {item.name}
                            </span>
                            <span className="text-right">
                              <strong className="font-mono text-[13px] text-[var(--text-bright)]">
                                {fmtCount(item.value)}
                              </strong>
                              <span className="block font-mono text-[12px] text-[var(--text-dim)]">
                                {pct(item.value, rowsNoLevel)}%
                              </span>
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
                        value={`${fmtCount(selectedLevel.value)} registros`}
                        caption={`${pct(selectedLevel.value, rowsNoLevel)}% de los ${rowsNoLevel} registros del archivo`}
                        rows={topHostsForLevel.map(host => ({
                          label: host.host,
                          value: `${host.value} eventos`,
                        }))}
                        note={
                          riskBlock?.justification
                            ? `Justificación de este nivel: ${riskBlock.justification}`
                            : 'Hosts con más eventos clasificados en este nivel.'
                        }
                        onClose={() => filters.setLevel('')}
                      />
                    )}
                  </AnimatePresence>
                </>
              )}
            </DashboardSection>

            {/* Top hosts con su reparto por criticidad */}
            <DashboardSection title="Hosts con mayor volumen de eventos">
              <p className="mb-3 font-mono text-[12.5px] text-[var(--text-dim)]">
                Registros por host, repartidos por criticidad.
              </p>

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
                      formatter={(value: number, name) => [`${fmtCount(value)} eventos`, name]}
                    />
                    <Legend />
                    {LEVEL_ORDER.map((level, index) => (
                      <Bar
                        key={level}
                        dataKey={level}
                        name={LEVEL_LABELS[level]}
                        stackId="hosts"
                        fill={LEVEL_COLORS[level]}
                        isAnimationActive={chartMotion.isAnimationActive}
                        animationDuration={chartMotion.duration}
                        animationBegin={index * CHART_STAGGER}
                        label={{ position: 'right', fontSize: 10, fill: '#bfefff', formatter: (value: number) => (value > 0 ? value : '') }}
                      >
                        {/* Las celdas viven dentro de <Bar>: Recharts las lee como
                            hijos de la serie y aplica el marcado a cada host. */}
                        {selectionCells(hostsData, focusHost, h => h.host, '#3b82f6', true, LEVEL_COLORS[level])}
                      </Bar>
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <p className="py-8 text-center font-mono text-[13px] text-[var(--text-dim)]">
                  No hay hosts para mostrar.
                </p>
              )}

              <p className="mt-2 font-mono text-[12px] text-[var(--text-dim)]">
                Pulsá una barra para filtrar por ese host; volvé a pulsar para liberarlo.
              </p>

              <AnimatePresence mode="wait">
                {selectedHostData && (
                  <ChartInsight
                    key={`host-${selectedHostData.host}`}
                    title={`Host ${selectedHostData.host}`}
                    color="#3b82f6"
                    value={`${fmtCount(selectedHostData.total)} eventos`}
                    caption={
                      totalHostEvents > 0
                        ? `${pct(selectedHostData.total, totalHostEvents)}% de los ${fmtCount(totalHostEvents)} eventos contabilizados por host`
                        : undefined
                    }
                    rows={LEVEL_ORDER.map(level => ({
                      label: LEVEL_LABELS[level],
                      value: `${fmtCount(selectedHostData[level])} eventos`,
                    }))}
                    note="Reparto calculado de los registros agrupados por host en el resumen del análisis."
                    onClose={() => activeHosts.forEach(h => filters.toggleFacet('Hostname', h))}
                  />
                )}
              </AnimatePresence>
            </DashboardSection>
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            {/* Actividad temporal: hora si el backend la aporta, si no día */}
            <DashboardSection title="Actividad temporal por criticidad">
              {timeData.length === 0 ? (
                <p className="py-10 text-center font-mono text-[13px] text-[var(--text-dim)]">
                  El backend no devolvió agrupaciones temporales para este análisis.
                </p>
              ) : (
                <>
                  <p className="mb-3 font-mono text-[12.5px] leading-relaxed text-[var(--text-dim)]">
                    {hourly
                      ? 'Agrupación por hora a partir de los timestamps reales del archivo; los tramos sin actividad no aparecen.'
                      : 'Agrupación por día a partir de los timestamps reales del archivo.'}
                  </p>

                  {hourlyTooLong && (
                    <p className="mb-3 border border-hud-line/50 bg-hud-line/10 px-3 py-2 font-mono text-[12.5px] leading-relaxed text-hud-fg">
                      El archivo abarca {fmtCount(hourlyData.length)} tramos con actividad por hora: para
                      que el eje siga siendo legible y el gráfico no pinte miles de puntos, se muestra la
                      serie diaria, con los mismos registros del archivo.
                    </p>
                  )}

                  {!hourly && timeData.length === 1 && (
                    <p className="mb-3 border border-[#ffb020]/50 bg-[#ffb020]/10 px-3 py-2 font-mono text-[12.5px] leading-relaxed text-[#ffb020]">
                      Los datos cubren un solo día ({timeData[0].detail}) y la respuesta no incluye
                      desglose horario: se muestra el total del día. Con una sola fecha no hay
                      tendencia que graficar.
                    </p>
                  )}

                  {hourly && dailyData.length === 1 && (
                    <p className="mb-3 border border-hud-line/50 bg-hud-line/10 px-3 py-2 font-mono text-[12.5px] leading-relaxed text-hud-fg">
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
                          formatter={(value: number, name) => [`${fmtCount(value)} eventos`, name]}
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
                          formatter={(value: number, name) => [`${fmtCount(value)} eventos`, name]}
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
                          formatter={(value: number, name) => [`${fmtCount(value)} eventos`, name]}
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

                  <p className="mt-2 font-mono text-[12px] text-[var(--text-dim)]">
                    Pulsá un tramo de la serie para fijarlo y ver su desglose por criticidad.
                  </p>

                  <AnimatePresence mode="wait">
                    {selectedSlot && (
                      <ChartInsight
                        key={`slot-${selectedSlot.label}`}
                        title={`${selectedSlot.detail} · ${hourly ? 'hora' : 'día'}`}
                        color="#3b82f6"
                        value={`${fmtCount(selectedSlot.total)} eventos`}
                        caption={`${pct(selectedSlot.total, totalSeries)}% de los ${fmtCount(totalSeries)} eventos del periodo`}
                        rows={LEVEL_ORDER.map(level => ({
                          label: LEVEL_LABELS[level],
                          value: `${fmtCount(selectedSlot[level])} eventos`,
                        }))}
                        note="Cifras tomadas de los timestamps del archivo: los tramos sin actividad no aparecen en la serie."
                        onClose={() => setFocusSlot(null)}
                      />
                    )}
                  </AnimatePresence>
                </>
              )}
            </DashboardSection>

            {/* Overall risk justification */}
            <DashboardSection title="Justificación del nivel de riesgo">
              <p className="font-mono text-[13.5px] leading-relaxed text-[var(--text)]">
                {summary.risk_justification || 'El backend no incluyó una justificación para este análisis.'}
              </p>
            </DashboardSection>
          </div>
        </>
      )}
    </section>
  )
}

export default Dashboard
