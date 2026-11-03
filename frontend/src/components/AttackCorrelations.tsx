import React, { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { useAnalysisStore } from '../store'
import { getAttackCorrelations, AttackCorrelations, BehaviorPatterns } from '../api/client'
import { DashboardSection } from './DashboardSection'
import { EmptyState } from './EmptyState'
import { stagger, cardIn } from '../motion'

/**
 * «Correlaciones ATT&CK»: el cruce determinista entre los Event_ID que existen
 * en el dataset y las técnicas del marco (glosario + KB).
 *
 * Además del JOIN al marco, el panel pinta los PATRONES CONDUCTUALES del
 * mismo motor que redacta la sección de correlación del informe
 * (`behavior_correlations.py`): IPs en varios tipos de eventos, acciones
 * repetidas IP + host + evento, IPs y usuarios multi-host y candidatos a
 * fuerza bruta. El JOIN es referencia del marco; los patrones son hallazgo
 * sobre tus datos.
 *
 * Cada técnica mostrada llega porque un Event_ID **observado** la referencia en
 * el glosario, anotada con sus recuentos reales; los Event_ID sin entrada se
 * listan aparte (`unmapped`) en lugar de inventarles una correlación. El panel
 * es una cola de revisión: orden por riesgo observado, no alfabético.
 */
const AttackCorrelationsPanel: React.FC = () => {
  const analysisId = useAnalysisStore(state => state.analysisId)
  const status = useAnalysisStore(state => state.status)

  const [data, setData] = useState<AttackCorrelations | null>(null)
  const [loading, setLoading] = useState(false)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [openEvent, setOpenEvent] = useState<string | null>(null)

  const fetchCorrelations = useCallback(async (signal?: AbortSignal) => {
    if (!analysisId || status !== 'done') return
    setLoading(true)
    try {
      setFetchError(null)
      setData(await getAttackCorrelations(analysisId, signal))
    } catch (err) {
      if ((err as { name?: string })?.name !== 'CanceledError') {
        setData(null)
        setFetchError('No se pudo cargar la correlación ATT&CK. Reintenta o revisa la conexión con el backend.')
      }
    } finally {
      setLoading(false)
    }
  }, [analysisId, status])

  useEffect(() => {
    const controller = new AbortController()
    fetchCorrelations(controller.signal)
    return () => controller.abort()
  }, [fetchCorrelations])

  if (!analysisId || status !== 'done') {
    return (
      <EmptyState hint="Sube un archivo Excel para ver las técnicas MITRE ATT&CK correlacionadas con tus registros." />
    )
  }

  if (fetchError) {
    return (
      <div className="panel">
        <div className="panel-body text-center">
          <p className="font-mono text-[13px] text-[var(--text-dim)]">{fetchError}</p>
          <button
            type="button"
            className="btn-hud btn-primary-hud mt-4"
            onClick={() => fetchCorrelations()}
          >
            Reintentar
          </button>
        </div>
      </div>
    )
  }

  if (loading && !data) {
    return (
      <div className="panel">
        <div className="panel-body font-mono text-[13px] text-hud-muted">
          Cruzando Event_ID observados con la base de conocimiento…
        </div>
      </div>
    )
  }

  if (!data) return null

  const { techniques, tactics, event_ids: eventEntries, unmapped } = data
  // Registros cubiertos = filas cuyo Event_ID tiene glosario (cada fila cuenta
  // una sola vez). La suma de matched_rows por técnica se repite entre técnicas
  // y superaría el total, así que no sirve como KPI.
  const coveredRows = eventEntries.reduce((acc, e) => acc + e.count, 0)

  // --- Correlaciones conductuales (behavior_correlations.py) ---------------
  // Mismo motor que el campo «correlation» del informe: IP ↔ Event_ID ↔
  // Hostname ↔ User ↔ Result, sin modelo. `totals` viene ANTES del corte a
  // las 8 entradas que lista cada bloque, así que el «+N más» es honesto.
  const patrones: BehaviorPatterns | undefined = data.behavior_patterns
  const bloques = patrones
    ? [
        {
          key: 'multi_event_ips',
          titulo: 'IPs presentes en más de un tipo de evento',
          umbral: '≥2 tipos de evento',
          total: patrones.totals.multi_event_ips,
          filas: patrones.multi_event_ips.map(e => ({
            id: e.ip,
            principal: e.ip,
            detalle: `${e.event_types} tipos de evento`,
            cifras: [
              `${e.events} registros`,
              ...(e.hosts === undefined ? [] : [`${e.hosts} hosts`]),
            ],
          })),
        },
        {
          key: 'repeated_actions',
          titulo: 'Acciones repetidas (misma IP + host + evento)',
          umbral: '≥5 repeticiones',
          total: patrones.totals.repeated_actions,
          filas: patrones.repeated_actions.map(e => ({
            id: `${e.ip}|${e.host}|${e.event_id}`,
            principal: e.ip,
            detalle: `evento ${e.event_id} en ${e.host}`,
            cifras: [`×${e.count}`],
          })),
        },
        {
          key: 'multi_host_ips',
          titulo: 'IPs con alcance en muchos hosts',
          umbral: '≥4 hosts distintos',
          total: patrones.totals.multi_host_ips,
          filas: patrones.multi_host_ips.map(e => ({
            id: e.ip,
            principal: e.ip,
            detalle: `${e.hosts} hosts distintos`,
            cifras: [`${e.events} registros`],
          })),
        },
        {
          key: 'multi_host_users',
          titulo: 'Usuarios usados en varios hosts',
          umbral: '≥2 hosts distintos',
          total: patrones.totals.multi_host_users,
          filas: patrones.multi_host_users.map(e => ({
            id: e.user,
            principal: e.user,
            detalle: `${e.hosts} hosts distintos`,
            cifras: [`${e.events} registros`],
          })),
        },
        {
          key: 'brute_force',
          titulo: 'Candidatos a fuerza bruta',
          umbral: '≥3 fallos y al menos 1 éxito',
          total: patrones.totals.brute_force,
          filas: patrones.brute_force.map(e => ({
            id: e.ip,
            principal: e.ip,
            detalle: `${e.failures} fallos · ${e.successes} éxitos`,
            cifras: [
              `${e.events} eventos`,
              ...(e.hosts === undefined ? [] : [`${e.hosts} hosts`]),
            ],
          })),
        },
      ]
    : []
  const hayPatrones = bloques.some(bloque => bloque.total > 0)

  return (
    <section className="space-y-6">
      {/* KPIs: qué tan cubierto está el dataset por el marco */}
      <motion.div
        variants={stagger()}
        initial="hidden"
        animate="show"
        className="grid grid-cols-1 gap-4 sm:grid-cols-4"
      >
        {[
          { label: 'Técnicas observadas', value: techniques.length, hint: 'ancladas a Event_ID reales' },
          { label: 'Tácticos', value: tactics.length, hint: 'cubiertos por esas técnicas' },
          { label: 'Event IDs con glosario', value: eventEntries.length, hint: `de ${data.observed_events} observados` },
          { label: 'Registros cubiertos', value: coveredRows, hint: `de ${data.observed_rows} con Event_ID` },
        ].map(kpi => (
          <motion.div key={kpi.label} variants={cardIn} className="panel">
            {/* Rótulo en caja de 3 líneas: los rótulos ocupan distinto
                número de líneas («Técnicas observadas» en 2, «Event IDs con
                glosario» en 3…) y sin caja fija el número quedaba a distinta
                altura en cada tarjeta. Sin clamp: el desbordamiento de una
                palabra larga lo absorbe el padding de la tarjeta, mientras
                que recortarlo cortaba letras. El pie se ancla abajo (mt-auto)
                para alinear también el texto secundario. */}
            <div className="panel-body flex h-full flex-col p-4">
              <div className="hud-label min-h-[48px] leading-[1.45]">{kpi.label}</div>
              <div className="mt-1 font-display text-2xl font-bold text-hud-line">
                {kpi.value.toLocaleString('es')}
              </div>
              <div className="mt-auto pt-1 font-mono text-[10.5px] text-[var(--text-dim)]">
                {kpi.hint}
              </div>
            </div>
          </motion.div>
        ))}
      </motion.div>

      {/* Correlaciones conductuales: los cruces que el JOIN Event_ID → MITRE
          no hace (IP ↔ host ↔ usuario ↔ resultado), con cifras exactas. */}
      {hayPatrones && (
        <DashboardSection title="Patrones conductuales repetidos">
          <div className="px-4 pt-1">
            <p className="font-mono text-[11px] text-[var(--text-dim)]">
              Cruce determinista sobre tus registros (IP ↔ evento ↔ host ↔ usuario ↔
              resultado): mismas cifras que las del informe técnico.
            </p>
          </div>
          <motion.div
            variants={stagger()}
            initial="hidden"
            animate="show"
            className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2 xl:grid-cols-3"
          >
            {bloques.map(bloque => (
              <motion.div
                key={bloque.key}
                variants={cardIn}
                className="border border-[#d946ef]/35 bg-[var(--bg-void)] p-3"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
                  <span className="text-[12.5px] font-semibold text-[var(--text-bright)]">
                    {bloque.titulo}
                  </span>
                  <span className="font-mono text-[10px] text-[#f0abfc]">
                    {bloque.umbral}
                  </span>
                </div>

                {bloque.filas.length === 0 ? (
                  <p className="mt-2 font-mono text-[11.5px] text-[var(--text-dim)]">
                    Ningún registro de tus datos cumple este umbral.
                  </p>
                ) : (
                  <ul className="mt-2 space-y-1.5">
                    {bloque.filas.map(fila => (
                      <li
                        key={fila.id}
                        className="border-t border-hud-soft pt-1.5 first:border-t-0 first:pt-0"
                      >
                        <span className="font-mono text-[11.5px] font-bold text-hud-line">
                          {fila.principal}
                        </span>
                        <span className="ml-2 text-[11.5px] text-[var(--text)]">
                          {fila.detalle}
                        </span>
                        <span className="ml-2 font-mono text-[10.5px] text-[var(--text-dim)]">
                          {fila.cifras.join(' · ')}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}

                {bloque.total > bloque.filas.length && (
                  <p className="mt-1.5 font-mono text-[10px] text-[var(--text-dim)]">
                    +{bloque.total - bloque.filas.length} más: se listan las{' '}
                    {bloque.filas.length} principales de {bloque.total}.
                  </p>
                )}
              </motion.div>
            ))}
          </motion.div>
        </DashboardSection>
      )}

      {/* Tácticos cubiertos: cabecera de lectura rápida */}
      {tactics.length > 0 && (
        <DashboardSection title="Tácticos cubiertos por los registros">
          <div className="flex flex-wrap gap-2 p-4">
            {tactics.map(tactic => (
              <span
                key={tactic.id}
                title={`${tactic.ids.join(', ')} · ${tactic.rows} registros · ${tactic.suspicious} sospechosos`}
                className="border border-[#d946ef]/45 bg-[#d946ef]/10 px-2.5 py-1 font-mono text-[11px] text-[#f0abfc]"
              >
                {tactic.name_es}
                <span className="ml-1.5 text-[var(--text-dim)]">
                  · {tactic.techniques} téc. · {tactic.rows} reg.
                </span>
              </span>
            ))}
          </div>
        </DashboardSection>
      )}

      {/* Técnicas: orden por riesgo observado (lo manda el backend) */}
      <DashboardSection title="Técnicas MITRE ATT&CK observadas">
        {techniques.length === 0 ? (
          <p className="p-4 font-mono text-[13px] text-[var(--text-dim)]">
            Ningún Event_ID de tus registros está asociado a una técnica ATT&CK en el glosario.
          </p>
        ) : (
          <motion.div
            variants={stagger()}
            initial="hidden"
            animate="show"
            className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2"
          >
            {techniques.map(tech => {
              // Las técnicas completadas desde la fuente oficial no traen
              // traducción curada: se muestra el texto original en inglés
              // (mejor que una tarjeta en blanco).
              const description = tech.descripcion_es || tech.description_en
              const detection = tech.se_detecta
              return (
                <motion.div
                  key={tech.id}
                  variants={cardIn}
                  className="border border-[#d946ef]/35 bg-[var(--bg-void)] p-3"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <span className="font-mono text-xs font-bold text-[#f0abfc]">{tech.id}</span>
                      <span className="ml-1.5 text-[12.5px] font-semibold text-[var(--text-bright)]">
                        {tech.name}
                      </span>
                    </div>
                    <span className="whitespace-nowrap border border-[#d946ef]/50 bg-[#d946ef]/15 px-1.5 py-0.5 text-[10px] text-[#f0abfc]">
                      {tech.tactic_es || tech.tactic}
                    </span>
                  </div>

                  {tech.aliases.length > 0 && (
                    <div className="mt-0.5 text-[10px] text-[var(--text-dim)]">
                      antes: {tech.aliases.join(', ')}
                    </div>
                  )}

                  <p className="mt-1.5 text-[11.5px] leading-relaxed text-[var(--text-dim)]">
                    {description}
                  </p>
                  {detection && (
                    <p className="mt-1 text-[11px] text-[var(--text-dim)]">
                      <span className="font-semibold text-[var(--text)]">Detección:</span> {detection}
                    </p>
                  )}

                  {/* Evidencia observada: con qué Event_ID del dataset se ancla */}
                  <div className="mt-2 border-t border-[#d946ef]/25 pt-2">
                    <div className="flex items-center justify-between">
                      <div className="flex flex-wrap gap-1">
                        {tech.matched_events.slice(0, 5).map(ev => (
                          <span
                            key={ev.event_id}
                            title={`${ev.count} registros · ${ev.suspicious} sospechosos`}
                            className="border border-hud-soft bg-[var(--bg-panel)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-dim)]"
                          >
                            {ev.event_id}
                          </span>
                        ))}
                        {tech.matched_events.length > 5 && (
                          <span className="px-1 py-0.5 font-mono text-[10px] text-[var(--text-dim)]">
                            +{tech.matched_events.length - 5}
                          </span>
                        )}
                      </div>
                      <a
                        href={tech.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="ml-2 shrink-0 text-[10.5px] text-hud-line hover:underline"
                      >
                        attack.mitre.org ↗
                      </a>
                    </div>
                    <div className="mt-1.5 flex items-center gap-3 font-mono text-[10.5px] text-[var(--text-dim)]">
                      <span>
                        <span className="text-[var(--text)]">{tech.matched_rows}</span> registros
                      </span>
                      <span>
                        <span className="text-[#f97316]">{tech.matched_suspicious}</span> sospechosos
                      </span>
                    </div>
                  </div>
                </motion.div>
              )
            })}
          </motion.div>
        )}
      </DashboardSection>

      {/* Event_ID observados con su ficha del glosario (acordeón) */}
      <DashboardSection title={`Event IDs del dataset (${eventEntries.length} con glosario)`}>
        <div className="divide-y divide-hud-soft">
          {eventEntries.map(entry => {
            const isOpen = openEvent === entry.id
            const totalLevels = Object.values(entry.levels).reduce((a, b) => a + b, 0)
            return (
              <div key={entry.id}>
                <button
                  type="button"
                  onClick={() => setOpenEvent(isOpen ? null : entry.id)}
                  aria-expanded={isOpen}
                  className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-hud-line/5"
                >
                  <span className="font-mono text-[12px] font-bold text-hud-line">{entry.id}</span>
                  <span className="flex-1 truncate text-[12.5px] text-[var(--text)]">{entry.titulo}</span>
                  {entry.suspicious > 0 && (
                    <span className="chip" style={{ color: '#f97316', background: '#f973161a' }}>
                      {entry.suspicious} sospechosos
                    </span>
                  )}
                  <span className="font-mono text-[11px] text-[var(--text-dim)]">
                    {entry.count} reg.
                  </span>
                  <span className={`text-[var(--text-dim)] transition-transform ${isOpen ? 'rotate-90' : ''}`}>
                    ▸
                  </span>
                </button>
                {isOpen && (
                  <div className="bg-[var(--bg-void)] px-4 pb-3 pt-1">
                    <p className="text-[11.5px] text-[var(--text-dim)]">{entry.descripcion}</p>
                    <p className="mt-1 text-[11.5px] text-[var(--text-dim)]">
                      <span className="font-semibold text-[var(--text)]">Por qué importa:</span>{' '}
                      {entry.por_que_importa}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[10.5px] text-[var(--text-dim)]">
                      <span>
                        {totalLevels} registros
                        {Object.entries(entry.levels)
                          .map(([lvl, n]) => ` · ${lvl}: ${n}`)
                          .join('')}
                      </span>
                      {entry.hosts && entry.hosts.length > 0 && (
                        <span>hosts: {entry.hosts.join(', ')}</span>
                      )}
                      {entry.first_seen && (
                        <span>
                          {entry.first_seen} → {entry.last_seen}
                        </span>
                      )}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {entry.relacionados.map(tid => (
                        <span
                          key={tid}
                          className="border border-[#d946ef]/40 bg-[#d946ef]/10 px-1.5 py-0.5 font-mono text-[10px] text-[#f0abfc]"
                        >
                          {tid}
                        </span>
                      ))}
                    </div>
                    <div className="mt-1.5 text-[10px] text-[var(--text-dim)]">Fuente: {entry.fuente}</div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </DashboardSection>

      {/* Honesty first: los que no están en el glosario no se correlacionan */}
      {unmapped.length > 0 && (
        <DashboardSection title={`Event IDs sin entrada en el glosario (${unmapped.length})`}>
          <div className="p-4">
            <p className="mb-2.5 font-mono text-[11.5px] text-[var(--text-dim)]">
              Estos Event_ID aparecen en tus registros pero el glosario no los cubre: se listan
              tal cual, sin correlación inventada.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {unmapped.map(item => (
                <span
                  key={item.event_id}
                  title={`${item.count} registros · ${item.suspicious} sospechosos`}
                  className="border border-hud-soft bg-[var(--bg-panel)] px-2 py-1 font-mono text-[11px] text-[var(--text-dim)]"
                >
                  {item.event_id}
                  <span className="ml-1.5 text-[10px]">×{item.count}</span>
                </span>
              ))}
            </div>
          </div>
        </DashboardSection>
      )}

      <p className="border-t border-[#d946ef]/30 pt-2 text-[10.5px] text-[#f0abfc]/85">{data.note}</p>
    </section>
  )
}

export default AttackCorrelationsPanel
