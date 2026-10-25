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
  critico: 'bg-red-600 text-white',
  alto: 'bg-orange-500 text-white',
  medio: 'bg-yellow-400 text-yellow-900',
  bajo: 'bg-emerald-500 text-white',
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
        <strong key={key} className="font-semibold text-gray-900">
          {token.slice(2, -2)}
        </strong>,
      )
    } else {
      nodes.push(
        <code key={key} className="px-1 py-0.5 rounded bg-gray-100 text-[13px] font-mono text-indigo-700">
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
    <div className="space-y-1.5 text-sm leading-relaxed text-gray-700">
      {lines.map((line, i) => {
        const key = `l${i}`
        if (!line.trim()) return <div key={key} className="h-2" />
        if (line.startsWith('> '))
          return (
            <div key={key} className="border-l-2 border-indigo-300 pl-3 text-gray-600 text-[13px]">
              {renderInline(line.slice(2), key)}
            </div>
          )
        if (/^\d+\.\s/.test(line))
          return (
            <div key={key} className="flex gap-2 pl-1">
              <span className="font-mono text-[13px] text-indigo-600 shrink-0">
                {line.match(/^\d+/)?.[0]}.
              </span>
              <span className="text-[13px]">{renderInline(line.replace(/^\d+\.\s/, ''), key)}</span>
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
    <div className="mt-3 rounded-lg border border-violet-200 bg-violet-50/60 p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[11px] font-bold uppercase tracking-wide text-violet-700">
          Referencia de marco
        </span>
        <span className="text-[10px] text-violet-500">MITRE ATT&amp;CK · Event IDs Windows</span>
      </div>

      {events.length > 0 && (
        <div className="space-y-1.5 mb-2.5">
          {events.map(item => (
            <div key={item.id} className="text-[12px] leading-snug text-gray-700">
              <span className="font-mono font-semibold text-violet-800">Event ID {item.id}</span>
              <span className={`ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full ${
                item.match === 'evidencia'
                  ? 'bg-emerald-100 text-emerald-700'
                  : 'bg-gray-200 text-gray-600'
              }`}>
                {item.match === 'evidencia' ? 'en tus registros' : 'citado en tu consulta'}
              </span>
              <div className="text-gray-600">{item.titulo}</div>
              <div className="text-gray-500">{item.por_que_importa}</div>
            </div>
          ))}
        </div>
      )}

      {techniques.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {techniques.map(tech => (
            <div key={tech.id} className="border border-violet-200 rounded-lg bg-white p-2.5">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <span className="font-mono text-xs font-bold text-violet-700">{tech.id}</span>
                  <span className="ml-1.5 text-[12px] font-semibold text-gray-800">{tech.name}</span>
                </div>
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-100 text-violet-700 whitespace-nowrap">
                  {tech.tactic_es}
                </span>
              </div>
              {tech.aliases.length > 0 && (
                <div className="text-[10px] text-gray-400 mt-0.5">
                  antes: {tech.aliases.join(', ')}
                </div>
              )}
              <p className="text-[11.5px] text-gray-600 mt-1">{tech.descripcion_es}</p>
              <p className="text-[11px] text-gray-500 mt-1">
                <span className="font-semibold text-gray-600">Detección:</span> {tech.se_detecta}
              </p>
              <div className="flex items-center justify-between mt-1.5">
                <div className="flex flex-wrap gap-1">
                  {tech.eventos.slice(0, 4).map(ev => (
                    <span key={ev} className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
                      {ev}
                    </span>
                  ))}
                </div>
                <a
                  href={tech.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[10.5px] text-indigo-600 hover:underline shrink-0 ml-2"
                >
                  attack.mitre.org ↗
                </a>
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-[10.5px] text-violet-600/80 mt-2 border-t border-violet-200 pt-1.5">
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
  const ready = status === 'done' && !!analysisId

  // Estado del índice semántico (se actualiza mientras termina la indexación)
  useEffect(() => {
    if (!ready || !analysisId) return
    let cancelled = false
    const load = async () => {
      try {
        const data = await getRagStatus(analysisId)
        if (!cancelled) setRag(data)
      } catch {
        if (!cancelled) setRag(null)
      }
    }
    load()
    const timer = window.setInterval(load, 3000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
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
      setMessages(prev => [...prev, { id: `u${Date.now()}`, role: 'user', text }])
      setLoading(true)
      try {
        const res = await askQuestion(analysisId, text, {
          level: level || undefined,
          suspicious,
          nResults: 8,
        })
        setMessages(prev => [...prev, { id: `a${Date.now()}`, role: 'assistant', text: res.answer, response: res }])
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
        tone: 'bg-amber-50 border-amber-200 text-amber-800',
        text: `Índice semántico no disponible (${rag.reason ?? 'sin motor'}): la consulta usa búsqueda léxica sobre los registros.`,
      }
    if (!rag.events)
      return {
        tone: 'bg-indigo-50 border-indigo-200 text-indigo-800',
        text: 'Indexando los registros en el índice semántico… la primera consulta puede tardar unos segundos.',
      }
    const kbPart = kb
      ? ` · base de conocimiento: ${kb.techniques} técnicas ATT&CK + ${kb.event_ids} Event IDs`
      : ''
    return {
      tone: 'bg-emerald-50 border-emerald-200 text-emerald-800',
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
        <div className={`border rounded-lg px-4 py-2.5 text-sm ${ragNote.tone}`}>{ragNote.text}</div>
      )}

      <DashboardSection>
        {/* Sugerencias */}
        {messages.length === 0 && (
          <div className="mb-4">
            <p className="text-sm text-gray-500 mb-2">Prueba con una de estas consultas:</p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map(suggestion => (
                <button
                  key={suggestion}
                  onClick={() => send(suggestion)}
                  className="text-sm border border-indigo-200 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded-full px-3.5 py-1.5 transition-colors"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Conversación */}
        <div
          ref={listRef}
          className="h-[46vh] min-h-[320px] overflow-y-auto rounded-lg border border-gray-200 bg-gray-50 p-4 space-y-4"
        >
          {messages.length === 0 && (
            <p className="text-sm text-gray-400 text-center mt-12">
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
                <div className="max-w-[85%] bg-primary text-white rounded-2xl rounded-br-sm px-4 py-2.5 text-sm">
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
                <div className="max-w-[92%] bg-white border border-gray-200 rounded-2xl rounded-bl-sm px-4 py-3 shadow-sm">
                  <AnswerBody text={message.text} />

                  {message.response && (
                    <div className="mt-3 pt-3 border-t border-gray-100 space-y-2">
                      <div className="flex flex-wrap items-center gap-2 text-[11px]">
                        <span className="px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 font-medium">
                          {message.response.mode === 'aggregate'
                            ? message.response.summary_line
                            : `${message.response.retrieved} eventos recuperados`}
                        </span>
                        <span className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                          confianza {message.response.confidence}
                        </span>
                        <span className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                          {message.response.engine === 'llm'
                            ? `modelo ${message.response.model}`
                            : 'motor local determinista'}
                        </span>
                        {message.response.warnings.includes('baja_confianza') && (
                          <span className="px-2 py-0.5 rounded-full bg-amber-100 text-amber-700">
                            baja similitud
                          </span>
                        )}
                      </div>

                      {message.response.evidence.length > 0 && (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                          {message.response.evidence.map((item, idx) => (
                            <div
                              key={`${item.log_id}-${idx}`}
                              className="border border-gray-200 rounded-lg p-2.5 bg-white"
                            >
                              <div className="flex items-center justify-between gap-2 mb-1.5">
                                <span className="font-mono text-xs font-semibold text-gray-800">
                                  {item.log_id}
                                </span>
                                <span
                                  className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded-full ${
                                    LEVEL_BADGE[item.level] ?? 'bg-gray-200 text-gray-700'
                                  }`}
                                >
                                  {LEVEL_LABEL[item.level] ?? item.level}
                                </span>
                              </div>
                              <div className="text-[11px] text-gray-600 space-y-0.5 font-mono">
                                <div>host {item.host} · origen {item.source_ip}</div>
                                <div>Event_ID {item.event_id} · {item.severity}</div>
                              </div>
                              <div className="mt-2">
                                <div className="h-1.5 rounded-full bg-gray-100 overflow-hidden">
                                  <div
                                    className="h-full bg-indigo-500"
                                    style={{ width: `${Math.round(item.relevance * 100)}%` }}
                                  />
                                </div>
                                <div className="text-[10px] text-gray-400 mt-0.5">
                                  similitud {(item.relevance * 100).toFixed(0)}%
                                </div>
                              </div>
                            </div>
                          ))}
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
              <div className="bg-white border border-gray-200 rounded-2xl rounded-bl-sm px-4 py-3 shadow-sm">
                <div className="flex items-center gap-1.5 text-gray-400">
                  {[0, 1, 2].map(i => (
                    <motion.span
                      key={i}
                      className="h-2 w-2 rounded-full bg-indigo-400"
                      animate={{ opacity: [0.3, 1, 0.3] }}
                      transition={{ duration: 1, repeat: Infinity, delay: i * 0.15 }}
                    />
                  ))}
                  <span className="ml-2 text-xs">recuperando evidencias…</span>
                </div>
              </div>
            </div>
          )}
        </div>

        {error && (
          <div className="mt-3 border border-red-200 bg-red-50 text-red-700 text-sm rounded-lg px-4 py-2.5">
            {error}
          </div>
        )}

        {/* Filtros + entrada */}
        <div className="mt-4 flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-2 text-gray-600">
              <span>Filtrar por criticidad</span>
              <select
                value={level}
                onChange={e => setLevel(e.target.value)}
                className="border border-gray-300 rounded px-2 py-1.5 text-sm bg-white"
              >
                <option value="">Todas</option>
                <option value="critico">Crítico</option>
                <option value="alto">Alto</option>
                <option value="medio">Medio</option>
                <option value="bajo">Bajo</option>
              </select>
            </label>
            <label className="flex items-center gap-2 text-gray-600 cursor-pointer">
              <input
                type="checkbox"
                checked={suspicious}
                onChange={e => setSuspicious(e.target.checked)}
                className="h-4 w-4 accent-indigo-600"
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
              className="flex-1 border border-gray-300 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent"
            />
            <button
              type="submit"
              disabled={loading || input.trim().length < 3}
              className="px-5 py-2.5 rounded-lg bg-primary text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90 transition-opacity"
            >
              {loading ? 'Consultando…' : 'Consultar'}
            </button>
          </form>

          {summary && (
            <p className="text-xs text-gray-400">
              Base: {summary.rows} registros indexados · los datos nunca salen de tu equipo.
            </p>
          )}
        </div>
      </DashboardSection>
    </section>
  )
}

export default AssistantChat
