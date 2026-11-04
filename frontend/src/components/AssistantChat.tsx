import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { useAnalysisStore } from '../store'
import {
  askQuestion,
  askQuestionStream,
  clearChatHistory,
  getRagStatus,
  sendFeedback,
  AskResponse,
  RagStatusResponse,
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

/**
 * Preguntas agnósticas: valen con CUALQUIER archivo cargado (no nombran
 * hosts, Event IDs ni cifras de un dataset concreto).
 */
const GENERIC_SUGGESTIONS = [
  '¿Qué hosts concentran los eventos críticos?',
  '¿Hubo intentos de fuerza bruta?',
  'Muéstrame los eventos sospechosos de nivel alto',
]

/** Clave con mayor valor de un conteo {clave: nº} — el dato dominante del archivo. */
const topCountKey = (counts?: Record<string, number>): string | null => {
  if (!counts) return null
  let best: string | null = null
  for (const [key, value] of Object.entries(counts)) {
    if (value > 0 && (best === null || value > (counts[best] ?? 0))) best = key
  }
  return best
}

/** Motivos por los que se sustituye la respuesta del LLM por la determinista. */
const WARNING_LABEL: Record<string, string> = {
  grounding_rechazado: 'respuesta sustituida: el modelo citó Log_ID inexistentes',
  stream_interrumpido: 'respuesta sustituida: el modelo se interrumpió a mitad',
  llm_sin_respuesta: 'respuesta local: el modelo no devolvió texto',
  fuera_de_tema: 'fuera del tema: no aparece en tus registros',
  consulta_bloqueada: 'consulta bloqueada: instrucciones ajenas a los registros',
  cortesia: 'saludo: sin consulta sobre los registros',
  candidata_creada: 'candidata creada: pendiente de tu aprobación en «Correlaciones ATT&CK»',
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  /** Consulta que originó esta respuesta: se reenvía al corregirla (few-shot). */
  question?: string
  response?: AskResponse
  /** true mientras llegan tokens del LLM (se muestra el cursor de escritura). */
  streaming?: boolean
  /** Motivo de sustitución (evento `fallback`): se muestra como aviso ámbar. */
  warning?: string
  /** 'saved' = la corrección alimentó la memoria de criterio del analista. */
  feedback?: 'saved' | 'error'
  /**
   * Usuario abrió las coincidencias débiles de esta respuesta. Sin esto, una
   * consulta fuera de tema pintaba 8 tarjetas rojas de «similitud 2%» justo
   * debajo del «no aparece en tus registros»; ahora vienen colapsadas tras
   * un botón que muestra la mejor similitud.
   */
  showWeak?: boolean
}

// ---------------------------------------------------------------------------
// Persistencia del chat en la pestaña (sessionStorage)
//
// `App.tsx` desmonta este componente al cambiar de sección y el `useState`
// perdía todos los mensajes: ir a otra sección —o pulsar F5— borraba la
// conversación aunque el backend seguía con su memoria multi-turno. La clave
// es POR ANÁLISIS: sobrevive al cambio de sección y al F5 con la pestaña
// viva, se limpia al cerrarla, y un archivo nuevo nunca hereda el chat del
// anterior. Todo en try/catch (mismo patrón que `persist.ts`): sin storage
// la app sigue funcionando, solo que sin restaurar.
// ---------------------------------------------------------------------------
const CHAT_KEY_PREFIX = 'dojo_soc.chat'

/** Tope de mensajes persistidos: una sesión larga no debe acercarse a la
 *  cuota de sessionStorage (~5 MB); el historial útil es corto. */
const CHAT_MAX_MESSAGES = 50

const chatKey = (analysisId: string): string => `${CHAT_KEY_PREFIX}.${analysisId}`

/** Forma mínima de un mensaje guardado: lo que no la cumpla se descarta. */
const isValidStoredMessage = (value: unknown): value is ChatMessage => {
  if (!value || typeof value !== 'object') return false
  const m = value as Partial<ChatMessage>
  return (
    typeof m.id === 'string' &&
    m.id.length > 0 &&
    (m.role === 'user' || m.role === 'assistant') &&
    typeof m.text === 'string'
  )
}

const loadChatMessages = (analysisId: string): ChatMessage[] => {
  if (!analysisId) return []
  try {
    const raw = window.sessionStorage.getItem(chatKey(analysisId))
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(isValidStoredMessage).map(m => ({
      ...m,
      // Un mensaje que se guardó a mitad de streaming no puede volver con el
      // cursor de escritura pegado: se restaura como respuesta terminada.
      streaming: false,
    }))
  } catch {
    // storage no disponible o JSON corrupto: se ignora deliberadamente
    return []
  }
}

const saveChatMessages = (analysisId: string, messages: ChatMessage[]): void => {
  if (!analysisId) return
  try {
    window.sessionStorage.setItem(
      chatKey(analysisId),
      JSON.stringify(messages.slice(-CHAT_MAX_MESSAGES)),
    )
  } catch {
    // almacenamiento no disponible (modo privado / cuota): se ignora
  }
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
 * Botón «Corregir» de una respuesta del asistente.
 *
 * Es la vía EXPLÍCITA de aprendizaje: el analista escribe la respuesta buena
 * y el backend la guarda como criterio few-shot para consultas futuras afines
 * (sin reentrenar el modelo). Tras guardar muestra la confirmación para que
 * quede claro que la corrección quedó aprendida.
 */
const FeedbackControl: React.FC<{
  analysisId: string
  question: string
  badAnswer: string
  saved?: boolean
  onSaved: () => void
  onError: (msg: string) => void
}> = ({ analysisId, question, badAnswer, saved, onSaved, onError }) => {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)

  const submit = async () => {
    const text = draft.trim()
    if (!text || sending) return
    setSending(true)
    try {
      const res = await sendFeedback(analysisId, question, text, badAnswer)
      setDraft('')
      setOpen(false)
      if (res.ok) onSaved()
      else onError('No se pudo guardar la corrección.')
    } catch {
      onError('No se pudo guardar la corrección. Revisa el backend.')
    } finally {
      setSending(false)
    }
  }

  if (saved) {
    return (
      <div className="mt-3 border border-[#10b981]/45 bg-[#10b981]/10 px-3 py-2 font-mono text-[11.5px] text-[#34d399]">
        ✓ Corrección aprendida: el asistente la usará como criterio en consultas
        similares.
      </div>
    )
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-3 font-mono text-[11.5px] text-[var(--text-dim)] underline decoration-dotted hover:text-[var(--text-bright)]"
      >
        ¿Está mal? Corregir esta respuesta
      </button>
    )
  }

  return (
    <div className="mt-3 border border-hud-soft bg-[var(--bg-void)] p-3">
      <label className="hud-label" htmlFor="feedback-correction">
        Respuesta correcta (así debería haber respondido)
      </label>
      <textarea
        id="feedback-correction"
        className="hud-input mt-2 w-full resize-y font-mono text-[12.5px]"
        rows={4}
        value={draft}
        autoFocus
        placeholder='Ej.: SRV-DC01 concentra 12 intentos fallidos (Log_ID "EVT-10432").'
        onChange={e => setDraft(e.target.value)}
      />
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={!draft.trim() || sending}
          className="btn-hud btn-primary-hud disabled:opacity-50"
        >
          {sending ? 'guardando…' : 'Aprender corrección'}
        </button>
        <button
          type="button"
          onClick={() => {
            setDraft('')
            setOpen(false)
          }}
          className="btn-hud"
        >
          Cancelar
        </button>
      </div>
      <p className="mt-2 font-mono text-[10.5px] text-[var(--text-dim)]">
        Se guarda como criterio del analista: no cambia el modelo, solo enseña a
        este asistente a responder como vos.
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
  const [error, setError] = useState<string | null>(null)

  const listRef = useRef<HTMLDivElement>(null)
  // Contador de mensajes: `Date.now()` repetía clave si dos mensajes caían en
  // el mismo milisegundo (el guard de `loading` es un estado, asíncrono).
  const msgSeq = useRef(0)
  // true → el PRIMER guardado tras (re)montar se salta: en StrictMode (dev)
  // React monta dos veces y el efecto de guardado corre con el estado VIEJO
  // (`[]`) antes de que la segunda restauración lea el storage… y borraba
  // el chat entero. Tras saltarlo, el cambio de mensajes derivado de la
  // restauración vuelve a ejecutar el guardado con el estado ya correcto.
  const skipNextSave = useRef(false)
  const ready = status === 'done' && !!analysisId

  // Host dominante del archivo cargado: ancla también el placeholder del input.
  const topHost = topCountKey(summary?.top_hosts)

  // Sugerencias ancladas AL DATASET CARGADO: su Event ID más frecuente y su
  // host dominante salen del summary, nunca de una lista escrita a mano (así
  // sirven con cualquier archivo, no solo con el del analista de turno). La
  // de Result solo aparece si el archivo tiene ese campo; al ir antes que las
  // genéricas no se pierde nunca del corte a 5.
  const suggestions = useMemo(() => {
    const base: string[] = []
    const eventId = topCountKey(summary?.event_id_counts)
    if (eventId) base.push(`¿Qué significa el Event ID ${eventId}?`)
    if (topHost) base.push(`¿Qué pasó en ${topHost}?`)
    if (summary?.result_counts && Object.keys(summary.result_counts).length > 0) {
      base.push('¿Cuántos registros fallaron o devolvieron error HTTP?')
    }
    for (const generica of GENERIC_SUGGESTIONS) {
      if (base.length >= 5) break
      base.push(generica)
    }
    return base.slice(0, 5)
  }, [summary, topHost])

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

  // Restaura el historial de ESTE análisis: corre en montaje (id restaurado
  // tras F5) y en cada cambio de archivo, para que un chat viejo nunca se
  // pinte sobre un análisis nuevo.
  useEffect(() => {
    if (!analysisId) return
    skipNextSave.current = true
    const restored = loadChatMessages(analysisId)
    // Claves React únicas: los ids siguen del último restaurado (u12/a12 →
    // los próximos mensajes arrancan en u13/a13), así no colisionan.
    msgSeq.current = restored.reduce((max, m) => {
      const n = Number.parseInt(m.id.slice(1), 10)
      return Number.isNaN(n) ? max : Math.max(max, n)
    }, msgSeq.current)
    setMessages(restored)
  }, [analysisId])

  // Guarda tras cada cambio de mensajes: incluye el vacío, así que «Borrar
  // conversación» limpia también el storage de la pestaña.
  useEffect(() => {
    if (!analysisId) return
    if (skipNextSave.current) {
      skipNextSave.current = false
      return
    }
    saveChatMessages(analysisId, messages)
  }, [messages, analysisId])

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, loading])

  const send = useCallback(
    async (question: string) => {
      const text = question.trim()
      if (!text || !analysisId || loading) return
      setError(null)
      setInput('')
      const assistantId = `a${++msgSeq.current}`
      setMessages(prev => [
        ...prev,
        { id: `u${++msgSeq.current}`, role: 'user', text },
        { id: assistantId, role: 'assistant', text: '', streaming: true, question: text },
      ])
      setLoading(true)

      // Acumula tokens y los vuelca al estado cada ~80 ms: repintar por cada
      // fragmento del modelo saturaba React (qwen emite trozos de 1-3 letras).
      let buffer = ''
      let timer: number | null = null
      const flush = () => {
        if (!buffer) return
        const chunk = buffer
        buffer = ''
        setMessages(prev =>
          prev.map(m => (m.id === assistantId ? { ...m, text: m.text + chunk } : m)),
        )
      }
      const push = (token: string) => {
        buffer += token
        if (timer === null) {
          timer = window.setTimeout(() => {
            timer = null
            flush()
          }, 80)
        }
      }

      try {
        await askQuestionStream(
          analysisId,
          text,
          { level: level || undefined, suspicious, nResults: 8 },
          event => {
            if (event.kind === 'meta') {
              // Evidencias y referencia llegan antes que el texto: se pintan ya.
              setMessages(prev =>
                prev.map(m =>
                  m.id === assistantId ? { ...m, response: event.data as AskResponse } : m,
                ),
              )
            } else if (event.kind === 'token') {
              push(event.text)
            } else if (event.kind === 'final') {
              flush()
              setMessages(prev =>
                prev.map(m =>
                  m.id === assistantId
                    ? { ...m, text: event.data.answer, response: event.data, streaming: false, warning: undefined }
                    : m,
                ),
              )
            } else if (event.kind === 'fallback') {
              // El LLM falló o citó Log_ID inventados: se muestra el determinista.
              flush()
              setMessages(prev =>
                prev.map(m =>
                  m.id === assistantId
                    ? {
                        ...m,
                        text: event.data.answer,
                        response: event.data,
                        streaming: false,
                        warning: event.data.warning ?? 'llm_sin_respuesta',
                      }
                    : m,
                ),
              )
            }
          },
        )
        // El stream cerró sin `final` (p. ej. corte del proxy): no se queda el
        // cursor de escritura clavado y el mensaje vacío se retira.
        setMessages(prev => {
          const target = prev.find(m => m.id === assistantId)
          if (target && !target.text.trim()) return prev.filter(m => m.id !== assistantId)
          return prev.map(m => (m.id === assistantId ? { ...m, streaming: false } : m))
        })
      } catch {
        // El transporte SSE falló antes de emitir nada: se reintenta con la
        // consulta normal (sin streaming) para no perder la pregunta.
        try {
          const res = await askQuestion(analysisId, text, {
            level: level || undefined,
            suspicious,
            nResults: 8,
          })
          flush()
          setMessages(prev =>
            prev.map(m =>
              m.id === assistantId
                ? { ...m, text: res.answer, response: res, streaming: false }
                : m,
            ),
          )
        } catch (fallbackErr) {
          const detail =
            (fallbackErr as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
            'No se pudo consultar el asistente. Revisa que el backend esté activo en el puerto 8500.'
          setError(detail)
          // Sin respuesta utilizable se retira el mensaje del asistente.
          setMessages(prev => prev.filter(m => m.id !== assistantId))
        }
      } finally {
        if (timer !== null) window.clearTimeout(timer)
        flush()
        setLoading(false)
      }
    },
    [analysisId, loading, level, suspicious],
  )

  /** Borra la conversación en pantalla y la memoria multi-turno del backend. */
  const clearConversation = useCallback(async () => {
    if (!analysisId) return
    setMessages([])
    try {
      await clearChatHistory(analysisId)
    } catch {
      // El backend puede estar caído: la memoria en RAM morirá al reiniciarse.
    }
  }, [analysisId])

  // True cuando la burbuja del streaming ya se pinta (llegó el `meta` con las
  // evidencias o el primer token): mientras tanto se muestra «recuperando…».
  const streamingVisible = messages.some(
    m => m.role === 'assistant' && m.streaming && (!!m.response || !!m.text),
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
    return {
      tone: '#10b981',
      text: `Recuperación híbrida activa · ${rag.events} eventos indexados · ${rag.model}`,
    }
  }, [rag])

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

          {messages.map(message => {
            // La burbuja del streaming se muestra desde el `meta` (evidencias):
            // hasta entonces se ve el indicador de «recuperando…».
            if (message.role === 'assistant' && message.streaming && !message.response && !message.text)
              return null
            return message.role === 'user' ? (
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
                  {/* Cursor de escritura (▍) mientras llegan tokens del modelo. */}
                  <AnswerBody text={message.streaming ? `${message.text}▍` : message.text} />

                  {message.response && (
                    <div className="mt-3 space-y-2 border-t border-hud-soft pt-3">
                      <div className="flex flex-wrap items-center gap-2 text-[11px]">
                        <span className="chip whitespace-normal normal-case text-hud-line">
                          {message.response.mode === 'aggregate' || !message.response.retrieved
                            ? message.response.summary_line || 'sin eventos recuperados'
                            : `${message.response.retrieved} eventos recuperados`}
                        </span>
                        <span className="chip whitespace-normal normal-case text-[var(--text-dim)]">
                          confianza {message.response.confidence}
                        </span>
                        <span className="chip whitespace-normal normal-case text-[var(--text-dim)]">
                          {message.response.engine === 'llm'
                            ? `modelo ${message.response.model}`
                            : message.response.engine === 'streaming'
                              ? 'generando respuesta…'
                              : 'motor local determinista'}
                        </span>
                        {message.response.warnings.includes('baja_confianza') && (
                          <span className="chip whitespace-normal normal-case text-[#ffb020]">
                            baja similitud
                          </span>
                        )}
                        {/* Cortes de intención (tema ajeno, inyección,
                            saludo): viajan en `warnings` y se etiquetan aquí,
                            no en el evento `fallback`. */}
                        {message.response.warnings
                          .filter(w => w !== 'baja_confianza' && !!WARNING_LABEL[w])
                          .map(warning => (
                            <span
                              key={warning}
                              className="chip whitespace-normal normal-case text-[#ffb020]"
                            >
                              {WARNING_LABEL[warning]}
                            </span>
                          ))}
                        {message.warning && (
                          <span className="chip whitespace-normal normal-case text-[#ffb020]">
                            {WARNING_LABEL[message.warning] ?? message.warning}
                          </span>
                        )}
                      </div>

                      {message.response.evidence.length > 0 && (
                        <>
                          {/* Evidencia débil colapsada: si la similitud no
                              alcanzó el umbral, las tarjetas no aportan —
                              se ofrecen tras un botón con la mejor
                              similitud en vez de inundar el hilo. */}
                          {message.response.warnings.includes('baja_confianza') &&
                            !message.showWeak && (
                              <button
                                onClick={() =>
                                  setMessages(prev =>
                                    prev.map(m =>
                                      m.id === message.id
                                        ? { ...m, showWeak: true }
                                        : m,
                                    ),
                                  )
                                }
                                className="btn-hud w-full text-left"
                              >
                                {`ver coincidencias débiles (${Math.round(
                                  Math.max(
                                    ...message.response.evidence.map(e => e.relevance),
                                  ) * 100,
                                )}%)`}
                              </button>
                            )}
                          {(!message.response.warnings.includes('baja_confianza') ||
                            message.showWeak) && (
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
                        </>
                      )}

                      {/* Vía explícita de aprendizaje: solo con la respuesta ya
                          cerrada (no durante el streaming) y si hay pregunta. */}
                      {!message.streaming &&
                        message.question &&
                        message.text.trim() && (
                          <FeedbackControl
                            analysisId={analysisId}
                            question={message.question}
                            // Si hubo `warning`, el texto visible es el del motor
                            // determinista: no es un error del modelo que archivar.
                            badAnswer={message.warning ? '' : message.text}
                            saved={message.feedback === 'saved'}
                            onSaved={() =>
                              setMessages(prev =>
                                prev.map(m =>
                                  m.id === message.id
                                    ? { ...m, feedback: 'saved' as const }
                                    : m,
                                ),
                              )
                            }
                            onError={setError}
                          />
                        )}
                    </div>
                  )}
                </div>
              </motion.div>
            )
          })}

          {loading && !streamingVisible && (
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
            {messages.length > 0 && (
              <button
                type="button"
                onClick={() => void clearConversation()}
                disabled={loading}
                className="btn-hud ml-auto"
                title="Borra la conversación y la memoria multi-turno del asistente"
              >
                Limpiar conversación
              </button>
            )}
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
              placeholder={
                topHost
                  ? `Pregunta sobre los registros… p. ej. ¿qué pasó en ${topHost}?`
                  : 'Pregunta sobre los registros… p. ej. ¿qué hosts concentran los eventos críticos?'
              }
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
