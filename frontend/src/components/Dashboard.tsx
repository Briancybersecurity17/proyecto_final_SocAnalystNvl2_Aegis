import React from 'react'
import { useAnalysisStore } from '../store'
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts'

const Dashboard: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)

  if (!summary) {
    return (
      <div className="p-4 text-gray-500">No analysis data yet. Upload an Excel file to see the dashboard.</div>
    )
  }

  // Example data – you can shape this however you need based on the AI output
  const data = [
    { name: 'Rows', value: summary.rows },
    { name: 'Risk', value: summary.risk === 'high' ? 3 : summary.risk === 'medium' ? 2 : 1 },
  ]

  return (
    <section className="bg-white rounded shadow p-4">
      <h2 className="text-lg font-medium mb-4">Dashboard</h2>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data}
          margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
        >
          <XAxis dataKey="name" />
          <YAxis hide />
          <Tooltip />
          <Legend />
          <Bar dataKey="value" fill="#1e40af" />
        </BarChart>
      </ResponsiveContainer>
    </section>
  )
}

export default Dashboard
