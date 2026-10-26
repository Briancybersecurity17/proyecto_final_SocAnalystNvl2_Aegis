import React, { useEffect, useState } from 'react'
import { getRagStatus } from '../api/client'

type ConnState = 'checking' | 'ok' | 'down'

const STYLE: Record<ConnState, { dot: string; label: string; color: string }> = {
  checking: { dot: 'bg-hud-muted', label: 'Conectando con el backend…', color: '#7280a3' },
  ok: { dot: 'bg-emerald-400', label: 'Backend conectado', color: '#10b981' },
  down: { dot: 'bg-rose-500', label: 'Sin conexión con el backend', color: '#f87171' },
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

  const { dot, label, color } = STYLE[state]

  return (
    <span
      className="chip"
      style={{ color, background: `${color}14`, boxShadow: `0 0 10px ${color}33` }}
      title="Estado de la API (se comprueba cada 15 s)"
      aria-live="polite"
    >
      <span className={`h-2 w-2 rounded-full ${dot}`} />
      {label}
    </span>
  )
}

export default BackendStatus
