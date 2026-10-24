import React from 'react'
import { useAnalysisStore } from '../store'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  LineChart,
  Line,
} from 'recharts'

const COLORS = ['#e74c3c', '#e67e22', '#f1c40f', '#2ecc71', '#3498db']

const Dashboard: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)

  if (!summary) {
    return (
      <div className="p-4 text-gray-500">
        No analysis data yet. Upload an Excel file to see the dashboard.
      </div>
    )
  }

  // Prepare data for charts
  const severityData = Object.entries(summary.severity_counts || {}).map(([name, value]) => ({ name, value }))
  const eventsData = Object.entries(summary.events_per_day || {}).map(([date, value]) => ({ date, value }))
  const topHostsData = Object.entries(summary.top_hosts || {}).map(([host, value]) => ({ host, value }))

  return (
    <section className="space-y-8">
      {/* Summary cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white rounded shadow p-4 text-center">
          <h3 className="text-lg font-medium mb-1">Total rows</h3>
          <p className="text-2xl font-bold">{summary.rows}</p>
        </div>
        <div className="bg-white rounded shadow p-4 text-center">
          <h3 className="text-lg font-medium mb-1">Unique hosts</h3>
          <p className="text-2xl font-bold">{summary.unique_hosts ?? '—'}</p>
        </div>
        <div className="bg-white rounded shadow p-4 text-center">
          <h3 className="text-lg font-medium mb-1">Risk level</h3>
          <p className="text-2xl font-bold capitalize">{summary.risk}</p>
        </div>
        <div className="bg-white rounded shadow p-4 text-center">
          <h3 className="text-lg font-medium mb-1">Columns</h3>
          <p className="text-sm text-gray-600">{summary.columns?.length ?? 0}</p>
        </div>
      </div>

      {/* Severity distribution pie */}
      <div className="bg-white rounded shadow p-4">
        <h3 className="text-xl font-semibold mb-4">Severity distribution</h3>
        <ResponsiveContainer width="100%" height={300}>
          <PieChart>
            <Pie
              data={severityData}
              dataKey="value"
              nameKey="name"
              cx="50%"
              cy="50%"
              outerRadius={100}
              label={({ name, percent }) => `${name} (${(percent * 100).toFixed(0)}%)`}
            >
              {severityData.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
      </div>

      {/* Events per day line chart */}
      <div className="bg-white rounded shadow p-4">
        <h3 className="text-xl font-semibold mb-4">Events per day</h3>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={eventsData}>
            <XAxis dataKey="date" stroke="#555" />
            <YAxis />
            <Tooltip />
            <Legend />
            <Line type="monotone" dataKey="value" name="Events" stroke="#8884d8" strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Top hosts bar chart */}
      <div className="bg-white rounded shadow p-4">
        <h3 className="text-xl font-semibold mb-4">Top 5 hosts by event count</h3>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={topHostsData} layout="vertical" margin={{ top: 20, right: 30, left: 80, bottom: 20 }}>
            <XAxis type="number" />
            <YAxis dataKey="host" type="category" />
            <Tooltip />
            <Bar dataKey="value" fill="#82ca9d" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

export default Dashboard
