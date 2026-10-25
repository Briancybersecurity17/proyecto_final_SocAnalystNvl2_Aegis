import React, { useEffect, useState } from 'react'
import { getRagStatus } from '../api/client'

type ConnState = 'checking' | 'ok' | 'down'

const STYLE: Record<ConnState, { dot: string; label: string }> = {
  checking: { dot: 'bg-gray-300', label: 'Conectando con el backend…' },
  ok: { dot: 'bg-emerald-400', label: 'Backend conectado' },
  down: { dot: 'bg-rose-400', label: 'Sin conexión con el backend' },
}

/**
 * Estado real de la API en la cabecera: sondea `/api/rag/status` al montar y
 * cada 15 s. Si el backend cae, el analista lo ve en el momento en lugar de
 * descubrirlo al subir un archivo.
 */
const BackendStatus: React.FC = () => {
  const [state, setState] = useState<ConnState>('checking')

  useEffect(() => {
    let alive = true
    const check = async () => {
      try {
        await getRagStatus()
        if (alive) setState('ok')
      } catch {
        if (alive) setState('down')
      }
    }
    void check()
    const id = setInterval(check, 15000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  const { dot, label } = STYLE[state]

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full bg-white/15 px-3 py-1 text-xs font-medium text-white"
      title="Estado de la API (se comprueba cada 15 s)"
      aria-live="polite"
    >
      <span className={`h-2 w-2 rounded-full ${dot}`} />
      {label}
    </span>
  )
}

export default BackendStatus
