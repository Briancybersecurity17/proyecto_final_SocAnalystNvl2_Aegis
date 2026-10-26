import React from 'react'
import { useUiStore, Section } from '../store'

interface EmptyStateProps {
  /** Texto de ayuda específico de la sección. */
  hint: React.ReactNode
  /** Muestra el botón de atajo a «Carga de archivos» (por defecto, sí). */
  cta?: boolean
}

/**
 * Estado vacío compartido por todas las secciones: mismo aspecto y, cuando
 * tiene sentido, un atajo directo a la carga del archivo en lugar de pedir
 * que se busque el menú.
 */
export const EmptyState: React.FC<EmptyStateProps> = ({ hint, cta = true }) => {
  const setSection = useUiStore(state => state.setSection)

  return (
    <div className="panel">
      <div className="panel-body p-8 text-center">
        <p className="font-display text-base font-bold uppercase tracking-[0.16em] text-hud-muted">
          Sin datos todavía
        </p>
        <p className="mx-auto mb-5 mt-2 max-w-xl font-mono text-[13px] leading-relaxed text-[var(--text-dim)]">
          {hint}
        </p>
        {cta && (
          <button type="button" onClick={() => setSection('upload' as Section)} className="btn-hud btn-primary-hud">
            Ir a Carga de archivos
          </button>
        )}
      </div>
    </div>
  )
}

export default EmptyState
