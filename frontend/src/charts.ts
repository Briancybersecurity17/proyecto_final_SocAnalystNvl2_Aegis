/**
 * charts.ts
 * ---------
 * Sistema compartido de estilos para todos los gráficos (Recharts): colores de
 * criticidad, ejes, rejilla, tooltip y tipografía numérica.
 *
 * Está en un solo archivo a propósito: antes cada gráfico improvisaba sus
 * propios `stroke`, `contentStyle` y formatos, y el dashboard se veía
 * discontinuo. Aquí se define una vez y se importa donde haga falta.
 *
 * Reglas que respeta:
 *  - Los colores de criticidad son semánticos y no se tocan (son los mismos
 *    que usan tarjetas, tablas y acordeones de la app).
 *  - El eje y la rejilla son grises neutros: nunca compiten con los datos.
 *  - Las animaciones solo se activan si el sistema no pide menos movimiento
 *    (accesibilidad, ver `usePrefersReducedMotion` en motion.ts).
 */

import type { CSSProperties, SVGProps } from 'react'
import { usePrefersReducedMotion } from './motion'

/** Colores fijos por nivel de criticidad (orden: crítico → bajo). */
export const LEVEL_COLORS: Record<string, string> = {
  critico: '#dc2626',
  alto: '#f97316',
  medio: '#facc15',
  bajo: '#10b981',
}

export const LEVEL_ORDER = ['critico', 'alto', 'medio', 'bajo'] as const

export const LEVEL_LABELS: Record<string, string> = {
  critico: 'Crítico',
  alto: 'Alto',
  medio: 'Medio',
  bajo: 'Bajo',
}

/** Trazo de los ejes: gris azulado legible sobre el fondo del HUD. */
export const AXIS = '#8494b8'

/** Rejilla: cian muy tenue para que no compita con las series. */
export const GRID = 'rgba(31,200,255,0.12)'

/** Tooltip común: tarjeta oscura con borde cian y brillo suave. */
export const TOOLTIP_STYLE: CSSProperties = {
  border: '1px solid rgba(31,200,255,0.55)',
  borderRadius: 2,
  background: '#0a0518',
  color: '#dce6f5',
  boxShadow: '0 0 16px rgba(31,200,255,0.35)',
  fontSize: 12,
  fontFamily: "'JetBrains Mono', 'Courier New', monospace",
}

/**
 * Estilo de cada línea («item») del tooltip.
 *
 * Hace falta porque Recharts pinta el texto de cada ítem con un estilo INLINE
 * (`color: entry.color || '#000'`, ver DefaultTooltipContent). En los gráficos
 * de ejes eso sale bien: el payload trae el color de la serie. Pero el Pie
 * arma su payload a mano { name, value, payload, dataKey, type } SIN `color`,
 * así que todo su tooltip cae en el fallback '#000': texto negro sobre el
 * fondo #0a0518 → se ve como una mancha gris apagada, casi ilegible.
 * El `color` de TOOLTIP_STYLE no lo salva porque vive en el contenedor y el
 * `<li>` tiene su propio estilo inline (la herencia pierde). `itemStyle` se
 * aplica después del fallback, así que gana.
 */
export const TOOLTIP_ITEM_STYLE: CSSProperties = { color: '#dce6f5' }

/** Formato de eje/tick con separador de miles según locale del navegador. */
export const AXIS_TICK = { fill: AXIS, fontSize: 12 }

/** Convierte cualquier valor recibido en un entero seguro para gráficos. */
export const toCount = (value: unknown): number =>
  Number.isFinite(Number(value)) ? Number(value) : 0

/** Porcentaje a 1 decimal sin divisiones por cero. */
export const pct = (part: number, total: number): string =>
  total > 0 ? ((part / total) * 100).toFixed(1) : '0.0'

/**
 * Formato de un porcentaje ya calculado en el backend (viene redondeado a 1
 * decimal): 13.6 → «13.6», 1 → «1.0». Sin esto convivían «1%» y «13.6%» en la
 * misma tabla, con y sin decimal.
 */
export const fmtPct = (value: unknown): string => {
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(1) : '0.0'
}

/**
 * Entero con separador de miles: el mismo formato en tarjetas, tooltips y
 * tablas (antes unas mostraban «10000 registros» y otras «10,000 eventos»).
 */
export const fmtCount = (value: unknown): string => toCount(value).toLocaleString('es-ES')

/** Duración de la entrada de un gráfico (ms). */
export const CHART_DURATION = 800

/**
 * Estado de animación de las gráficas: activa por defecto (entradas suaves de
 * 0.2–0.3 s, igual que el resto de la UI) y desactivada si el sistema operativo
 * pide reducir el movimiento.
 *
 * `duration` es la entrada de Recharts; con `CHART_STAGGER` entre series se
 * consigue que los tramos apilados entren escalonados (crítico primero).
 */
export const useChartMotion = (): { isAnimationActive: boolean; duration: number } => ({
  isAnimationActive: !usePrefersReducedMotion(),
  duration: CHART_DURATION,
})

/** Retardo entre series apiladas (ms): primero el nivel más crítico. */
export const CHART_STAGGER = 110

/**
 * Cursor de los gráficos de barras: banda azul muy tenue con borde punteado
 * en lugar del rectángulo gris sólido que Recharts dibuja por defecto.
 */
export const BAR_CURSOR: SVGProps<SVGElement> = {
  fill: 'rgba(31,200,255,0.08)',
  stroke: 'rgba(31,200,255,0.45)',
  strokeDasharray: '4 4',
}

/** Cursor de áreas y líneas: vertical punteada en el cian del HUD. */
export const LINE_CURSOR: SVGProps<SVGElement> = {
  stroke: '#1fc8ff',
  strokeDasharray: '4 4',
  strokeOpacity: 0.6,
}

/**
 * Variables de la sección seleccionada: aporta el color con el que brilla el
 * halo. La animación vive en `.chart-glow` (styles/index.css), de modo que si
 * el sistema pide menos movimiento el halo se queda fijo en vez de latir.
 */
export const glowVars = (color: string): CSSProperties =>
  ({ ['--chart-glow']: color }) as CSSProperties

/**
 * Estado que Recharts entrega al pulsar un gráfico de ejes (`onClick`).
 * `P` describe la fila pulsada, que varía según el gráfico.
 */
export type ChartClickState<P = Record<string, unknown>> = {
  activeLabel?: string
  activePayload?: Array<{ payload?: P }>
}
