import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { useAnalysisStore } from '../store'
import {
  askQuestion,
  getRagStatus,
  getKbStatus,
  AskResponse,
  RagStatusResponse,
  KbStatus,
} from '../api/client'
import { DashboardSection } from './DashboardSection'
import { EmptyState } from './EmptyState'

const LEVEL_BADGE: Record<string, string> = {
  critico: '#dc2626',
  alto: '#f97316',
  medio: '#facc15',
  bajo: '#10b981',
}

const LEVEL_LABEL: Record<string, string> = {
  critico: 'Crítico',
  alto: 'Alto',
  medio: 'Medio',
  bajo: 'Bajo',
}

const SUGGESTIONS = [
  '¿Qué hosts concentran los eventos críticos?',
  '¿Hubo intentos de fuerza bruta?',
  '¿Qué significa el Event ID 1102?',
  '¿Qué pasó en el controlador de dominio?',
  'Muéstrame los eventos sospechosos de nivel alto',
]

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  response?: AskResponse
}

type RagStatus = RagStatusResponse

/**
 * Renderiza texto plano del backend en nodos seguros (sin dangerouslySetInnerHTML).
 * Soporta: negritas `**texto**`, código `` `texto` ``, listas "1." y citas "> ".
 */
const renderInline = (text: string, keyBase: string): React.ReactNode[] => {
  const nodes: React.ReactNode[] = []
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0
  let match: RegExpExecArray | null
  let index = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index))
    const token = match[0]
    const key = `${keyBase}-${index++}`
    if (token.startsWith('**')) {
      nodes.push(
        <strong key={key} className="font-semibold text-[var(--text-bright)]">
          {token.slice(2, -2)}
        </strong>,
      )
    } else {
      nodes.push(
        <code
          key={key}
          className="border border-hud-soft bg-[var(--bg-void)] px-1 py-0.5 font-mono text-[12.5px] text-hud-fg"
        >
          {token.slice(1, -1)}
        </code>,
      )
    }
    last = match.index + token.length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

const AnswerBody: React.FC<{ text: string }> = ({ text }) => {
  const lines = text.split('\n')
  return (
    <div className="space-y-1.5 font-mono text-[13px] leading-relaxed text-[var(--text)]">
      {lines.map((line, i) => {
        const key = `l${i}`
        if (!line.trim()) return <div key={key} className="h-2" />
        if (line.startsWith('> '))
          return (
            <div
              key={key}
              className="border-l-2 border-hud-line/60 pl-3 text-[12.5px] text-[var(--text-dim)]"
            >
              {renderInline(line.slice(2), key)}
            </div>
          )
        if (/^\d+\.\s/.test(line))
          return (
            <div key={key} className="flex gap-2 pl-1">
              <span className="shrink-0 font-mono text-[12.5px] text-hud-line">
                {line.match(/^\d+/)?.[0]}.
              </span>
              <span className="text-[12.5px]">{renderInline(line.replace(/^\d+\.\s/, ''), key)}</span>
            </div>
          )
        return <p key={key}>{renderInline(line, key)}</p>
      })}
    </div>
  )
}

/**
 * Bloque «Referencia técnica»: lo que significan los Event IDs observados y a
 * qué técnicas MITRE ATT&CK se asocian. Es MATERIAL DEL MARCO, separado de la
 * evidencia, y así se etiqueta explícitamente (nunca es un hallazgo del archivo).
 */
const ReferenceBlock: React.FC<{ reference?: AskResponse['reference'] }> = ({ reference }) => {
  if (!reference?.available) return null
  const { event_ids: events, techniques } = reference
  if (!events.length && !techniques.length) return null

  return (
    <div className="mt-3 border border-[#d946ef]/45 bg-[#d946ef]/10 p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-[#e879f9]">
          Referencia de marco
        </span>
        <span className="font-mono text-[10px] text-[#f0abfc]">
          MITRE ATT&amp;CK · Event IDs Windows
        </span>
      </div>

      {events.length > 0 && (
        <div className="mb-2.5 space-y-1.5">
          {events.map(item => (
            <div key={item.id} className="text-[12px] leading-snug text-[var(--text)]">
              <span className="font-mono font-semibold text-[#f0abfc]">Event ID {item.id}</span>
              <span
                className={`ml-1.5 px-1.5 py-0.5 text-[10px] ${
                  item.match === 'evidencia'
                    ? 'border border-[#10b981]/60 bg-[#10b981]/15 text-[#10b981]'
                    : 'border border-hud-soft bg-[var(--bg-void)] text-[var(--text-dim)]'
                }`}
              >
                {item.match === 'evidencia' ? 'en tus registros' : 'citado en tu consulta'}
              </span>
              <div className="text-[var(--text-dim)]">{item.titulo}</div>
              <div className="text-[var(--text-dim)]">{item.por_que_importa}</div>
            </div>
          ))}
        </div>
      )}

      {techniques.length > 0 && (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {techniques.map(tech => (
            <div key={tech.id} className="border border-[#d946ef]/35 bg-[var(--bg-void)] p-2.5">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <span className="font-mono text-xs font-bold text-[#f0abfc]">{tech.id}</span>
                  <span className="ml-1.5 text-[12px] font-semibold text-[var(--text-bright)]">
                    {tech.name}
                  </span>
                </div>
                <span className="whitespace-nowrap border border-[#d946ef]/50 bg-[#d946ef]/15 px-1.5 py-0.5 text-[10px] text-[#f0abfc]">
                  {tech.tactic_es}
                </span>
              </div>
              {tech.aliases.length > 0 && (
                <div className="mt-0.5 text-[10px] text-[var(--text-dim)]">
                  antes: {tech.aliases.join(', ')}
                </div>
              )}
              <p className="mt-1 text-[11.5px] text-[var(--text-dim)]">{tech.descripcion_es}</p>
              <p className="mt-1 text-[11px] text-[var(--text-dim)]">
                <span className="font-semibold text-[var(--text)]">Detección:</span> {tech.se_detecta}
              </p>
              <div className="mt-1.5 flex items-center justify-between">
                <div className="flex flex-wrap gap-1">
                  {tech.eventos.slice(0, 4).map(ev => (
                    <span
                      key={ev}
                      className="border border-hud-soft bg-[var(--bg-panel)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-dim)]"
                    >
                      {ev}
                    </span>
                  ))}
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
            </div>
          ))}
        </div>
      )}

      <p className="mt-2 border-t border-[#d946ef]/30 pt-1.5 text-[10.5px] text-[#f0abfc]/85">
        {reference.note}
      </p>
    </div>
  )
}

export const AssistantChat: React.FC = () => {
  const analysisId = useAnalysisStore(state => state.analysisId)
  const summary = useAnalysisStore(state => state.summary)
  const status = useAnalysisStore(state => state.status)

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [level, setLevel] = useState('')
  const [suspicious, setSuspicious] = useState(false)
  const [rag, setRag] = useState<RagStatus | null>(null)
  const [kb, setKb] = useState<KbStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  const listRef = useRef<HTMLDivElement>(null)
  // Contador de mensajes: `Date.now()` repetía clave si dos mensajes caían en
  // el mismo milisegundo (el guard de `loading` es un estado, asíncrono).
  const msgSeq = useRef(0)
  const ready = status === 'done' && !!analysisId

  // Sugerencias: la de Result solo aparece si el archivo tiene ese campo.
  const suggestions = useMemo(() => {
    const base = [...SUGGESTIONS]
    if (summary?.result_counts && Object.keys(summary.result_counts).length > 0) {
      base.push('¿Cuántos registros fallaron o devolvieron error HTTP?')
    }
    return base
  }, [summary])

  // Estado del índice semántico (se actualiza mientras termina la indexación).
  // El sondeo se detiene en cuanto el índice queda listo: mantenerlo cada 3 s
  // para siempre solo generaba tráfico repetido contra el backend.
  useEffect(() => {
    if (!ready || !analysisId) return
    let cancelled = false
    let timer: number | null = null
    const load = async () => {
      try {
        const data = await getRagStatus(analysisId)
        if (cancelled) return
        setRag(data)
        if (data.available && data.total > 0 && timer !== null) {
          window.clearInterval(timer)
          timer = null
        }
      } catch {
        if (!cancelled) setRag(null)
      }
    }
    void load()
    timer = window.setInterval(() => void load(), 3000)
    return () => {
      cancelled = true
      if (timer !== null) window.clearInterval(timer)
    }
  }, [ready, analysisId])

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, loading])

  // Estado de la base de conocimiento (se consulta una vez al estar listo)
  useEffect(() => {
    if (!ready) return
    let cancelled = false
    getKbStatus()
      .then(data => {
        if (!cancelled) setKb(data)
      })
      .catch(() => {
        if (!cancelled) setKb(null)
      })
    return () => {
      cancelled = true
    }
  }, [ready])

  const send = useCallback(
    async (question: string) => {
      const text = question.trim()
      if (!text || !analysisId || loading) return
      setError(null)
      setInput('')
      setMessages(prev => [...prev, { id: `u${++msgSeq.current}`, role: 'user', text }])
      setLoading(true)
      try {
        const res = await askQuestion(analysisId, text, {
          level: level || undefined,
          suspicious,
          nResults: 8,
        })
        setMessages(prev => [...prev, { id: `a${++msgSeq.current}`, role: 'assistant', text: res.answer, response: res }])
      } catch (err) {
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'No se pudo consultar el asistente. Revisa que el backend esté activo en el puerto 8500.'
        setError(detail)
      } finally {
        setLoading(false)
      }
    },
    [analysisId, loading, level, suspicious],
  )

  const ragNote = useMemo(() => {
    if (!rag) return null
    if (!rag.available)
      return {
        tone: '#ffb020',
        text: `Índice semántico no disponible (${rag.reason ?? 'sin motor'}): la consulta usa búsqueda léxica sobre los registros.`,
      }
    if (!rag.events)
      return {
        tone: '#1fc8ff',
        text: 'Indexando los registros en el índice semántico… la primera consulta puede tardar unos segundos.',
      }
    const kbPart = kb
      ? ` · base de conocimiento: ${kb.techniques} técnicas ATT&CK + ${kb.event_ids} Event IDs`
      : ''
    return {
      tone: '#10b981',
      text: `Recuperación híbrida activa · ${rag.events} eventos indexados · ${rag.model}${kbPart}`,
    }
  }, [rag, kb])

  if (!ready) {
    return (
      <EmptyState hint="Sube un archivo Excel para preguntarle al asistente sobre sus registros." />
    )
  }

  return (
    <section className="space-y-4">
      {ragNote && (
        <div
          className="border px-4 py-2.5 font-mono text-[12.5px]"
          style={{ borderColor: `${ragNote.tone}80`, background: `${ragNote.tone}14`, color: ragNote.tone }}
        >
          {ragNote.text}
        </div>
      )}

      <DashboardSection>
        {/* Sugerencias */}
        {messages.length === 0 && (
          <div className="mb-4">
            <p className="hud-label mb-2">Prueba con una de estas consultas:</p>
            <div className="flex flex-wrap gap-2">
              {suggestions.map(suggestion => (
                <button
                  key={suggestion}
                  onClick={() => send(suggestion)}
                  className="btn-hud"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Conversación */}
        <div className="h-[46vh] min-h-[320px] space-y-4 overflow-y-auto border border-hud-soft bg-[var(--bg-void)] p-4">
          {messages.length === 0 && (
            <p className="mt-12 text-center font-mono text-[13px] text-[var(--text-dim)]">
              El asistente responde citando los <strong>Log_ID</strong> de los eventos recuperados.
              No genera hallazgos que no estén en tu archivo.
            </p>
          )}

          {messages.map(message =>
            message.role === 'user' ? (
              <motion.div
                key={message.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex justify-end"
              >
                <div className="max-w-[85%] border border-[#60a5fa]/50 bg-primary px-4 py-2.5 font-mono text-[13px] text-white shadow-[0_0_12px_rgba(30,64,175,0.5)]">
                  {message.text}
                </div>
              </motion.div>
            ) : (
              <motion.div
                key={message.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex justify-start"
              >
                <div className="max-w-[92%] border border-hud-soft bg-[var(--bg-panel)] px-4 py-3 shadow-[0_0_12px_rgba(31,200,255,0.12)]">
                  <AnswerBody text={message.text} />

                  {message.response && (
                    <div className="mt-3 space-y-2 border-t border-hud-soft pt-3">
                      <div className="flex flex-wrap items-center gap-2 text-[11px]">
                        <span className="chip whitespace-normal normal-case text-hud-line">
                          {message.response.mode === 'aggregate'
                            ? message.response.summary_line
                            : `${message.response.retrieved} eventos recuperados`}
                        </span>
                        <span className="chip whitespace-normal normal-case text-[var(--text-dim)]">
                          confianza {message.response.confidence}
                        </span>
                        <span className="chip whitespace-normal normal-case text-[var(--text-dim)]">
                          {message.response.engine === 'llm'
                            ? `modelo ${message.response.model}`
                            : 'motor local determinista'}
                        </span>
                        {message.response.warnings.includes('baja_confianza') && (
                          <span className="chip whitespace-normal normal-case text-[#ffb020]">
                            baja similitud
                          </span>
                        )}
                      </div>

                      {message.response.evidence.length > 0 && (
                        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                          {message.response.evidence.map((item, idx) => {
                            const tone = LEVEL_BADGE[item.level] ?? '#7280a3'
                            return (
                              <div
                                key={`${item.log_id}-${idx}`}
                                className="border border-hud-soft bg-[var(--bg-void)] p-2.5"
                              >
                                <div className="mb-1.5 flex items-center justify-between gap-2">
                                  <span className="font-mono text-xs font-semibold text-hud-fg">
                                    {item.log_id}
                                  </span>
                                  <span
                                    className="chip text-[10px]"
                                    style={{ color: tone, background: `${tone}1f` }}
                                  >
                                    {LEVEL_LABEL[item.level] ?? item.level}
                                  </span>
                                </div>
                                <div className="space-y-0.5 font-mono text-[11px] text-[var(--text-dim)]">
                                  <div className="truncate text-[var(--text)]" title={item.description}>
                                    {item.description}
                                  </div>
                                  <div>
                                    host {item.host} · origen {item.source_ip}
                                  </div>
                                  <div>
                                    Event_ID {item.event_id} · {item.severity}
                                    {item.result && item.result !== '—' && (
                                      <span className="text-[var(--text)]"> · Result {item.result}</span>
                                    )}
                                  </div>
                                </div>
                                <div className="mt-2">
                                  <div className="h-1.5 overflow-hidden bg-[var(--bg-panel)]">
                                    <div
                                      className="h-full bg-hud-line"
                                      style={{
                                        width: `${Math.round(item.relevance * 100)}%`,
                                        boxShadow: '0 0 8px #1fc8ff',
                                      }}
                                    />
                                  </div>
                                  <div className="mt-0.5 text-[10px] text-[var(--text-dim)]">
                                    similitud {(item.relevance * 100).toFixed(0)}%
                                  </div>
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      )}

                      <ReferenceBlock reference={message.response.reference} />
                    </div>
                  )}
                </div>
              </motion.div>
            ),
          )}

          {loading && (
            <div className="flex justify-start">
              <div className="border border-hud-soft bg-[var(--bg-panel)] px-4 py-3 shadow-[0_0_12px_rgba(31,200,255,0.12)]">
                <div className="flex items-center gap-1.5 text-hud-line">
                  {[0, 1, 2].map(i => (
                    <motion.span
                      key={i}
                      className="h-2 w-2 rounded-full bg-hud-line"
                      animate={{ opacity: [0.3, 1, 0.3] }}
                      transition={{ duration: 1, repeat: Infinity, delay: i * 0.15 }}
                    />
                  ))}
                  <span className="ml-2 font-mono text-xs">recuperando evidencias…</span>
                </div>
              </div>
            </div>
          )}
        </div>

        {error && (
          <div className="mt-3 border border-[#dc2626]/60 bg-[#dc2626]/10 px-4 py-2.5 font-mono text-[13px] text-[#f87171]">
            {error}
          </div>
        )}

        {/* Filtros + entrada */}
        <div className="mt-4 flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3 font-mono text-[13px]">
            <label className="flex items-center gap-2 text-[var(--text-dim)]">
              <span>Filtrar por criticidad</span>
              <select value={level} onChange={e => setLevel(e.target.value)} className="hud-input py-1.5">
                <option value="">Todas</option>
                <option value="critico">Crítico</option>
                <option value="alto">Alto</option>
                <option value="medio">Medio</option>
                <option value="bajo">Bajo</option>
              </select>
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-[var(--text-dim)]">
              <input
                type="checkbox"
                checked={suspicious}
                onChange={e => setSuspicious(e.target.checked)}
                className="h-4 w-4 accent-[#1fc8ff]"
              />
              <span>Solo sospechosos</span>
            </label>
          </div>

          <form
            onSubmit={e => {
              e.preventDefault()
              send(input)
            }}
            className="flex gap-2"
          >
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder="Pregunta sobre los registros… p. ej. ¿qué pasó en DC-01?"
              maxLength={300}
              className="hud-input flex-1"
            />
            <button
              type="submit"
              disabled={loading || input.trim().length < 3}
              className="btn-hud btn-primary-hud"
            >
              {loading ? 'Consultando…' : 'Consultar'}
            </button>
          </form>

          {summary && (
            <p className="font-mono text-[11.5px] text-[var(--text-dim)]">
              Base: {summary.rows} registros indexados · los datos nunca salen de tu equipo.
            </p>
          )}
        </div>
      </DashboardSection>
    </section>
  )
}

export default AssistantChat
