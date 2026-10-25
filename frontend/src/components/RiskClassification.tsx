import React, { useState } from 'react'
import { motion } from 'framer-motion'
import { useAnalysisStore, RiskLevelBlock } from '../store'
import { DashboardSection } from './DashboardSection'
import { stagger, cardIn } from '../motion'

const LEVEL_ORDER = ['critico', 'alto', 'medio', 'bajo'] as const

// Niveles que, si tienen eventos, reciben un pulso de brillo (una sola vez)
const GLOW_LEVELS = ['critico', 'alto']

const LEVEL_STYLES: Record<string, { border: string; bg: string; text: string; chip: string; bar: string }> = {
  critico: { border: 'border-red-600', bg: 'bg-red-50', text: 'text-red-700', chip: 'bg-red-600 text-white', bar: '#dc2626' },
  alto:    { border: 'border-orange-500', bg: 'bg-orange-50', text: 'text-orange-700', chip: 'bg-orange-500 text-white', bar: '#f97316' },
  medio:   { border: 'border-yellow-400', bg: 'bg-yellow-50', text: 'text-yellow-800', chip: 'bg-yellow-400 text-yellow-900', bar: '#facc15' },
  bajo:    { border: 'border-emerald-500', bg: 'bg-emerald-50', text: 'text-emerald-700', chip: 'bg-emerald-500 text-white', bar: '#10b981' },
}

const RiskClassification: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)
  const [openLevel, setOpenLevel] = useState<string>('alto')

  if (!summary) {
    return (
      <div className="p-8 text-center text-gray-500 bg-white rounded shadow">
        <p className="text-lg font-medium mb-2">Sin datos todavía</p>
        <p>Sube un archivo Excel en la sección <strong>Carga de archivos</strong> para ver la clasificación de riesgos.</p>
      </div>
    )
  }

  const riskLevels = summary.risk_levels ?? {}

  const block = (lvl: string): RiskLevelBlock =>
    riskLevels[lvl] ?? {
      level: lvl,
      label: lvl,
      count: 0,
      percentage: 0,
      justification: 'Sin datos.',
      examples: [],
    }

  return (
    <section className="space-y-8">
      {/* Global verdict */}
      <div className="bg-white rounded shadow p-5 border-l-4 border-primary">
        <div className="flex flex-wrap items-center gap-3 mb-2">
          <h2 className="text-xl font-semibold">Clasificación global</h2>
          <span className={`px-3 py-1 rounded-full text-sm font-bold uppercase ${LEVEL_STYLES[summary.risk]?.chip ?? 'bg-gray-200'}`}>
            {summary.risk}
          </span>
        </div>
        <p className="text-gray-700 leading-relaxed">{summary.risk_justification}</p>
        <p className="text-xs text-gray-500 mt-2">
          Total analizado: {summary.rows} registros · {summary.unique_hosts ?? '—'} hosts
        </p>
      </div>

      {/* Monitoring cards – one per criticality */}
      <motion.div
        variants={stagger(0.07)}
        initial="hidden"
        animate="show"
        className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4"
      >
        {LEVEL_ORDER.map(lvl => {
          const b = block(lvl)
          const style = LEVEL_STYLES[lvl]
          const expanded = openLevel === lvl
          const glow = b.count > 0 && GLOW_LEVELS.includes(lvl)
          return (
            <motion.div
              key={lvl}
              variants={cardIn}
              className={`bg-white rounded shadow border-t-4 ${style.border} ${expanded ? 'ring-2 ring-primary' : ''}`}
            >
              <button
                className="w-full text-left p-4"
                onClick={() => setOpenLevel(expanded ? '' : lvl)}
              >
                <div className="flex items-center justify-between">
                  <span className="relative inline-block">
                    {/* Halo de brillo: una sola vez al entrar, solo si hay eventos */}
                    {glow && (
                      <motion.span
                        aria-hidden="true"
                        className="absolute inset-0 rounded"
                        style={{ backgroundColor: style.bar, filter: 'blur(9px)' }}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: [0, 0.75, 0] }}
                        transition={{ duration: 1.2, delay: 0.35, times: [0, 0.35, 1] }}
                      />
                    )}
                    <span className={`relative px-2 py-0.5 rounded text-xs font-bold uppercase ${style.chip}`}>
                      {b.label}
                    </span>
                  </span>
                  <span className="text-2xl font-bold" style={{ color: undefined }}>{b.count}</span>
                </div>
                <div className="w-full bg-gray-200 rounded-full h-2 mt-3">
                  <motion.div
                    className="h-2 rounded-full"
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.min(100, b.percentage)}%` }}
                    transition={{ duration: 0.7, delay: 0.15, ease: 'easeOut' }}
                    style={{ backgroundColor: style.bar }}
                  />
                </div>
                <p className="text-xs text-gray-500 mt-1">{b.percentage}% de los registros</p>
                <p className="text-xs text-gray-400 mt-2">{expanded ? '▲ Clic para contraer' : '▼ Clic para ver justificación'}</p>
              </button>

              {expanded && (
                <div className={`px-4 pb-4 ${style.bg} border-t`}>
                  <h4 className="text-xs font-semibold uppercase text-gray-600 mt-3 mb-1">Justificación</h4>
                  <p className="text-sm text-gray-700 leading-relaxed">{b.justification}</p>

                  {b.examples.length > 0 && (
                    <>
                      <h4 className="text-xs font-semibold uppercase text-gray-600 mt-4 mb-1">
                        Registros de ejemplo ({b.examples.length})
                      </h4>
                      <ul className="space-y-2">
                        {b.examples.map((ex, idx) => (
                          <li key={idx} className="text-xs bg-white rounded border p-2">
                            <div className="font-medium">
                              {String(ex.Hostname ?? '—')} · Event {String(ex.Event_ID ?? '—')}
                            </div>
                            <div className="text-gray-600 truncate" title={String(ex.Event_Description ?? '')}>
                              {String(ex.Event_Description ?? '—')}
                            </div>
                            <div className="text-gray-500 mt-1 italic">{String(ex._reason ?? '')}</div>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              )}
            </motion.div>
          )
        })}
      </motion.div>

      {/* Detailed justification table */}
      <DashboardSection title="Justificación por nivel">
        <div className="overflow-auto">
          <table className="min-w-full text-sm border border-gray-200">
            <thead className="bg-gray-100">
              <tr>
                <th className="px-3 py-2 text-left">Nivel</th>
                <th className="px-3 py-2 text-left">Registros</th>
                <th className="px-3 py-2 text-left">% </th>
                <th className="px-3 py-2 text-left">Justificación</th>
              </tr>
            </thead>
            <tbody>
              {LEVEL_ORDER.map((lvl, idx) => {
                const b = block(lvl)
                const style = LEVEL_STYLES[lvl]
                return (
                  <motion.tr
                    key={lvl}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.22, delay: 0.1 + idx * 0.05, ease: 'easeOut' }}
                    className={idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'}
                  >
                    <td className="px-3 py-2">
                      <span className={`px-2 py-0.5 rounded text-xs font-bold uppercase ${style.chip}`}>{b.label}</span>
                    </td>
                    <td className="px-3 py-2 font-semibold">{b.count}</td>
                    <td className="px-3 py-2">{b.percentage}%</td>
                    <td className="px-3 py-2 text-gray-700 max-w-2xl">{b.justification}</td>
                  </motion.tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </DashboardSection>
    </section>
  )
}

export default RiskClassification
