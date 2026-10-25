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

import type { CSSProperties } from 'react'
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

/** Trazo de los ejes: gris medio, legible sobre blanco. */
export const AXIS = '#64748b'

/** Rejilla: gris muy tenue para que no compita con las series. */
export const GRID = '#e2e8f0'

/** Tooltip común: tarjeta blanca con borde suave y sombra discreta. */
export const TOOLTIP_STYLE: CSSProperties = {
  border: '1px solid #cbd5e1',
  borderRadius: 8,
  background: '#fff',
  boxShadow: '0 8px 24px rgba(15,23,42,.12)',
  fontSize: 12,
}

/** Formato de eje/tick con separador de miles según locale del navegador. */
export const AXIS_TICK = { fill: AXIS, fontSize: 12 }

/** Convierte cualquier valor recibido en un entero seguro para gráficos. */
export const toCount = (value: unknown): number =>
  Number.isFinite(Number(value)) ? Number(value) : 0

/** Porcentaje a 1 decimal sin divisiones por cero. */
export const pct = (part: number, total: number): string =>
  total > 0 ? ((part / total) * 100).toFixed(1) : '0.0'

/**
 * Estado de animación de las gráficas: activa por defecto (entradas suaves de
 * 0.2–0.3 s, igual que el resto de la UI) y desactivada si el sistema operativo
 * pide reducir el movimiento.
 */
export const useChartMotion = (): { isAnimationActive: boolean } => ({
  isAnimationActive: !usePrefersReducedMotion(),
})
