import React from 'react'
import { ResponsiveContainer, BarChart, Bar, XAxis, ReferenceArea } from 'recharts'
import { DashboardSection } from './DashboardSection'
import { useAnalysisStore } from '../store'
import { useFilterStore } from '../store/filters'
import {
  AXIS_TICK,
  LEVEL_COLORS,
  LEVEL_ORDER,
  TimePoint,
  buildTimeBuckets,
  useChartMotion,
} from '../charts'

/**
 * Etiquetas de los tramos cubiertos por la ventana `from`/`to` del store.
 *
 * `detail` usa espacio («2026-01-10 08:00») y el store escribe ISO con «T»:
 * se normalizan ambos lados con `.replace('T', ' ')` y se compara por
 * `detail`, ya las dos partes con el mismo formato. Sin ventana, o sin tramos
 * dentro de ella, devuelve vacío: no hay región que pintar.
 */
const labelsForRange = (points: TimePoint[], from: string | null, to: string | null): string[] => {
  if (!from && !to) return []
  const fromNorm = from ? from.replace('T', ' ') : null
  const toNorm = to ? to.replace('T', ' ') : null
  return points
    .filter(point => {
      const detail = point.detail.replace('T', ' ')
      return (!fromNorm || detail >= fromNorm) && (!toNorm || detail <= toNorm)
    })
    .map(point => point.label)
}

/**
 * Tira de tiempo: histograma global de los tramos del archivo.
 *
 * Es el control de la sección, no una visualización scopeda: lee el resumen
 * GLOBAL del store (`useAnalysisStore`), nunca el acotado por los filtros, de
 * modo que la tira sigue mostrando el histograma completo mientras el resto
 * del dashboard se estrecha. No emite ninguna petición (D6): solo deriva los
 * tramos con `buildTimeBuckets`, la misma regla que la serie temporal, y por
 * eso muestra las mismas lagunas (sin inventar datos).
 * Sin gestos: únicamente el render estático.
 */
export const TimelineBrush: React.FC = () => {
  const summary = useAnalysisStore((s) => s.summary)
  const from = useFilterStore((s) => s.from)
  const to = useFilterStore((s) => s.to)
  const chartMotion = useChartMotion()

  if (!summary) return null

  const { points } = buildTimeBuckets(summary)

  // Con menos de 2 tramos no se renderiza: un solo tramo no puede recortar
  // nada y el resto del dashboard sigue comportándose igual.
  if (points.length < 2) return null

  const rangeLabels = labelsForRange(points, from, to)

  return (
    <DashboardSection title="Línea de tiempo">
      <ResponsiveContainer width="100%" height={90}>
        <BarChart data={points} margin={{ top: 4, right: 8, left: 8, bottom: 4 }}>
          {/* Región sombreada de la ventana confirmada: del inicio del primer
              tramo cubierto al fin del último. Va antes de las barras para
              quedar por detrás (el orden de los hijos es el orden de pintado);
              sin tramos dentro no se pinta nada. */}
          {rangeLabels.length > 0 && (
            <ReferenceArea
              x1={rangeLabels[0]}
              x2={rangeLabels[rangeLabels.length - 1]}
              fill="#3b82f6"
              fillOpacity={0.18}
            />
          )}
          {/* Sin YAxis, sin rejilla y sin tooltip: es un mini-histograma;
              solo se etiquetan el primer y el último tramo. */}
          <XAxis dataKey="label" tick={AXIS_TICK} interval="preserveStartEnd" />
          {LEVEL_ORDER.map(level => (
            <Bar
              key={level}
              dataKey={level}
              stackId="tl"
              fill={LEVEL_COLORS[level]}
              isAnimationActive={chartMotion.isAnimationActive}
              animationDuration={chartMotion.duration}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </DashboardSection>
  )
}
