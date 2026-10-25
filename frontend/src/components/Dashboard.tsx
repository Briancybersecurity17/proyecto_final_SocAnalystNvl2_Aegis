import React, { useState } from 'react'
import { motion } from 'framer-motion'
import { SummaryCard } from './SummaryCard'
import { DashboardSection } from './DashboardSection'
import { EmptyState } from './EmptyState'
import { useAnalysisStore } from '../store'
import { stagger, cardIn, useCountUp } from '../motion'
import {
  LEVEL_COLORS,
  LEVEL_ORDER,
  LEVEL_LABELS,
  AXIS_TICK,
  GRID,
  TOOLTIP_STYLE,
  toCount,
  pct,
  useChartMotion,
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

const Dashboard: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)
  const [hostLevel, setHostLevel] = useState<string>('todos')
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
        <SummaryCard label="Riesgo global" value={<span className="capitalize">{summary.risk || '—'}</span>} />
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
        </p>
        {severityData.every(item => item.value === 0) ? (
          <p className="py-10 text-center text-sm text-gray-500">
            No hay datos de clasificación para representar.
          </p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_240px] gap-4 items-center">
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={severityData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  innerRadius={62}
                  outerRadius={100}
                  paddingAngle={2}
                  isAnimationActive={chartMotion.isAnimationActive}
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
                solapaban sobre los sectores pequeños del donut */}
            <div className="space-y-3">
              {severityData.map(item => (
                <div key={item.level} className="flex items-center justify-between gap-3 border-b border-gray-100 pb-2">
                  <span className="flex items-center gap-2 text-sm text-gray-700">
                    <span className="inline-block h-3 w-3 rounded-sm" style={{ background: LEVEL_COLORS[item.level] }} />
                    {item.name}
                  </span>
                  <span className="text-right">
                    <strong className="text-sm text-gray-900">{item.value.toLocaleString()}</strong>
                    <span className="block text-xs text-gray-500">{pct(item.value, rows)}%</span>
                  </span>
                </div>
              ))}
            </div>
          </div>
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

            <ResponsiveContainer width="100%" height={300}>
              {hourly ? (
                <AreaChart data={timeData} margin={{ top: 14, right: 20, left: 8, bottom: 8 }}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="label" tick={AXIS_TICK} />
                  <YAxis allowDecimals={false} tick={AXIS_TICK} />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(value: number, name) => [`${toCount(value).toLocaleString()} eventos`, name]}
                    labelFormatter={label => `${label} · ${totalByLabel.get(String(label)) ?? 0} eventos`}
                  />
                  <Legend />
                  {LEVEL_ORDER.map(level => (
                    <Area
                      key={level}
                      type="monotone"
                      dataKey={level}
                      name={LEVEL_LABELS[level]}
                      stackId="t"
                      stroke={LEVEL_COLORS[level]}
                      fill={LEVEL_COLORS[level]}
                      fillOpacity={0.7}
                      strokeWidth={2}
                      activeDot={{ r: 4 }}
                      isAnimationActive={chartMotion.isAnimationActive}
                    />
                  ))}
                </AreaChart>
              ) : timeData.length === 1 ? (
                <BarChart data={timeData} margin={{ top: 14, right: 20, left: 8, bottom: 8 }}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="label" tick={AXIS_TICK} />
                  <YAxis allowDecimals={false} tick={AXIS_TICK} />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(value: number, name) => [`${toCount(value).toLocaleString()} eventos`, name]}
                    labelFormatter={label => `${label} · ${totalByLabel.get(String(label)) ?? 0} eventos`}
                  />
                  <Legend />
                  {LEVEL_ORDER.map(level => (
                    <Bar key={level} dataKey={level} name={LEVEL_LABELS[level]} stackId="daily" fill={LEVEL_COLORS[level]} isAnimationActive={chartMotion.isAnimationActive} />
                  ))}
                </BarChart>
              ) : (
                <LineChart data={timeData} margin={{ top: 8, right: 20, left: 8, bottom: 8 }}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
                  <XAxis dataKey="label" tick={AXIS_TICK} />
                  <YAxis allowDecimals={false} tick={AXIS_TICK} />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(value: number, name) => [`${toCount(value).toLocaleString()} eventos`, name]}
                    labelFormatter={label => `${label} · ${totalByLabel.get(String(label)) ?? 0} eventos`}
                  />
                  <Legend />
                  {LEVEL_ORDER.map(level => (
                    <Line key={level} type="monotone" dataKey={level} name={LEVEL_LABELS[level]} stroke={LEVEL_COLORS[level]} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} isAnimationActive={chartMotion.isAnimationActive} />
                  ))}
                </LineChart>
              )}
            </ResponsiveContainer>
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
          <ResponsiveContainer width="100%" height={Math.max(280, hostsData.length * 42)}>
            <BarChart data={hostsData} layout="vertical" margin={{ top: 8, right: 44, left: 12, bottom: 8 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" allowDecimals={false} tick={AXIS_TICK} />
              <YAxis dataKey="host" type="category" width={88} tick={AXIS_TICK} />
              <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(value: number, name) => [`${toCount(value).toLocaleString()} eventos`, name]} />
              <Legend />
              {shownLevels.map(level => (
                <Bar
                  key={level}
                  dataKey={level}
                  name={LEVEL_LABELS[level]}
                  stackId={hostLevel === 'todos' ? 'hosts' : undefined}
                  fill={LEVEL_COLORS[level]}
                  isAnimationActive={chartMotion.isAnimationActive}
                  label={{ position: 'right', fontSize: 10, fill: '#475569', formatter: (value: number) => (value > 0 ? value : '') }}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-8 text-center text-sm text-gray-500">No hay hosts para mostrar.</p>
        )}
      </DashboardSection>

      {/* Original Severity column, kept separate from the derived levels */}
      <DashboardSection title="Valores originales del campo Severity">
        <p className="text-sm text-gray-600 mb-4">
          Tal como vienen en el archivo, sin convertir a Crítico / Alto / Medio / Bajo.
        </p>
        {rawSeverityData.length ? (
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={rawSeverityData} margin={{ top: 8, right: 20, left: 8, bottom: 8 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="severity" tick={AXIS_TICK} />
              <YAxis allowDecimals={false} tick={AXIS_TICK} />
              <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(value: number) => [`${toCount(value).toLocaleString()} registros`, 'Cantidad']} />
              <Bar
                dataKey="count"
                name="Registros originales"
                fill="#0891b2"
                radius={[5, 5, 0, 0]}
                isAnimationActive={chartMotion.isAnimationActive}
                label={{ position: 'top', fontSize: 11, fill: '#475569' }}
              />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-8 text-center text-sm text-gray-500">
            La respuesta no incluye conteos del campo Severity original.
          </p>
        )}
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
