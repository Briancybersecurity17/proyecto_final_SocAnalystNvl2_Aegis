import React from 'react'
import { motion } from 'framer-motion'
import { Cell } from 'recharts'
import { glowVars } from '../charts'

/** Clave de dato seleccionada y color con el que debe brillar. */
export interface SelectionItem {
  key: string
  color: string
}

/**
 * Devuelve celdas `<Cell>` de Recharts listas para meter como hijos de cada
 * `<Bar>` (Recharts las lee vía `findAllByType` y aplica sus props a cada
 * segmento por índice).
 *
 * La celda elegida conserva su color y gana halo (`.chart-glow`) y contorno;
 * el resto se atenúa para que la mirada vaya a lo seleccionado. El halo se
 * aplica por clase, no por estilo en línea, para que la regla de
 * `prefers-reduced-motion` de styles/index.css pueda dejarlo fijo.
 *
 * `keepSeriesFill`: en barras apiladas el color lo pone cada serie, así que la
 * celda no toca `fill` (pintaría todos los tramos del mismo color); solo
 * marca con contorno y atenúa el resto. En barras de un solo color sí se
 * pinta el `fill`.
 */
export function selectionCells<T extends Record<string, unknown>>(
  items: T[],
  selectedKey: string | null,
  getKey: (item: T) => string,
  color: string,
  keepSeriesFill = false
): React.ReactNode[] {
  return items.map(item => {
    const key = getKey(item)
    const active = selectedKey === key
    const dimmed = selectedKey !== null && !active
    return (
      <Cell
        key={key}
        fill={keepSeriesFill ? undefined : color}
        fillOpacity={dimmed ? 0.3 : 1}
        stroke={
          active
            ? keepSeriesFill
              ? 'rgba(15,23,42,0.55)'
              : color
            : 'none'
        }
        strokeWidth={active ? 2 : 0}
        className={active ? 'chart-glow' : undefined}
        style={active ? glowVars(color) : undefined}
      />
    )
  })
}

export interface InsightRow {
  label: string
  value: string
}

/**
 * Tarjeta de detalle que aparece debajo del gráfico al seleccionar una parte
 * de él. Aporta el desglose completo del elemento elegido (además del
 * tooltip, que solo muestra el dato a secas) y un botón para deseleccionar.
 *
 * Todo lo que muestra se calcula del resumen del análisis: no inventa cifras.
 */
export const ChartInsight: React.FC<{
  title: string
  color: string
  value: React.ReactNode
  caption?: string
  rows?: InsightRow[]
  note?: string
  onClose: () => void
}> = ({ title, color, value, caption, rows = [], note, onClose }) => (
  <motion.div
    initial={{ opacity: 0, y: 8 }}
    animate={{ opacity: 1, y: 0 }}
    exit={{ opacity: 0, y: -6, transition: { duration: 0.14 } }}
    transition={{ duration: 0.22, ease: 'easeOut' }}
    className="mt-4 rounded-lg border p-4"
    style={{ borderColor: `${color}66`, background: `${color}12` }}
  >
    <div className="flex items-start justify-between gap-4">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide" style={{ color }}>
          {title}
        </p>
        <p className="text-2xl font-bold text-gray-900">{value}</p>
        {caption && <p className="text-xs text-gray-600">{caption}</p>}
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label="Quitar selección"
        className="rounded p-1 text-gray-500 transition-colors hover:bg-white hover:text-gray-800"
      >
        ✕
      </button>
    </div>

    {rows.length > 0 && (
      <dl
        className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t pt-3 sm:grid-cols-4"
        style={{ borderColor: `${color}33` }}
      >
        {rows.map(row => (
          <div key={row.label}>
            <dt className="text-[11px] uppercase tracking-wide text-gray-500">{row.label}</dt>
            <dd className="text-sm font-semibold text-gray-800">{row.value}</dd>
          </div>
        ))}
      </dl>
    )}

    {note && <p className="mt-3 text-xs leading-relaxed text-gray-600">{note}</p>}
  </motion.div>
)
