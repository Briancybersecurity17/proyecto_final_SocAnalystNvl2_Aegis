import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { FacetField, FilterParams, getFacets } from '../api/client'

/**
 * Cabecera de columna con AutoFilter estilo Excel: una flechita ▾ que abre
 * un desplegable con los valores del campo, su recuento y checkboxes
 * (multi-selección OR dentro de la columna, AND con el resto de filtros).
 *
 * Los valores viajan por `GET /facets?fields=<columna>` — el modo «pedido
 * explícito» del endpoint, que devuelve AQUELLA columna aunque la detección
 * automática la descarte por cardinalidad (Log_ID, Timestamp,
 * Event_Description…). La búsqueda (`value_q`) se manda al backend para que
 * alcance valores fuera del `top_n` sin servir la columna entera.
 *
 * El panel se portaliza a `document.body` con posición `fixed`: vive dentro
 * del contenedor con `overflow-auto` de la tabla y, sin portal, se recortaría
 * contra sus bordes. Al ser fixed hay que reposicionarlo al hacer scroll
 * (capturado en la fase de captura, para que también llegue el del
 * contenedor de la tabla, cuyo scroll no burbujea) y al redimensionar.
 */
type ColumnFilterProps = {
  analysisId: string
  /** Columna del archivo (nombre literal, también la clave de la faceta). */
  column: string
  /**
   * Etiqueta visible en la cabecera. Por defecto se muestra el nombre literal
   * de la columna («Source_IP»); quien ya tiene rótulos propios en la tabla
   * («IP origen») pasa el suyo sin cambiar la clave de la faceta, que sigue
   * siendo `column`.
   */
  label?: string
  /** Filtros vigentes: los conteos se sirven con el mismo contexto que /summary. */
  filters: FilterParams
  /** Valores marcados en ESTA columna (facets[column]). */
  selected: string[]
  onToggle: (value: string) => void
  onClear: () => void
}

/** Valores por respuesta: suficiente para categorías habitualmente cortas. */
const TOP_N = 100
/** Ancho del panel; se usa también para no salirse por el borde derecho. */
const PANEL_WIDTH = 264

const ColumnFilter: React.FC<ColumnFilterProps> = ({
  analysisId,
  column,
  label,
  filters,
  selected,
  onToggle,
  onClear,
}) => {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [field, setField] = useState<FacetField | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const active = selected.length > 0

  // Serialización estable de los filtros: los objetos del store cambian de
  // identidad en cada render y usarlos como dependencia refrescaría en bucle.
  const filtersKey = JSON.stringify(filters)

  // --- posición del panel ---------------------------------------------------
  const place = useCallback(() => {
    const rect = btnRef.current?.getBoundingClientRect()
    if (!rect) return
    // Clamped al viewport: una cabecera en el borde derecho de la tabla
    // (hecha scroll horizontal) no debe mandar el panel fuera de pantalla.
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - PANEL_WIDTH - 8))
    setPos({ top: rect.bottom + 2, left })
  }, [])

  useEffect(() => {
    if (!open) return
    place()
    const reposicionar = () => place()
    // Fase de captura: el scroll del contenedor de la tabla no burbujea, y
    // el thead sticky cambia su rect al desplazarse en horizontal.
    window.addEventListener('scroll', reposicionar, true)
    window.addEventListener('resize', reposicionar)
    return () => {
      window.removeEventListener('scroll', reposicionar, true)
      window.removeEventListener('resize', reposicionar)
    }
  }, [open, place])

  // --- cierre por clic fuera / Escape --------------------------------------
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const destino = e.target as Node
      if (panelRef.current?.contains(destino)) return
      if (btnRef.current?.contains(destino)) return // el botón conmuta solo
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Un cambio de análisis cierra el panel: los valores del anterior no
  // deben quedar en pantalla mientras llegan los del nuevo.
  useEffect(() => setOpen(false), [analysisId])

  // --- valores del campo ----------------------------------------------------
  // Se refresca al abrirse y con cada cambio de filtros o de búsqueda: los
  // conteos son estilo Splunk (el backend ignora el filtro de ESTE campo),
  // así que al marcar un valor los demás siguen ahí para poder cambiar.
  useEffect(() => {
    if (!open) return
    const ctrl = new AbortController()
    setLoading(true)
    setError('')
    // La búsqueda teclea a destello: 250 ms de pausa; un cambio de filtros
    // (checkbox) viaja ya, sin espera.
    const timer = window.setTimeout(
      async () => {
        try {
          const res = await getFacets(analysisId, filters, {
            fields: [column],
            topN: TOP_N,
            valueQ: search.trim(),
            signal: ctrl.signal,
          })
          if (ctrl.signal.aborted) return
          setField(res.fields[0] ?? null)
        } catch {
          if (ctrl.signal.aborted) return
          setError('No se pudieron cargar los valores')
        } finally {
          if (!ctrl.signal.aborted) setLoading(false)
        }
      },
      search.trim() ? 250 : 0,
    )
    return () => {
      window.clearTimeout(timer)
      ctrl.abort()
    }
    // filtersKey (no filters) es la dependencia que evita el bucle de renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, filtersKey, search, column, analysisId])

  const valores = field?.values ?? []
  const filtrando = search.trim() !== ''
  const hayMas = !filtrando && !!field && field.distinct > field.values.length

  return (
    <>
      <th scope="col" className="whitespace-nowrap">
        <button
          ref={btnRef}
          type="button"
          onClick={() => setOpen(o => !o)}
          aria-haspopup="true"
          aria-expanded={open}
          title={
            active
              ? `${label ?? column}: ${selected.length} valor(es) filtrando`
              : `Filtrar por ${label ?? column}`
          }
          className={`inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.12em] transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-hud-line ${
            open || active ? 'text-hud-line' : 'text-hud-fg hover:text-white'
          }`}
        >
          {label ?? column}
          <svg viewBox="0 0 10 6" className="h-1.5 w-2.5 shrink-0 fill-current" aria-hidden="true">
            <path d="M0 0l5 6 5-6z" />
          </svg>
        </button>
      </th>

      {open &&
        pos &&
        createPortal(
          <div
            ref={panelRef}
            style={{ position: 'fixed', top: pos.top, left: pos.left, width: PANEL_WIDTH }}
            className="z-50 border border-hud-soft bg-panel shadow-[0_12px_32px_rgba(0,0,0,0.65)]"
            data-column-filter={column}
          >
            <div className="border-b border-hud-soft px-3 py-2">
              <div className="hud-label mb-1.5 truncate" title={column}>
                {label ?? column}
              </div>
              <input
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Buscar valor…"
                className="hud-input w-full py-1.5 text-[12px]"
                autoFocus
              />
            </div>

            <div className="max-h-64 overflow-y-auto py-1">
              {loading && valores.length === 0 && (
                <p className="px-3 py-2 font-mono text-[11.5px] text-[var(--text-dim)]">
                  Cargando valores…
                </p>
              )}
              {error && (
                <p className="px-3 py-2 font-mono text-[11.5px] text-[#f97316]">{error}</p>
              )}
              {!error && !loading && valores.length === 0 && (
                <p className="px-3 py-2 font-mono text-[11.5px] text-[var(--text-dim)]">
                  {filtrando ? 'Ningún valor coincide con la búsqueda' : 'Sin valores que ofrecer'}
                </p>
              )}
              {valores.map(v => {
                const marcado = selected.includes(v.value)
                return (
                  <label
                    key={v.value}
                    className={`flex cursor-pointer items-center gap-2 px-3 py-1 transition-colors hover:bg-[rgba(31,200,255,0.07)] ${
                      marcado ? 'bg-[rgba(31,200,255,0.10)]' : ''
                    }`}
                    title={`${v.value} (${v.count})`}
                  >
                    <input
                      type="checkbox"
                      checked={marcado}
                      onChange={() => onToggle(v.value)}
                      className="shrink-0 accent-[#1fc8ff]"
                    />
                    <span className="truncate text-[12px] text-[var(--text)]">{v.value}</span>
                    <span className="ml-auto shrink-0 font-mono text-[11px] text-[var(--text-dim)]">
                      {v.count}
                    </span>
                  </label>
                )
              })}
              {hayMas && (
                <p className="px-3 pt-1 font-mono text-[10.5px] text-[var(--text-dim)]">
                  + {field!.distinct - valores.length} valores más (usá la búsqueda)
                </p>
              )}
            </div>

            <div className="flex items-center justify-between gap-2 border-t border-hud-soft px-3 py-1.5">
              <span className="hud-label truncate">
                {active ? `${selected.length} marcados` : 'Filtrar'}
              </span>
              <div className="flex shrink-0 gap-3">
                <button
                  type="button"
                  onClick={onClear}
                  disabled={!active}
                  className="font-mono text-[11px] text-hud-line transition-colors hover:underline disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Limpiar
                </button>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="font-mono text-[11px] text-[var(--text-dim)] transition-colors hover:text-white"
                >
                  Cerrar
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  )
}

export default ColumnFilter
