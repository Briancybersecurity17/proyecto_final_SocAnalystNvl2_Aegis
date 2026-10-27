import React, { useEffect, useRef, useState } from 'react'
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
  dayMonth,
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
 * Umbral en px para distinguir un clic de un arrastre: por debajo se
 * considera pulsación simple y confirma un solo tramo.
 */
const DRAG_THRESHOLD_PX = 4

/** Estado del arrastre en curso: tramos en `points` + origen del gesto en px. */
type DragState = {
  /** Coordenada de la pulsación, para medir la distancia del gesto. */
  startX: number
  startY: number
  /** Índice del tramo pulsado. */
  startIndex: number
  /** Índice del tramo bajo el puntero (mientras el gráfico lo resuelve). */
  currentIndex: number
}

/**
 * Ventana ISO de un tramo (D4): la rama la decide la granularidad (`hourly`),
 * nunca el texto del `detail`. Horario: `from` con «T» y `to` al cierre de la
 * hora; diario: `from` solo fecha y `to` al cierre del día (borde inclusivo
 * que `labelsForRange` normaliza y deja dentro de la ventana).
 */
const bucketIso = (point: TimePoint, hourly: boolean): { from: string; to: string } => {
  if (hourly) {
    const from = point.detail.replace(' ', 'T')
    return { from, to: `${from.slice(0, 13)}:59:59.999` }
  }
  return { from: point.detail, to: `${point.detail}T23:59:59.999` }
}

/**
 * Tira de tiempo: histograma global de los tramos del archivo.
 *
 * Es el control de la sección, no una visualización scopeda: lee el resumen
 * GLOBAL del store (`useAnalysisStore`), nunca el acotado por los filtros, de
 * modo que la tira sigue mostrando el histograma completo mientras el resto
 * del dashboard se estrecha. No emite ninguna petición durante el gesto (D6):
 * solo deriva los tramos con `buildTimeBuckets`, la misma regla que la serie
 * temporal, y por eso muestra las mismas lagunas (sin inventar datos).
 * Gestos: el arrastre acota de tramo a tramo y fija `from`/`to` únicamente
 * al soltar; un clic (< 4 px) selecciona un solo tramo con la misma derivación.
 */
export const TimelineBrush: React.FC = () => {
  const summary = useAnalysisStore((s) => s.summary)
  const from = useFilterStore((s) => s.from)
  const to = useFilterStore((s) => s.to)
  const setRange = useFilterStore((s) => s.setRange)
  const chartMotion = useChartMotion()

  // Los buckets se calculan siempre para que los oyentes globales (registrados
  // en montaje) puedan leerlos por ref sin tocar el orden de los hooks.
  const buckets = summary ? buildTimeBuckets(summary) : null
  const points = buckets?.points ?? []
  const hourly = buckets?.hourly ?? false

  const dragRef = useRef<DragState | null>(null)
  const pointsRef = useRef<TimePoint[]>(points)
  const hourlyRef = useRef(hourly)
  const containerRef = useRef<HTMLDivElement | null>(null)
  /** Región que se pinta mientras el usuario arrastra (de tramo a tramo). */
  const [dragRange, setDragRange] = useState<{ x1: string; x2: string } | null>(null)

  // Mantén vivos los datos a los que acceden los oyentes globales: ellos se
  // registran una sola vez y no deben arrastrar closures obsoletos.
  useEffect(() => {
    pointsRef.current = points
    hourlyRef.current = hourly
  })

  // Gestor global del arrastre (Review Focus 2): `mousemove`/`mouseup` sobre
  // `window` para que el gesto se complete aunque el puntero salga del
  // `<svg>`, con limpieza en el `unmount` para no dejar oyentes huérfanos.
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const drag = dragRef.current
      if (!drag) return
      // Sin ningún botón pulsado el mouseup se perdió fuera de la página (se
      // soltó sobre DevTools o con alt-tab): se cancela el gesto para que no
      // reanude la región ni pueda commitear un `setRange` en un clic
      // posterior. El arrastre en curso mantiene el botón pulsado (buttons≠0).
      if (e.buttons === 0) {
        dragRef.current = null
        setDragRange(null)
        return
      }
      const pts = pointsRef.current
      const rect = containerRef.current?.getBoundingClientRect()
      if (!rect || !pts.length) return
      // Dentro de la tira el propio gráfico resuelve el tramo exacto (su
      // `onMouseMove` llega antes, al burbujear); fuera se estima por la
      // posición horizontal para que el arrastre siga respondiendo en los
      // costados y se complete igual al soltar.
      if (e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom) return
      const ratio = (e.clientX - rect.left) / rect.width
      const index = Math.min(pts.length - 1, Math.max(0, Math.round(ratio * (pts.length - 1))))
      if (index === drag.currentIndex || !pts[index]) return
      drag.currentIndex = index
      setDragRange({
        x1: pts[Math.min(drag.startIndex, index)].label,
        x2: pts[Math.max(drag.startIndex, index)].label,
      })
    }

    const onUp = (e: MouseEvent) => {
      const drag = dragRef.current
      if (!drag) return
      dragRef.current = null
      setDragRange(null)
      const pts = pointsRef.current
      if (!pts.length) return
      // Umbral de 4 px: por debajo es un clic y confirma el tramo pulsado.
      const moved =
        Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) >= DRAG_THRESHOLD_PX
      const [a, b] = moved
        ? [Math.min(drag.startIndex, drag.currentIndex), Math.max(drag.startIndex, drag.currentIndex)]
        : [drag.startIndex, drag.startIndex]
      const first = pts[a]
      const last = pts[b]
      if (!first || !last) return
      // Única escritura del gesto (D6): una sola llamada al soltar, que es
      // también la única petición de `/summary` que dispara el arrastre.
      const startIso = bucketIso(first, hourlyRef.current)
      const endIso = bucketIso(last, hourlyRef.current)
      setRange(startIso.from, endIso.to)
    }

    // Un press nuevo con un gesto aún vivo significa que su mouseup nunca
    // llegó a la página: se cancela en fase de captura de `window` (previa a
    // los handlers de Recharts, que se registran en la raíz de React) para
    // que ese gesto viejo no pueda commitear un `setRange` en un clic
    // posterior; si el press es sobre la tira, `startDrag` instala acto
    // seguido el gesto fresco. No se cancela en `blur` a propósito: acercar
    // el puntero a las DevTools para inspeccionar no debe abortar el arrastre.
    const cancelStaleDrag = () => {
      if (!dragRef.current) return
      dragRef.current = null
      setDragRange(null)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    window.addEventListener('mousedown', cancelStaleDrag, true)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      window.removeEventListener('mousedown', cancelStaleDrag, true)
    }
  }, [])

  if (!summary) return null

  // Con menos de 2 tramos no se renderiza: un solo tramo no puede recortar
  // nada y el resto del dashboard sigue comportándose igual.
  if (points.length < 2) return null

  const rangeLabels = labelsForRange(points, from, to)

  /** Pulsación sobre un tramo: inicia el gesto y pinta su región. */
  const startDrag = (info: { activeLabel?: string }, e: React.MouseEvent): void => {
    // Sin preventDefault el navegador seleccionaría el texto del eje al
    // arrastrar; la info de Recharts (activo bajo el cursor) no se ve afectada.
    e.preventDefault()
    const index = info.activeLabel ? points.findIndex(p => p.label === info.activeLabel) : -1
    // Pulsación fuera del área de trazado (o sin tramo que resaltar): no
    // inicia gesto; el resto de la página sigue comportándose con normalidad.
    if (index < 0) return
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      startIndex: index,
      currentIndex: index,
    }
    setDragRange({ x1: points[index].label, x2: points[index].label })
  }

  /** Movimiento sobre el gráfico: el tramo bajo el puntero manda la región. */
  const trackDrag = (info: { activeLabel?: string }): void => {
    const drag = dragRef.current
    // Sin tramo resuelto (eje, margen) se conserva el último conocido.
    if (!drag || !info.activeLabel) return
    const index = points.findIndex(p => p.label === info.activeLabel)
    if (index < 0 || index === drag.currentIndex) return
    drag.currentIndex = index
    setDragRange({
      x1: points[Math.min(drag.startIndex, index)].label,
      x2: points[Math.max(drag.startIndex, index)].label,
    })
  }

  return (
    <DashboardSection title="Línea de tiempo">
      {/* El contenedor da el rectángulo con el que el oyente global decide si
          el puntero siguió dentro de la tira o se fue fuera del `<svg>`. */}
      <div ref={containerRef} className="cursor-crosshair select-none">
        <ResponsiveContainer width="100%" height={90}>
          <BarChart
            data={points}
            margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
            onMouseDown={startDrag}
            onMouseMove={trackDrag}
          >
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
            {/* Región del arrastre en curso: exactamente de tramo a tramo
                (labels min/max del estado local), sin tocar el store. */}
            {dragRange && (
              <ReferenceArea
                x1={dragRange.x1}
                x2={dragRange.x2}
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
            {/* Etiquetas de los extremos de la ventana confirmada con
                `dayMonth`. Van en un ReferenceArea posterior a las barras
                (relleno transparente) para quedar pintadas por encima: el
                relleno va antes, por detrás. */}
            {rangeLabels.length > 0 && (
              <ReferenceArea
                x1={rangeLabels[0]}
                x2={rangeLabels[rangeLabels.length - 1]}
                fill="none"
                label={{
                  content: props => {
                    // El viewBox de un ReferenceArea es siempre el rect
                    // cartesiano del área (el tipo común de Recharts añade
                    // la variante polar, que aquí no aplica).
                    const { x = 0, y = 0, width = 0 } =
                      (props.viewBox as { x?: number; y?: number; width?: number } | undefined) ?? {}
                    return (
                      <g fontSize={10} fill="var(--text-dim)" style={{ fontFamily: 'var(--font-mono)' }}>
                        <text x={x + 4} y={y + 12}>
                          {dayMonth(rangeLabels[0])}
                        </text>
                        <text x={x + width - 4} y={y + 12} textAnchor="end">
                          {dayMonth(rangeLabels[rangeLabels.length - 1])}
                        </text>
                      </g>
                    )
                  },
                }}
              />
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
      {/* Nota de uso del gesto, en el patrón mono de las notas existentes. */}
      <p className="mt-2 font-mono text-xs text-dim">
        Arrastrá un tramo para acotar el análisis; con un clic seleccionás un solo tramo.
      </p>
    </DashboardSection>
  )
}
