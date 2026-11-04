import React, { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import {
  AttackCandidate,
  approveAttackCandidate,
  getAttackCandidates,
  rejectAttackCandidate,
} from '../api/client'
import { cardIn } from '../motion'

/**
 * «Fichas candidatas»: el taller de aprendizaje de ataques nuevos (fase 3).
 *
 * Cuando el asistente encuentra un ataque FUERA del catálogo —o una ficha
 * existente que no aplica al archivo— y el LLM está encendido, propone un
 * borrador de ficha con las columnas y Event IDs REALES del archivo, ya
 * validado de forma determinista. Acá queda PENDIENTE hasta que el analista
 * lo apruebe o lo rechace: nunca entra al catálogo solo. Aprobar agrega la
 * ficha (o reemplaza la regla, si es una mejora) y match_card la ve al
 * instante; rechazar marca la decisión para siempre.
 */

/** Rótulos de los motivos de rechazo que puede devolver el backend. */
const MOTIVO_NO_OK: Record<string, string> = {
  rechazada_previa: 'Ya la rechazaste antes: esa decisión se respeta.',
  aprobada_previa: 'Ya estaba aprobada.',
  no_existe: 'Esa candidata ya no existe.',
  catalogo_ilegible: 'El catálogo de fichas no se pudo leer: no se aprobó nada.',
}

const ESTADO_LABEL: Record<string, string> = {
  pendiente: 'pendiente de revisión',
  aprobada: 'aprobada',
  rechazada: 'rechazada',
}

const ESTADO_CLASE: Record<string, string> = {
  pendiente: 'border-amber-400/40 text-amber-300',
  aprobada: 'border-emerald-400/40 text-emerald-300',
  rechazada: 'border-red-400/40 text-red-300',
}

const AttackCandidates: React.FC = () => {
  const [items, setItems] = useState<AttackCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [nota, setNota] = useState<string | null>(null)

  const fetchCandidates = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    try {
      setFetchError(null)
      const res = await getAttackCandidates(undefined, signal)
      setItems(res.candidates)
    } catch (err) {
      if ((err as { name?: string })?.name !== 'CanceledError') {
        setFetchError(
          'No se pudieron cargar las fichas candidatas. Reintenta o revisa la conexión con el backend.',
        )
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void fetchCandidates(controller.signal)
    return () => controller.abort()
  }, [fetchCandidates])

  const decidir = async (id: string, accion: 'approve' | 'reject') => {
    setBusyId(id)
    setNota(null)
    setFetchError(null)
    try {
      const res =
        accion === 'approve'
          ? await approveAttackCandidate(id)
          : await rejectAttackCandidate(id)
      if (res.ok) {
        setNota(
          res.motivo === 'aprobada'
            ? `Ficha «${id}» aprobada: ya está en el catálogo y el asistente puede detectarla.`
            : res.motivo === 'rechazada'
              ? `Candidata «${id}» rechazada: no volverá a proponerse.`
              : res.motivo === 'ya_aprobada'
                ? `«${id}» ya estaba aprobada.`
                : `«${id}» ya estaba rechazada.`,
        )
      } else {
        setFetchError(
          MOTIVO_NO_OK[res.motivo] ??
            `No se pudo procesar «${id}» (motivo: ${res.motivo}).`,
        )
      }
      await fetchCandidates()
    } catch {
      setFetchError(
        `No se pudo ${accion === 'approve' ? 'aprobar' : 'rechazar'} «${id}». Reintenta o revisa la conexión con el backend.`,
      )
    } finally {
      setBusyId(null)
    }
  }

  return (
    <motion.section variants={cardIn} className="space-y-4">
      <div className="panel">
        <div className="panel-body">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="panel-title">Fichas candidatas (ataques nuevos)</h2>
              <p className="hud-label mt-1">
                Ninguna entra al catálogo sin tu aprobación
              </p>
            </div>
            <button
              type="button"
              className="btn-hud"
              onClick={() => void fetchCandidates()}
              disabled={loading}
            >
              {loading ? 'Actualizando…' : 'Actualizar'}
            </button>
          </div>

          {nota && (
            <p className="mt-3 font-mono text-[13px] text-emerald-400">{nota}</p>
          )}
          {fetchError && (
            <p className="mt-3 font-mono text-[13px] text-amber-400">{fetchError}</p>
          )}

          {items.length === 0 && !fetchError ? (
            <p className="mt-4 font-mono text-[13px] text-[var(--text-dim)]">
              {loading
                ? 'Cargando candidatas…'
                : 'Sin candidatas todavía. Cuando el asistente encuentre un ataque fuera del catálogo (con el LLM encendido) propondrá un borrador acá, con sus columnas y Event IDs reales, para tu revisión.'}
            </p>
          ) : (
            <ul className="mt-4 space-y-3">
              {items.map(cand => (
                <li key={cand.id} className="rounded border border-hud-soft p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[13px] font-semibold text-hud">
                      {cand.card?.nombre || cand.id}
                    </span>
                    <span className="rounded border border-sky-400/40 px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide text-sky-300">
                      {cand.tipo === 'mejora'
                        ? 'mejora de ficha existente'
                        : 'ficha nueva'}
                    </span>
                    <span
                      className={`rounded border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide ${ESTADO_CLASE[cand.status] ?? ''}`}
                    >
                      {ESTADO_LABEL[cand.status] ?? cand.status}
                    </span>
                  </div>

                  <p className="mt-2 font-mono text-[12px] text-[var(--text-dim)]">
                    Pregunta que la generó: «{cand.question}» · evaluación:{' '}
                    {cand.evaluacion.applicable
                      ? `aplica (${cand.evaluacion.total} ${cand.evaluacion.total === 1 ? 'detección' : 'detecciones'})`
                      : `no aplica (${cand.evaluacion.motivo}${
                          cand.evaluacion.missing.length
                            ? `, falta ${cand.evaluacion.missing.join(', ')}`
                            : ''
                        })`}
                  </p>

                  {cand.regla_anterior && (
                    <p className="mt-1 font-mono text-[12px] text-[var(--text-dim)]">
                      Regla actual → propuesta:{' '}
                      <span className="text-amber-200/80">
                        {JSON.stringify(cand.regla_anterior)}
                      </span>{' '}
                      →{' '}
                      <span className="text-emerald-200/80">
                        {JSON.stringify(cand.card?.regla)}
                      </span>
                    </p>
                  )}

                  {cand.status === 'pendiente' && (
                    <div className="mt-3 flex gap-2">
                      <button
                        type="button"
                        className="btn-hud btn-primary-hud"
                        disabled={busyId === cand.id}
                        onClick={() => void decidir(cand.id, 'approve')}
                      >
                        {busyId === cand.id ? 'Procesando…' : 'Aprobar'}
                      </button>
                      <button
                        type="button"
                        className="btn-hud"
                        disabled={busyId === cand.id}
                        onClick={() => void decidir(cand.id, 'reject')}
                      >
                        Rechazar
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </motion.section>
  )
}

export default AttackCandidates
