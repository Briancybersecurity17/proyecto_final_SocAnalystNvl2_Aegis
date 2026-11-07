import React, { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useAnalysisStore, RiskLevelBlock, SeverityMappingEntry } from '../store'
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

/** Glosa en español de los valores de Severity, para citarlos sin jerga. */
const SEVERITY_GLOSS: Record<string, string> = {
  critical: 'crítica',
  critico: 'crítica',
  high: 'alta',
  alto: 'alta',
  medium: 'media',
  medio: 'media',
  warning: 'advertencia',
  low: 'baja',
  bajo: 'baja',
  informational: 'informativa',
  informative: 'informativa',
  info: 'informativa',
}

/**
 * «Remate» de cada nivel en lenguaje llano: responde la pregunta que la
 * justificación técnica no contesta —¿por qué ESTE nivel?—, sin tecnicismos.
 * Para Crítico se cita el valor original de Severity en palabras («Warning» =
 * advertencia) porque ahí está el porqué concreto de estos datos, y solo se
 * menciona la política de «advertencia = crítica» si ese valor aparece; si el
 * archivo no trae columna Severity no hay qué citar y no se inventa.
 */
const plainWhy = (lvl: string, criterion: SeverityMappingEntry[]): string => {
  switch (lvl) {
    case 'critico': {
      const paren = criterion.length
        ? ` (aquí: ${criterion
            .map(m => {
              const gloss = SEVERITY_GLOSS[m.raw.trim().toLowerCase()]
              return `«${m.raw}»${gloss ? ` = ${gloss}` : ''}`
            })
            .join(' · ')})`
        : ''
      const policy = criterion.some(m => m.raw.trim().toLowerCase() === 'warning')
        ? ', y por política una advertencia se trata como crítica'
        : ''
      return `el archivo los marcó como los más graves${paren}${policy}: son los que hay que revisar primero, con atención inmediata.`
    }
    case 'alto':
      return 'pocos pero relevantes: severidad alta en el archivo, en la cola de revisión después de los críticos.'
    case 'medio':
      return 'severidad media en el archivo: actividad a vigilar que, por sí sola, no indica un incidente.'
    default:
      return 'actividad habitual de la infraestructura (severidad baja o informativa): no requiere acción.'
  }
}

const RiskClassification: React.FC = () => {
  const summary = useAnalysisStore(state => state.summary)
  // Vista detalle: null = rejilla de 4 tarjetas (siempre colapsadas); lvl =
  // panel a ancho completo con toda la info de ese nivel. Sin acordeón: una
  // tarjeta nunca crece dentro de la grilla, así no se estiran las otras.
  const [detail, setDetail] = useState<string | null>(null)
  // Origen horizontal (%) desde el que crece el panel: la posición de la
  // tarjeta elegida, para que la animación arranque donde estaba la vista.
  const [origin, setOrigin] = useState(50)
  const gridRef = useRef<HTMLDivElement>(null)
  const backRef = useRef<HTMLButtonElement>(null)

  // ESC vuelve a la rejilla: el mismo atajo que el botón «← Volver».
  useEffect(() => {
    if (!detail) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDetail(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [detail])

  // El foco lo mueve el panel al terminar su animación de entrada: con
  // AnimatePresence mode="wait" el botón monta ~160 ms después del estado,
  // así que un useEffect sobre [detail] llegaría con el ref aún vacío.
  // Al salir, backRef ya es null y el foco no se toca.
  const focusBack = () => backRef.current?.focus()

  // Un análisis nuevo vuelve a la rejilla: el panel no se hereda entre
  // corridas. generated_at cambia solo al analizar otro archivo; mover un
  // filtro no lo toca (el panel se queda y sus cifras se actualizan solas).
  const generatedAt = summary?.generated_at
  useEffect(() => {
    setDetail(null)
  }, [generatedAt])

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

  /**
   * Abre el panel de nivel: guarda el origen de la animación (dónde estaba
   * la tarjeta dentro de la grilla) antes de cambiar el estado, porque al
   * re-renderizar la grilla ya no está en el DOM.
   */
  const openDetail = (lvl: string, card: HTMLElement) => {
    const grid = gridRef.current
    if (grid) {
      const r = card.getBoundingClientRect()
      const g = grid.getBoundingClientRect()
      setOrigin(g.width ? ((r.left + r.width / 2 - g.left) / g.width) * 100 : 50)
    }
    setDetail(lvl)
  }

  /**
   * Panel de detalle a ancho completo (1 columna, como el mockup aprobado):
   * botón de vuelta, cabecera con chip + conteo + barra + %, justificación
   * técnica, el remate en lenguaje llano y los ejemplos trazables. Es el
   * contenido del antiguo acordeón, fuera de la grilla.
   */
  const renderDetail = (lvl: string) => {
    const b = block(lvl)
    const style = LEVEL_STYLES[lvl]
    // Valores ORIGINALES de Severity que se traducen a ESTE nivel: alimenta
    // el remate «Por qué es…», que cita el porqué concreto de estos datos.
    const criterion = severityMapping.filter(m => m.level === lvl)
    return (
      <>
        <button
          ref={backRef}
          onClick={() => setDetail(null)}
          className="mb-4 inline-flex items-center gap-2 border border-hud-soft bg-[var(--bg-void)] px-3 py-1.5 font-mono text-[11.5px] uppercase tracking-[0.1em] text-hud-line transition-colors hover:border-hud-line hover:bg-[rgba(31,200,255,0.08)] focus-visible:border-hud-line focus-visible:shadow-[0_0_10px_rgba(31,200,255,0.35)] focus-visible:outline-none"
        >
          ← Volver a los 4 niveles
        </button>

        {/* Cabecera: mismo dato que la tarjeta en la grilla, a mayor tamaño */}
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-hud-soft pb-4">
          <div className="flex items-center gap-4">
            <LevelChip level={lvl} label={b.label} />
            <span className="font-display text-[40px] leading-none font-bold" style={{ color: style.color }}>
              {fmtCount(b.count)}
            </span>
          </div>
          <div className="min-w-[220px] flex-1 sm:max-w-[320px]">
            <div className="h-2 w-full bg-[var(--bg-void)]">
              <motion.div
                className="h-2"
                initial={{ width: 0 }}
                animate={{ width: `${Math.min(100, b.percentage)}%` }}
                transition={{ duration: 0.7, delay: 0.2, ease: 'easeOut' }}
                style={{ backgroundColor: style.bar, boxShadow: `0 0 8px ${style.bar}` }}
              />
            </div>
            <p className="mt-1 font-mono text-[12px] text-[var(--text-dim)]">
              {fmtPct(b.percentage)}% de los registros
            </p>
          </div>
        </div>

        <h4 className="hud-label mt-4 mb-1">Justificación técnica</h4>
        <p className="font-mono text-[12.5px] leading-relaxed text-[var(--text)]">
          {b.justification}
        </p>

        {/* Remate en lenguaje llano: la pregunta que la justificación
            técnica no contesta —¿por qué ESTE nivel?—. El dato técnico de
            arriba queda para quien lo quiera; esto es el cierre para
            cualquier lector. */}
        {b.count > 0 && (
          <>
            <h4 className="hud-label mt-4 mb-1">Por qué es {b.label}</h4>
            <p className="text-[13px] leading-relaxed text-[var(--text)]">
              {plainWhy(lvl, criterion)}
            </p>
          </>
        )}

        {b.examples.length > 0 && (
          <>
            <h4 className="hud-label mt-4 mb-1">
              Registros de ejemplo ({b.examples.length})
            </h4>
            <ul className="space-y-2">
              {b.examples.map((ex, idx) => {
                // Severity original del registro: se pinta con el color del
                // nivel al que se traduce, para que se vea de un vistazo
                // «Warning → Crítico».
                const sevRaw = String(ex.Severity ?? '').trim()
                const sevLevel = sevRaw
                  ? severityMapping.find(m => m.raw === sevRaw)?.level
                  : undefined
                return (
                  <li
                    key={idx}
                    className="border border-hud-soft bg-[var(--bg-void)] p-2 font-mono text-[12px]"
                  >
                    {/* Trazabilidad primero: con el Log_ID el registro se
                        puede volver a buscar en la tabla y en el informe. */}
                    <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-[var(--text-dim)]">
                      <span className="font-bold text-hud-line">
                        {String(ex.Log_ID ?? '—')}
                      </span>
                      {ex.Timestamp && <span>{String(ex.Timestamp)}</span>}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-hud-fg">
                        {String(ex.Hostname ?? '—')} · Event {String(ex.Event_ID ?? '—')}
                      </span>
                      {sevRaw &&
                        (sevLevel ? (
                          <LevelChip level={sevLevel} label={sevRaw} />
                        ) : (
                          <span className="text-[10.5px] text-[var(--text-dim)]">
                            Severity: {sevRaw}
                          </span>
                        ))}
                    </div>
                    <div className="truncate text-[var(--text-dim)]" title={String(ex.Event_Description ?? '')}>
                      {String(ex.Event_Description ?? '—')}
                    </div>
                    <div className="mt-1 italic text-[var(--text-dim)]">{String(ex._reason ?? '')}</div>
                  </li>
                )
              })}
            </ul>
          </>
        )}
      </>
    )
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

      {/* Monitoring cards – one per criticality. Sin acordeón: la rejilla
          queda siempre colapsada y el clic abre el detalle a ancho completo.
          AnimatePresence mode="wait" encadena las transiciones (sale la
          grilla y recién después entra el panel), para que las dos alturas
          no se pisen en el mismo frame. */}
      <AnimatePresence mode="wait">
        {detail === null ? (
          <motion.div
            key="grid"
            ref={gridRef}
            variants={stagger(0.07)}
            initial="hidden"
            animate="show"
            exit={{ opacity: 0, scale: 0.98, transition: { duration: 0.16, ease: 'easeIn' } }}
            className="grid grid-cols-1 items-start gap-4 md:grid-cols-2 xl:grid-cols-4"
          >
            {LEVEL_ORDER.map(lvl => {
              const b = block(lvl)
              const style = LEVEL_STYLES[lvl]
              const glow = b.count > 0 && GLOW_LEVELS.includes(lvl)
              return (
                <motion.div
                  key={lvl}
                  variants={cardIn}
                  className="border border-hud-soft bg-inset/80 shadow-[0_0_12px_rgba(31,200,255,0.12)] transition-shadow [clip-path:var(--chamfer-sm)] hover:border-[rgba(31,200,255,0.55)]"
                >
                  <button
                    className="w-full p-4 text-left"
                    onClick={e => openDetail(lvl, e.currentTarget)}
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
                    <p className="hud-label mt-2">▼ Ver detalle</p>
                  </button>
                </motion.div>
              )
            })}
          </motion.div>
        ) : (
          <motion.div
            key="detail"
            initial={{ opacity: 0, y: 8, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1, transition: { duration: 0.26, ease: 'easeOut' } }}
            exit={{ opacity: 0, y: 8, scale: 0.97, transition: { duration: 0.2, ease: 'easeIn' } }}
            onAnimationComplete={focusBack}
            style={{ transformOrigin: `${origin}% 0` }}
            className="border border-hud-soft bg-inset/80 p-5 shadow-[0_0_12px_rgba(31,200,255,0.12)] [clip-path:var(--chamfer-sm)]"
            role="region"
            aria-label={`Detalle del nivel ${LEVEL_LABELS[detail] ?? detail}`}
          >
            {renderDetail(detail)}
          </motion.div>
        )}
      </AnimatePresence>

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
