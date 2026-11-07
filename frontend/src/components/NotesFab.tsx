import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'

/**
 * NotesFab — bloc de notas flotante («Notas de campo»).
 *
 * Botón circular abajo a la derecha que abre un panel con un único bloc:
 * para pegar al vuelo un evento visto en Correlaciones (o cualquier cifra)
 * y tenerlo a mano sin cambiarse de sección a copiar y pegar.
 *
 * Todo vive en el navegador (localStorage, con la misma tolerancia a modo
 * privado que persist.ts): nada viaja al backend ni al repo. Una sola nota
 * a propósito — YAGNI: el analista quiere un bloc, no un gestor de notas.
 *
 * Estilo: el marco firma del proyecto (.panel con borde neón y chamfer,
 * .panel-title con tick romboidal) para que el panel se lea como cualquier
 * otro panel de la app, y el botón hereda el glow de los botones HUD.
 */

const KEY = 'aegis.notes'

const loadNotes = (): string => {
  try {
    return window.localStorage.getItem(KEY) ?? ''
  } catch {
    return ''
  }
}

const saveNotes = (value: string): void => {
  try {
    window.localStorage.setItem(KEY, value)
  } catch {
    // sin almacenamiento (modo privado / cuota agotada): el bloc sigue
    // funcionando en memoria durante la sesión
  }
}

/** Mismo criterio que el handler global de Esc de App.tsx: con el foco en
 *  un campo editable la tecla la consume el editor, no la vista. */
const isEditable = (target: EventTarget | null): boolean => {
  const el = target as HTMLElement | null
  return (
    !!el &&
    (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' || el.isContentEditable)
  )
}

const CRITICAL_STYLE: React.CSSProperties = {
  borderColor: 'rgba(220, 38, 38, 0.7)',
  color: '#fca5a5',
  background: 'linear-gradient(180deg, rgba(220, 38, 38, 0.28), rgba(220, 38, 38, 0.06))',
}

const COPIED_STYLE: React.CSSProperties = {
  borderColor: 'rgba(16, 185, 129, 0.65)',
  color: '#6ee7b7',
}

export const NotesFab: React.FC = () => {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState<string>(loadNotes)
  const [copied, setCopied] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const copiedTimer = useRef<number | null>(null)
  const confirmTimer = useRef<number | null>(null)

  // Al abrir, el cursor entra directo al bloc: pegar es el uso principal.
  useEffect(() => {
    if (open) textareaRef.current?.focus()
  }, [open])

  // Esc cierra el panel solo con el foco «suelto»; en el textarea la
  // consumís escribiendo (la app global ya respeta los campos editables).
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isEditable(e.target)) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  // Limpieza de los timers de feedback al desmontar.
  useEffect(() => {
    return () => {
      if (copiedTimer.current !== null) window.clearTimeout(copiedTimer.current)
      if (confirmTimer.current !== null) window.clearTimeout(confirmTimer.current)
    }
  }, [])

  const onChange = (value: string): void => {
    setText(value)
    saveNotes(value)
    if (confirmClear) setConfirmClear(false)
  }

  const copy = async (): Promise<void> => {
    if (!text) return
    const feedback = (): void => {
      setCopied(true)
      if (copiedTimer.current !== null) window.clearTimeout(copiedTimer.current)
      copiedTimer.current = window.setTimeout(() => setCopied(false), 1400)
    }
    try {
      await navigator.clipboard.writeText(text)
      feedback()
    } catch {
      // Sin permiso de la API asíncrona (contexto sin activación de usuario):
      // copia con el método clásico sobre la selección y, si ese también
      // falla, queda el texto seleccionado para copiar a mano (Ctrl+C).
      textareaRef.current?.select()
      try {
        if (document.execCommand('copy')) feedback()
      } catch {
        // solo selección manual: ya quedó el texto marcado
      }
    }
  }

  // Limpiar es destructivo: el primer click arma la confirmación (3 s de
  // gracia), el segundo borra. Sin modales nativos, para no romper el estilo.
  const clear = (): void => {
    if (!text) return
    if (!confirmClear) {
      setConfirmClear(true)
      if (confirmTimer.current !== null) window.clearTimeout(confirmTimer.current)
      confirmTimer.current = window.setTimeout(() => setConfirmClear(false), 3000)
      return
    }
    if (confirmTimer.current !== null) window.clearTimeout(confirmTimer.current)
    setConfirmClear(false)
    onChange('')
  }

  const hasNotes = text.trim().length > 0

  return (
    <>
      {/* Panel del bloc: crece desde la esquina del botón (origin bottom-right) */}
      <AnimatePresence>
        {open && (
          <motion.div
            role="dialog"
            aria-label="Notas de campo"
            initial={{ opacity: 0, scale: 0.94, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 10 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            style={{ transformOrigin: 'bottom right' }}
            className="panel fixed bottom-24 right-6 z-50 w-[min(380px,calc(100vw-3rem))]"
          >
            <div className="panel-head">
              <span className="panel-title">Notas de campo</span>
              <button
                onClick={() => setOpen(false)}
                aria-label="Cerrar notas"
                className="flex h-7 w-7 items-center justify-center text-[var(--hud-muted)] transition-colors hover:text-[var(--hud-line)] focus:outline-none focus-visible:ring-2 focus-visible:ring-hud-line"
              >
                ✕
              </button>
            </div>

            <div className="p-4">
              <textarea
                ref={textareaRef}
                value={text}
                onChange={e => onChange(e.target.value)}
                placeholder="Pega aquí lo que quieras revisar después…"
                spellCheck={false}
                aria-label="Contenido de las notas"
                className="hud-input h-44 w-full resize-none leading-relaxed focus:border-hud-line focus:shadow-[0_0_10px_rgba(31,200,255,0.3)]"
              />

              <div className="mt-3 flex items-center justify-between gap-3">
                <span className="hud-label truncate" aria-live="polite">
                  {text.length} caracteres
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => void copy()}
                    disabled={!text}
                    className="btn-hud"
                    style={copied ? COPIED_STYLE : undefined}
                  >
                    {copied ? '✓ copiado' : 'Copiar'}
                  </button>
                  <button
                    onClick={clear}
                    disabled={!text}
                    className="btn-hud"
                    style={confirmClear ? CRITICAL_STYLE : undefined}
                  >
                    {confirmClear ? '¿Seguro?' : 'Limpiar'}
                  </button>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Botón flotante: siempre visible, sobre cualquier sección. Si hay
          notas guardadas y el panel está cerrado, un punto magenta avisa. */}
      <motion.button
        onClick={() => setOpen(o => !o)}
        aria-label={open ? 'Cerrar notas de campo' : 'Abrir notas de campo'}
        aria-expanded={open}
        whileTap={{ scale: 0.93 }}
        className="fixed bottom-6 right-6 z-50 flex h-14 w-14 items-center justify-center rounded-full border border-[rgba(31,200,255,0.65)] bg-[linear-gradient(180deg,rgba(31,200,255,0.22),rgba(10,5,24,0.92))] text-hud-fg shadow-[0_0_18px_rgba(31,200,255,0.45),inset_0_0_12px_rgba(31,200,255,0.18)] transition-shadow hover:shadow-[0_0_26px_rgba(31,200,255,0.7),inset_0_0_14px_rgba(31,200,255,0.3)] focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-void)] focus-visible:ring-hud-line"
      >
        <svg
          width="24"
          height="24"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          {/* documento con esquina plegada + renglones: el icono de bloc */}
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 3v5h5" />
          <path d="M9 13h6M9 17h4" />
        </svg>
        {!open && hasNotes && (
          <span
            aria-hidden="true"
            className="absolute right-1 top-1 h-2.5 w-2.5 animate-pulse rounded-full ring-2 ring-[var(--bg-void)]"
            style={{ backgroundColor: 'var(--magenta)' }}
          />
        )}
      </motion.button>
    </>
  )
}

export default NotesFab
