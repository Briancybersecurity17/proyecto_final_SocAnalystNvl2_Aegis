import React from 'react'
import { useAnalysisStore } from '../store'
import { SummaryCard } from './SummaryCard'
import { DashboardSection } from './DashboardSection'
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
} from 'recharts'

const COLORS = ['#e74c3c', '#e67e22', '#f1c40f', '#2ecc71', '#3498db']

export const RiskClassification: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)
  if (!summary) {
    return <div className="p-4 text-gray-500">No analysis data yet. Upload an Excel file to see risk classification.</div>
  }

  const severityData = Object.entries(summary.severity_counts || {}).map(([name, value]) => ({ name, value }))

  return (
    <section className="space-y-8">
      {/* Risk level and justification */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <SummaryCard label="Risk level" value={<span className="capitalize">{summary.risk}</span>} />
        <SummaryCard label="Justification" value={summary.risk_justification} />
      </div>

      {/* Severity distribution */}
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
    </section>
  )
}

export default RiskClassification
