import React from 'react'
import { motion } from 'framer-motion'
import { SummaryCard } from './SummaryCard'
import { DashboardSection } from './DashboardSection'
import { EmptyState } from './EmptyState'
import { useAnalysisStore } from '../store'
import { stagger, cardIn, useCountUp } from '../motion'
import {
  Tooltip,
  Legend,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
} from 'recharts'

// Criticality palette (fixed order: critico → bajo)
const LEVEL_COLORS: Record<string, string> = {
  critico: '#dc2626',
  alto: '#f97316',
  medio: '#facc15',
  bajo: '#10b981',
}
const LEVEL_ORDER = ['critico', 'alto', 'medio', 'bajo']
const LEVEL_LABELS: Record<string, string> = {
  critico: 'Crítico',
  alto: 'Alto',
  medio: 'Medio',
  bajo: 'Bajo',
}

/** Contador animado para las cifras de las tarjetas de criticidad. */
const Counter: React.FC<{ value: number }> = ({ value }) => <>{useCountUp(value)}</>

const Dashboard: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)

  if (!summary) {
    return (
      <EmptyState hint="Sube un archivo Excel para ver las estadísticas y los gráficos por criticidad." />
    )
  }

  const levelCounts = summary.level_counts ?? {}

  // Data for charts
  const severityData = LEVEL_ORDER
    .map(level => ({ name: LEVEL_LABELS[level], value: levelCounts[level] ?? 0, level }))
    .filter(d => d.value > 0)

  const eventsData = Object.entries(summary.events_per_day_by_level ?? {}).map(
    ([date, levels]) => ({
      date,
      critico: levels.critico ?? 0,
      alto: levels.alto ?? 0,
      medio: levels.medio ?? 0,
      bajo: levels.bajo ?? 0,
    })
  )

  const hostsData = Object.entries(summary.top_hosts_by_level ?? {}).map(
    ([host, levels]) => ({
      host,
      critico: levels.critico ?? 0,
      alto: levels.alto ?? 0,
      medio: levels.medio ?? 0,
      bajo: levels.bajo ?? 0,
    })
  )

  return (
    <section className="space-y-8">
      {/* Key metrics */}
      <motion.div
        variants={stagger(0.06)}
        initial="hidden"
        animate="show"
        className="grid grid-cols-2 lg:grid-cols-5 gap-4"
      >
        <SummaryCard label="Registros totales" value={summary.rows} />
        <SummaryCard label="Hosts únicos" value={summary.unique_hosts ?? '—'} />
        <SummaryCard label="Nivel de riesgo" value={<span className="capitalize">{summary.risk}</span>} />
        <SummaryCard label="Eventos sospechosos" value={summary.suspicious_events ?? 0} />
        <SummaryCard label="Columnas" value={summary.columns?.length ?? 0} />
      </motion.div>

      {/* Criticality breakdown */}
      <motion.div
        variants={stagger(0.06)}
        initial="hidden"
        animate="show"
        className="grid grid-cols-2 lg:grid-cols-4 gap-4"
      >
        {LEVEL_ORDER.map(level => {
          const count = levelCounts[level] ?? 0
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
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: color }}
                />
              </div>
              <p className="text-3xl font-bold mt-1" style={{ color }}>
                <Counter value={count} />
              </p>
              <p className="text-xs text-gray-500 mt-1">
                {summary.rows ? ((count / summary.rows) * 100).toFixed(1) : 0}% del total
              </p>
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

      {/* Overall risk justification */}
      <DashboardSection title="Justificación del nivel de riesgo">
        <p className="text-gray-700 leading-relaxed">{summary.risk_justification}</p>
      </DashboardSection>

      {/* Criticality distribution pie */}
      <DashboardSection title="Distribución por criticidad">
        <ResponsiveContainer width="100%" height={300}>
          <PieChart>
            <Pie
              data={severityData}
              dataKey="value"
              nameKey="name"
              cx="50%"
              cy="50%"
              outerRadius={110}
              label={({ name, percent }) => `${name} (${(percent * 100).toFixed(1)}%)`}
            >
              {severityData.map(entry => (
                <Cell key={entry.level} fill={LEVEL_COLORS[entry.level]} />
              ))}
            </Pie>
            <Tooltip />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
      </DashboardSection>

      {/* Events per day stacked by criticality */}
      <DashboardSection title="Eventos por día (segmentados por criticidad)">
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={eventsData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date" stroke="#555" />
            <YAxis />
            <Tooltip />
            <Legend />
            {LEVEL_ORDER.map(level => (
              <Line
                key={level}
                type="monotone"
                dataKey={level}
                name={LEVEL_LABELS[level]}
                stroke={LEVEL_COLORS[level]}
                strokeWidth={2}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </DashboardSection>

      {/* Top hosts stacked by criticality */}
      <DashboardSection title="Top hosts por volumen de eventos">
        <ResponsiveContainer width="100%" height={320}>
          <BarChart data={hostsData} layout="vertical" margin={{ top: 10, right: 30, left: 80, bottom: 10 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis type="number" />
            <YAxis dataKey="host" type="category" width={70} />
            <Tooltip />
            <Legend />
            {LEVEL_ORDER.map(level => (
              <Bar
                key={level}
                dataKey={level}
                name={LEVEL_LABELS[level]}
                stackId="hosts"
                fill={LEVEL_COLORS[level]}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </DashboardSection>
    </section>
  )
}

export default Dashboard
