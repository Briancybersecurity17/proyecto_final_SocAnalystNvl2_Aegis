import React, { useState } from 'react'
import { motion } from 'framer-motion'
import { useAnalysisStore, RiskLevelBlock } from '../store'
import { DashboardSection } from './DashboardSection'
import { EmptyState } from './EmptyState'
import { stagger, cardIn } from '../motion'
import { fmtCount, fmtPct } from '../charts'

const LEVEL_ORDER = ['critico', 'alto', 'medio', 'bajo'] as const

const LEVEL_LABELS: Record<string, string> = {
  critico: 'Crítico',
  alto: 'Alto',
  medio: 'Medio',
  bajo: 'Bajo',
}

// Niveles que, si tienen eventos, reciben un pulso de brillo (una sola vez)
const GLOW_LEVELS = ['critico', 'alto']

// Colores fijos por política del proyecto (rojo/naranja/amarillo/verde)
const LEVEL_STYLES: Record<string, { color: string; bar: string }> = {
  critico: { color: '#dc2626', bar: '#dc2626' },
  alto:    { color: '#f97316', bar: '#f97316' },
  medio:   { color: '#facc15', bar: '#facc15' },
  bajo:    { color: '#10b981', bar: '#10b981' },
}

/** Chip de criticidad: borde = color del texto (convención HUD). */
const LevelChip: React.FC<{ level: string; label?: string }> = ({ level, label }) => {
  const color = LEVEL_STYLES[level]?.color ?? 'var(--text-dim)'
  return (
    <span className="chip" style={{ color, background: `${color}1a` }}>
      {label ?? LEVEL_LABELS[level] ?? level}
    </span>
  )
}

/**
 * Lectura ejecutiva de un nivel: lenguaje llano para quien no quiere
 * tecnicismos. Solo usa cifras reales del análisis (recuentos y %) más la
 * semántica del nivel; los detalles técnicos (Event IDs, hosts) se quedan
 * en la justificación del acordeón, así nada se repite.
 */
const executiveNote = (lvl: string, b: RiskLevelBlock, total: number): string => {
  if (b.count === 0) {
    return `Sin registros en este nivel: nada de esta categoría entre los ${total} registros analizados.`
  }
  const head = `${fmtCount(b.count)} registros (${fmtPct(b.percentage)}% del total)`
  switch (lvl) {
    case 'critico':
      return `${head}: exigen atención inmediata; son la prioridad máxima del análisis.`
    case 'alto':
      return `${head}: pocos en volumen, pero marcados como prioridad de revisión.`
    case 'medio':
      return `${head}: actividad a vigilar; por sí sola no indica un incidente.`
    default:
      return `${head}: actividad habitual de la infraestructura; no requiere acción.`
  }
}

const RiskClassification: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)
  const [openLevel, setOpenLevel] = useState<string>('alto')

  if (!summary) {
    return (
      <EmptyState hint="Sube un archivo Excel para ver la clasificación de riesgos por criticidad." />
    )
  }

  const riskLevels = summary.risk_levels ?? {}
  const severityMapping = summary.severity_mapping ?? []

  const block = (lvl: string): RiskLevelBlock =>
    riskLevels[lvl] ?? {
      level: lvl,
      label: lvl,
      count: 0,
      percentage: 0,
      justification: 'Sin datos.',
      examples: [],
    }

  const globalColor = LEVEL_STYLES[summary.risk]?.color ?? '#1fc8ff'

  return (
    <section className="space-y-8">
      {/* Global verdict */}
      <div
        className="border-l-4 bg-inset/85 p-5 shadow-[0_0_16px_rgba(31,200,255,0.14)] [clip-path:var(--chamfer-sm)]"
        style={{ borderLeftColor: globalColor }}
      >
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <h2 className="font-display text-lg font-bold uppercase tracking-[0.14em] text-hud-line">
            Clasificación global
          </h2>
          <LevelChip level={summary.risk} />
        </div>
        <p className="font-mono text-[13.5px] leading-relaxed text-[var(--text)]">
          {summary.risk_justification}
        </p>
        <p className="hud-label mt-2">
          Total analizado: {summary.rows} registros · {summary.unique_hosts ?? '—'} hosts
        </p>
      </div>

      {/* Monitoring cards – one per criticality */}
      <motion.div
        variants={stagger(0.07)}
        initial="hidden"
        animate="show"
        className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4"
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
              className="border border-hud-soft bg-inset/80 shadow-[0_0_12px_rgba(31,200,255,0.12)] transition-shadow [clip-path:var(--chamfer-sm)]"
              style={expanded ? { borderColor: `${style.color}99`, boxShadow: `0 0 18px ${style.color}40` } : undefined}
            >
              <button
                className="w-full p-4 text-left"
                onClick={() => setOpenLevel(expanded ? '' : lvl)}
                aria-expanded={expanded}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="relative inline-block">
                    {/* Halo de brillo: una sola vez al entrar, solo si hay eventos */}
                    {glow && (
                      <motion.span
                        aria-hidden="true"
                        className="absolute inset-0"
                        style={{ backgroundColor: style.bar, filter: 'blur(9px)' }}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: [0, 0.75, 0] }}
                        transition={{ duration: 1.2, delay: 0.35, times: [0, 0.35, 1] }}
                      />
                    )}
                    <span className="relative">
                      <LevelChip level={lvl} label={b.label} />
                    </span>
                  </span>
                  <span className="font-display text-2xl font-bold" style={{ color: style.color }}>
                    {fmtCount(b.count)}
                  </span>
                </div>
                <div className="mt-3 h-2 w-full bg-[var(--bg-void)]">
                  <motion.div
                    className="h-2"
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.min(100, b.percentage)}%` }}
                    transition={{ duration: 0.7, delay: 0.15, ease: 'easeOut' }}
                    style={{ backgroundColor: style.bar, boxShadow: `0 0 8px ${style.bar}` }}
                  />
                </div>
                <p className="mt-1 font-mono text-[12px] text-[var(--text-dim)]">
                  {fmtPct(b.percentage)}% de los registros
                </p>
                <p className="hud-label mt-2">
                  {expanded ? '▲ Clic para contraer' : '▼ Clic para ver justificación'}
                </p>
              </button>

              {expanded && (
                <div
                  className="border-t px-4 pb-4"
                  style={{ borderColor: `${style.color}44`, background: `${style.color}0f` }}
                >
                  <h4 className="hud-label mt-3 mb-1">Justificación técnica</h4>
                  <p className="font-mono text-[12.5px] leading-relaxed text-[var(--text)]">
                    {b.justification}
                  </p>

                  {b.examples.length > 0 && (
                    <>
                      <h4 className="hud-label mt-4 mb-1">
                        Registros de ejemplo ({b.examples.length})
                      </h4>
                      <ul className="space-y-2">
                        {b.examples.map((ex, idx) => (
                          <li
                            key={idx}
                            className="border border-hud-soft bg-[var(--bg-void)] p-2 font-mono text-[12px]"
                          >
                            <div className="font-semibold text-hud-fg">
                              {String(ex.Hostname ?? '—')} · Event {String(ex.Event_ID ?? '—')}
                            </div>
                            <div className="truncate text-[var(--text-dim)]" title={String(ex.Event_Description ?? '')}>
                              {String(ex.Event_Description ?? '—')}
                            </div>
                            <div className="mt-1 italic text-[var(--text-dim)]">{String(ex._reason ?? '')}</div>
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

      {/* Resumen ejecutivo: mismo dato que el acordeón, en lenguaje llano */}
      <DashboardSection title="Resumen ejecutivo por nivel">
        <div className="overflow-auto border border-hud-soft">
          <table className="hud-table min-w-full">
            <caption className="sr-only">Resumen por nivel de riesgo: registros y porcentaje del total</caption>
            <thead>
              <tr>
                <th scope="col">Nivel</th>
                <th scope="col">Registros</th>
                <th scope="col">%</th>
                <th scope="col">Qué significa</th>
              </tr>
            </thead>
            <tbody>
              {LEVEL_ORDER.map((lvl, idx) => {
                const b = block(lvl)
                return (
                  <motion.tr
                    key={lvl}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.22, delay: 0.1 + idx * 0.05, ease: 'easeOut' }}
                  >
                    <td className="whitespace-nowrap">
                      <LevelChip level={lvl} label={b.label} />
                    </td>
                    <td className="font-semibold text-[var(--text-bright)]">{fmtCount(b.count)}</td>
                    <td>{fmtPct(b.percentage)}%</td>
                    <td className="max-w-2xl text-[var(--text-dim)]">
                      {executiveNote(lvl, b, summary.rows)}
                    </td>
                  </motion.tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </DashboardSection>

      {/* Trazabilidad: cómo se tradujo cada valor original del campo Severity */}
      {severityMapping.length > 0 && (
        <DashboardSection title="Correspondencia de severidad original → nivel de riesgo">
          <p className="mb-3 font-mono text-[12.5px] leading-relaxed text-[var(--text-dim)]">
            El campo <code className="border border-hud-soft bg-[var(--bg-void)] px-1">Severity</code> del archivo se
            conserva sin modificar. Esta es la traducción aplicada en el dashboard, los gráficos
            y el informe; los valores fuera del catálogo se clasifican por palabras clave
            (p.&nbsp;ej. <em>warn</em>, <em>critical</em>, <em>high</em>) y, sin evidencia, como
            Bajo.
          </p>
          <div className="overflow-auto border border-hud-soft">
            <table className="hud-table min-w-full">
              <caption className="sr-only">Correspondencia entre el valor original de Severity y el nivel de riesgo</caption>
              <thead>
                <tr>
                  <th scope="col">Valor original (Severity)</th>
                  <th scope="col">Nivel de riesgo</th>
                  <th scope="col">Registros</th>
                  <th scope="col">% del total</th>
                </tr>
              </thead>
              <tbody>
                {severityMapping.map((m, idx) => (
                  <motion.tr
                    key={m.raw}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.22, delay: 0.1 + idx * 0.05, ease: 'easeOut' }}
                  >
                    <td className="whitespace-nowrap font-semibold text-hud-fg">{m.raw}</td>
                    <td className="whitespace-nowrap">
                      <LevelChip level={m.level} />
                    </td>
                    <td className="text-right font-semibold">{fmtCount(m.count)}</td>
                    <td className="text-right">{fmtPct(m.percentage)}%</td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
        </DashboardSection>
      )}
    </section>
  )
}

export default RiskClassification
