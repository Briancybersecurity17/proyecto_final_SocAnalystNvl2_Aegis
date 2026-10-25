import React from 'react'
import { SummaryCard } from './SummaryCard'
import { DashboardSection } from './DashboardSection'
import { useAnalysisStore } from '../store'
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

const Dashboard: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)

  if (!summary) {
    return (
      <div className="p-8 text-center text-gray-500 bg-white rounded shadow">
        <p className="text-lg font-medium mb-2">Sin datos todavía</p>
        <p>Sube un archivo Excel en la sección <strong>Carga de archivos</strong> para ver las estadísticas.</p>
      </div>
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
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <SummaryCard label="Registros totales" value={summary.rows} />
        <SummaryCard label="Hosts únicos" value={summary.unique_hosts ?? '—'} />
        <SummaryCard label="Nivel de riesgo" value={<span className="capitalize">{summary.risk}</span>} />
        <SummaryCard label="Eventos sospechosos" value={summary.suspicious_events ?? 0} />
        <SummaryCard label="Columnas" value={summary.columns?.length ?? 0} />
      </div>

      {/* Criticality breakdown */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {LEVEL_ORDER.map(level => {
          const count = levelCounts[level] ?? 0
          const color = LEVEL_COLORS[level]
          return (
            <div
              key={level}
              className="bg-white rounded shadow p-4 border-l-4"
              style={{ borderLeftColor: color }}
            >
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-600">{LEVEL_LABELS[level]}</h3>
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: color }}
                />
              </div>
              <p className="text-3xl font-bold mt-1" style={{ color }}>{count}</p>
              <p className="text-xs text-gray-500 mt-1">
                {summary.rows ? ((count / summary.rows) * 100).toFixed(1) : 0}% del total
              </p>
            </div>
          )
        })}
      </div>

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
