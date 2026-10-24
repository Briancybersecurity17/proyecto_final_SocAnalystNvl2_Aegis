import React from 'react'
import { useAnalysisStore } from '../store'
import { DashboardSection } from './DashboardSection'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'

export const SecurityAnalysis: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)
  if (!summary) {
    return <div className="p-4 text-gray-500">No analysis data yet. Upload an Excel file to see the security analysis.</div>
  }

  return (
    <section className="space-y-8">
      <DashboardSection title="Suspicious events">
        <div className="overflow-auto">
          <table className="min-w-full text-sm border border-gray-200">
            <thead className="bg-gray-100">
              <tr>
                <th className="px-2 py-1 text-left">Hostname</th>
                <th className="px-2 py-1 text-left">Event ID</th>
                <th className="px-2 py-1 text-left">Description</th>
              </tr>
            </thead>
            <tbody>
              {summary.suspicious_details?.map((ev, idx) => (
                <tr key={idx} className={idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                  <td className="px-2 py-1">{ev.Hostname}</td>
                  <td className="px-2 py-1">{ev.Event_ID}</td>
                  <td className="px-2 py-1">{ev.Event_Description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </DashboardSection>

      <DashboardSection title="Correlated suspicious hosts">
        <ResponsiveContainer width="100%" height={300}>
          <BarChart
            data={Object.entries(summary.suspicious_correlations || {}).map(([host, count]) => ({ host, count }))}
            layout="vertical"
            margin={{ top: 20, right: 30, left: 80, bottom: 20 }}
          >
            <XAxis type="number" />
            <YAxis dataKey="host" type="category" />
            <Tooltip />
            <Bar dataKey="count" fill="#ff7300" />
          </BarChart>
        </ResponsiveContainer>
      </DashboardSection>
    </section>
  )
}

export default SecurityAnalysis
