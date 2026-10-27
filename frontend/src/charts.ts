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
import type { AnalysisSummary } from './store'

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

// --- bucketing temporal compartido -----------------------------------------

/**
 * "2026-10-23 07:00" → "23/10 07:00" (etiqueta corta para el eje).
 * Con `withYear` añade el año: sin él, datos de dos años distintos producían
 * etiquetas idénticas y el total del tooltip se pisaba entre tramos.
 */
export const hourLabel = (stamp: string, withYear = false): string => {
  const [date, time] = stamp.split(' ')
  const [year, month, day] = date.split('-')
  const base = `${Number(day)}/${Number(month)} ${time}`
  return withYear ? `${base}/${year.slice(2)}` : base
}

/**
 * "2026-01-12" → "12/01" (chip de la ventana temporal). Si el valor no trae
 * una fecha ISO reconocible se muestra tal cual: el chip nunca rompe el render.
 */
export const dayMonth = (iso: string): string => {
  const [, month, day] = iso.split('-')
  return month && day ? `${Number(day)}/${Number(month)}` : iso
}

/** Un punto de la serie temporal, ya por hora ya por día. */
export type TimePoint = {
  label: string
  detail: string
  total: number
  critico: number
  alto: number
  medio: number
  bajo: number
}

/**
 * Series temporales del resumen (horaria y diaria) junto con la granularidad
 * elegida para graficar.
 *
 * Una sola regla para la serie del dashboard y para la futura tira temporal
 * (`TimelineBrush`, spec §3.2 D2): ambos consumen `buildTimeBuckets` y, por
 * lo tanto, ven exactamente los mismos tramos, etiquetas y valores.
 */
export type TimeBuckets = {
  hourlyData: TimePoint[]
  dailyData: TimePoint[]
  hourly: boolean
  hourlyTooLong: boolean
  points: TimePoint[]
}

/** Máximo de tramos horarios que se grafican antes de caer a la serie diaria. */
export const HOURLY_MAX_POINTS = 400

/**
 * Bucketing temporal del resumen: **una sola regla** para la serie del
 * dashboard y para la futura tira temporal (spec §3.2 D2).
 *
 * Devuelve ambas series más la granularidad efectiva (`hourly`,
 * `hourlyTooLong`) y `points`, que es la serie que hay que graficar.
 */
export const buildTimeBuckets = (
  summary: Pick<AnalysisSummary, 'events_per_hour_by_level' | 'events_per_day_by_level'>
): TimeBuckets => {
  // Serie horaria real (fecha+hora presentes en los timestamps del archivo).
  // Si hay varios tramos se usa como eje principal: da mucha más resolución
  // que la agregación diaria y no se inventa ningún dato.
  const hourlyStamps = Object.keys(summary.events_per_hour_by_level ?? {})
  // Con datos de más de un año la etiqueta corta se queda sin año y dos
  // tramos distintos colisionarían (mismo «23/10 07:00»).
  const hourlyMultiYear = new Set(hourlyStamps.map(s => s.slice(0, 4))).size > 1
  const hourlyData: TimePoint[] = Object.entries(summary.events_per_hour_by_level ?? {})
    .map(([stamp, levels]) => {
      const critico = toCount(levels.critico)
      const alto = toCount(levels.alto)
      const medio = toCount(levels.medio)
      const bajo = toCount(levels.bajo)
      return {
        label: hourLabel(stamp, hourlyMultiYear),
        detail: stamp,
        critico,
        alto,
        medio,
        bajo,
        total: critico + alto + medio + bajo,
      }
    })
    .sort((a, b) => a.detail.localeCompare(b.detail))

  const dailyData: TimePoint[] = Object.entries(summary.events_per_day_by_level ?? {})
    .map(([date, levels]) => {
      const critico = toCount(levels.critico)
      const alto = toCount(levels.alto)
      const medio = toCount(levels.medio)
      const bajo = toCount(levels.bajo)
      return {
        label: date,
        detail: date,
        critico,
        alto,
        medio,
        bajo,
        total: critico + alto + medio + bajo,
      }
    })
    .sort((a, b) => a.detail.localeCompare(b.detail))

  // Granularidad efectiva: hora si el backend la aporta con varios tramos;
  // si no, día (y si solo hay un día, se avisa en vez de fingir una tendencia)
  // Archivos que abarcan cientos de días generan miles de tramos horarios: el
  // eje deja de ser legible y el gráfico se resiente. Se cae a la serie
  // DIARIA, que es la misma información agregada por día (dato real del
  // archivo, calculado por el backend; no se descarta ni se inventa nada).
  const hourlyTooLong = hourlyData.length > HOURLY_MAX_POINTS
  const hourly = hourlyData.length > 1 && !hourlyTooLong
  const points = hourly ? hourlyData : dailyData

  return { hourlyData, dailyData, hourly, hourlyTooLong, points }
}
