import React from 'react'
import { SummaryCard } from './SummaryCard';
import { DashboardSection } from './DashboardSection';

import { useAnalysisStore } from '../store';
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
        <SummaryCard label="Total rows" value={summary.rows} />
        <SummaryCard label="Unique hosts" value={summary.unique_hosts ?? '—'} />
        <SummaryCard label="Risk level" value={<span className="capitalize">{summary.risk}</span>} />
        <SummaryCard label="Columns" value={summary.columns?.length ?? 0} />
      </div>

      {/* Severity distribution pie */}
      <DashboardSection title="Severity distribution">
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
      </DashboardSection>

      {/* Events per day line chart */}
      <DashboardSection title="Events per day">
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={eventsData}>
            <XAxis dataKey="date" stroke="#555" />
            <YAxis />
            <Tooltip />
            <Legend />
            <Line type="monotone" dataKey="value" name="Events" stroke="#8884d8" strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </DashboardSection>

      {/* Top hosts bar chart */}
      <DashboardSection title="Top 5 hosts by event count">
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={topHostsData} layout="vertical" margin={{ top: 20, right: 30, left: 80, bottom: 20 }}>
            <XAxis type="number" />
            <YAxis dataKey="host" type="category" />
            <Tooltip />
            <Bar dataKey="value" fill="#82ca9d" />
          </BarChart>
        </ResponsiveContainer>
      </DashboardSection>
    </section>
  )
}

export default Dashboard
