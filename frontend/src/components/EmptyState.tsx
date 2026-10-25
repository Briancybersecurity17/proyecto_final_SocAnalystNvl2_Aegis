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
    <div className="p-8 text-center text-gray-500 bg-white rounded shadow">
      <p className="text-lg font-medium mb-2">Sin datos todavía</p>
      <p className="mb-4">{hint}</p>
      {cta && (
        <button
          type="button"
          onClick={() => setSection('upload' as Section)}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary/90 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
        >
          Ir a Carga de archivos
        </button>
      )}
    </div>
  )
}

export default EmptyState
