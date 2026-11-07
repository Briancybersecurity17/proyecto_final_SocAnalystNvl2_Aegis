# Línea de tiempo con brush (sub‑proyecto 2) — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** El analista arrastra un tramo sobre una línea de tiempo y eso fija la ventana temporal `from`/`to` en el store de filtros, recortando todo el análisis con el motor de filtros ya existente.

**Architecture:** Tira dedicada `TimelineBrush` alimentada por el resumen **global** (control) sobre la serie existente (visualización scopeda), bucketing compartido extraído a `charts.ts`, commit de la ventana al soltar el puntero, semántica `to` = fin de tramo con milisegundos. Sin cambios de backend (salvo un test que fija la semántica ya existente).

**Tech Stack:** React + TypeScript + Vite, Zustand, Recharts; FastAPI + pandas + pytest (solo tests). Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-10-27-linea-de-tiempo-brush-design.md` (el plan argumenta desde la spec; quien implementa lee ambas)

## Global Constraints

- Sin dependencias nuevas en el frontend. Comentarios en español.
- Red de regresión sub‑1 intacta: `pytest` (44 tests existentes) + el test nuevo del Task 1; `/rows` sin filtros sigue siendo `page=1&page_size=25&q=&level=&suspicious=false&result=` byte‑idéntica.
- D6: sin filtros activos ⇒ **cero peticiones adicionales**; con filtros ⇒ **una sola petición por cambio** (el commit es al soltar el puntero).
- El navegador nunca recibe más de 200 filas por petición; `level` solo admite `critico|alto|medio|bajo`.
- Backend y los 4 gráficos existentes sin cambios (el único toque backend es el test del Task 1; la serie solo pasa a consumir el helper compartido con idéntico comportamiento).
- Gate verde por tarea: `pytest` + `npx tsc --noEmit` (exit 0) + `npm run build` (exit 0, solo aviso preexistente de chunk >500 kB), ejecutados desde `frontend/`.
- Servidores para el recorrido: Vite `:5300` (proxy `/api` → `:8500`), uvicorn `:8500` con `--reload`.
- Los Excel de prueba se generan con pandas en scripts de `%TEMP%` (patrón del sub‑proyecto 1) y se suben por la UI.

## Review Focus

Estas cinco son las que la spec implica pero que ningún test unitario cubre; cada una queda anclada al task que posee el código:

1. **`to` a medianoche excluye el último día** → Task 1: test unitario con evento en `23:59:59.999`.
2. **Arrastre que termina fuera del `<svg>`** (mouseup no capturado → selección a medias o listeners colgados) → Task 6: `mousemove`/`mouseup` en `window` + paso de recorrido que arrastra fuera del gráfico.
3. **Bucketing de la tira diverge del de la serie** (dos reglas, etiquetas distintas) → Task 2 (helper único) + Task 5: comparar primeras/últimas etiquetas tira vs serie en el recorrido.
4. **Comportamiento de peticiones**: drag emite varias `/summary`, página>1 emite dos `/rows`, o «Limpiar filtros» no mata el debounce de `q` → Tasks 6/4/3: medición con Resource Timing en cada recorrido.
5. **`ReferenceArea` en eje de categorías resalta de más o desalineado** (limitación conocida, `Dashboard.tsx:282-287`) → Task 6: verificar visualmente que la región cubre exactamente los tramos elegidos.

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `backend/tests/test_filters.py` | +1 test: semántica `to` con milisegundos (Task 1) |
| `frontend/src/charts.ts` | +`hourLabel`, `dayMonth`, `TimePoint`, `buildTimeBuckets`, `HOURLY_MAX_POINTS` (Tasks 2 y 6) |
| `frontend/src/components/Dashboard.tsx` | consume `buildTimeBuckets`; monta `<TimelineBrush />` (Tasks 2 y 5) |
| `frontend/src/store/filters.ts` | +`clearEpoch`; doc de ventana ISO datetime (Tasks 3 y 6) |
| `frontend/src/components/DataTable.tsx` | guardia de epoch en debounce de `q`; un solo `/rows` por cambio (Tasks 3 y 4) |
| `frontend/src/components/TimelineBrush.tsx` | la tira: histograma global, región de ventana, gestos (Tasks 5 y 6) |

---

### Task 1: Test que fija la semántica `to` = fin de tramo

**Files:**
- Modify: `backend/tests/test_filters.py` (añadir al final)

**Interfaces:**
- Consumes: `FilterParams(from_dt, to_dt, …)` y `apply_filters(df, p)` existentes; fixture/patrón de `df` con pandas directo ya usado en el archivo.

- [x] **Step 1: Escribir el test de caracterización**

```python
def test_ventana_to_fin_de_tramo_cubre_el_dia_completo():
    df = pd.DataFrame({
        "Log_ID": [1, 2, 3, 4],
        "Timestamp": pd.to_datetime([
            "2026-01-12 00:00:00",
            "2026-01-12 12:00:00",
            "2026-01-12 23:59:59.999",
            "2026-01-13 00:00:00",
        ]),
    })
    out = apply_filters(
        df,
        FilterParams(
            from_dt=pd.Timestamp("2026-01-12"),
            to_dt=pd.Timestamp("2026-01-12 23:59:59.999"),
        ),
    )
    assert sorted(out["Log_ID"].tolist()) == [1, 2, 3]
```

- [x] **Step 2: Ejecutar solo ese test**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_filters.py::test_ventana_to_fin_de_tramo_cubre_el_dia_completo -v`
Expected: **PASS** — es un test de caracterización: fija el comportamiento que `_parse_ventana` (`pd.to_datetime`) ya implementa. Si FALLA, el parseo no acepta datetime con milisegundos: arreglar `_parse_ventana`, nunca el test.

- [x] **Step 3: Suite completa**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests -q`
Expected: **45 passed** (44 existentes + 1 nuevo), 1 warning starlette preexistente.

- [x] **Step 4: Commit**

```bash
git add backend/tests/test_filters.py
git commit -m "test: fija la semantica de to con milisegundos (fin de tramo)"
```

---

### Task 2: Bucketing temporal compartido en `charts.ts`

Refactor puro: la lógica de serie se muda a `charts.ts` para que la futura tira y la serie usen **una sola regla**. Cero cambio de comportamiento.

**Files:**
- Modify: `frontend/src/charts.ts`
- Modify: `frontend/src/components/Dashboard.tsx` (líneas ~56‑70, 79‑88, 188‑241 y los imports)

**Interfaces:**
- Consumes: `AnalysisSummary` (`frontend/src/store`) — `charts.ts` añade `import type { AnalysisSummary } from './store'` (sin ciclo: `store/index.ts` solo importa zustand).
- Produces (los Tasks 5 y 6 dependen de esto):
  - `export type TimePoint = { label: string; detail: string; total: number; critico: number; alto: number; medio: number; bajo: number }`
  - `export type TimeBuckets = { hourlyData: TimePoint[]; dailyData: TimePoint[]; hourly: boolean; hourlyTooLong: boolean; points: TimePoint[] }`
  - `export const buildTimeBuckets = (summary: Pick<AnalysisSummary, 'events_per_hour_by_level' | 'events_per_day_by_level'>): TimeBuckets`
  - `export const hourLabel = (stamp: string, withYear = false): string`
  - `export const dayMonth = (iso: string): string`
  - `export const HOURLY_MAX_POINTS = 400`

- [x] **Step 1: Mover `hourLabel`, `dayMonth` y `TimePoint` a `charts.ts`**

Corte literal desde `Dashboard.tsx` (comentarios JSDoc incluidos), con `export` delante. En `Dashboard.tsx` quedan importados de `'../charts'`.

- [x] **Step 2: Implementar `buildTimeBuckets` en `charts.ts`**

Encapsula el algoritmo actual de `Dashboard.tsx:188-241` **sin cambios**: construye `hourlyData`/`dailyData` (mapeo de niveles con `toCount` + `.sort` por `detail`), calcula `hourlyMultiYear` (años distintos → `hourLabel(stamp, true)`), `hourlyTooLong = hourlyData.length > HOURLY_MAX_POINTS`, `hourly = hourlyData.length > 1 && !hourlyTooLong` y `points = hourly ? hourlyData : dailyData`. El comment del spec (§3.2 D2) va en el JSDoc: una sola regla para serie y tira.

- [x] **Step 3: `Dashboard.tsx` consume el helper**

Reemplazar el bloque 188‑241 por:

```ts
const { hourlyData, dailyData, hourly, hourlyTooLong, points: timeData } =
  buildTimeBuckets(summary)
```

(tras el guard `if (!summary)`), y borrar los helpers/`TimePoint` locales. El resto del componente (`totalByLabel`, avisos, `hourlyTooLong`, renders) no cambia.

- [x] **Step 4: Verificar gate**

Run: `npx tsc --noEmit` y `npx vite build` (desde `frontend/`)
Expected: exit 0 y exit 0 (solo aviso preexistente).

- [x] **Step 5: Verificar en navegador que la serie no cambió**

Recargar `http://localhost:5300/` con `soc_sample.xlsx`: la sección «Actividad temporal por criticidad» muestra **exactamente** las mismas etiquetas y valores que antes (misma granularidad, mismos avisos).

- [x] **Step 6: Commit**

```bash
git add frontend/src/charts.ts frontend/src/components/Dashboard.tsx
git commit -m "refactor: bucketing temporal compartido (charts.ts) para serie y tira"
```

---

### Task 3: `clearEpoch` — carrera «Limpiar filtros» vs debounce de `q`

**Files:**
- Modify: `frontend/src/store/filters.ts`
- Modify: `frontend/src/components/DataTable.tsx` (líneas 98‑112)

**Interfaces:**
- Produces: `FilterState.clearEpoch: number` — `0` inicial; `+1` en `clearAll` y en `reset` (ambas son limpieza total; `reset` cubre el mismo rescate de `q` pendiente al cambiar de análisis).

- [x] **Step 1: Añadir `clearEpoch` al store**

Campo con doc en español en `FilterState`; `clearAll` y `reset` incrementan (`set(s => ({ ...EMPTY_FILTERS, clearEpoch: s.clearEpoch + 1 }))`).

- [x] **Step 2: Guardia en el debounce de `q` (`DataTable.tsx`)**

Al programar el timer capturar la época viva (`useFilterStore.getState().clearEpoch`); en el callback, antes de `setQ`, comparar con la época viva y **no hacer nada** si cambió. (Cinturón: el timer no reescribe un `q` ya limpiado.)

- [x] **Step 3: Vaciar el input al cambiar la época**

Efecto nuevo en `DataTable.tsx`: al cambiar `clearEpoch`, ejecutar
`setQInput('')` sin condición (solo se dispara en clear/reset, nunca al
teclear; esto cierra la ventana en la que el texto tecleado se queda en el
input con el store ya limpio). No tocar el efecto `prevQ` existente.

- [x] **Step 4: Verificar gate**

Run: `npx tsc --noEmit` y `npx vite build`
Expected: exit 0 y exit 0.

- [x] **Step 5: Recorrido de la carrera**

En la tabla: teclear «dc» y **sin esperar los 300 ms** pulsar «Limpiar filtros». Esperar 1 s:
Expected: input vacío, **sin** chip «Búsqueda», contador «12 de 12 registros»; en la consola, 0 errores.

- [x] **Step 6: Commit**

```bash
git add frontend/src/store/filters.ts frontend/src/components/DataTable.tsx
git commit -m "fix: clearEpoch cierra la carrera entre Limpiar filtros y el debounce de q"
```

---

### Task 4: Un solo `/rows` por cambio de filtros en página >1

**Files:**
- Modify: `frontend/src/components/DataTable.tsx` (efectos de fetch ~152‑172)

**Interfaces:**
- Consumes: `filtersKey` (serialización estable ya existente en el componente), estado `page`, callback `fetchRows` existente — **`fetchRows` no se toca**.

- [x] **Step 1: Evitar el fetch huérfano al resetear página**

Comportamiento a implementar (mecanismo a criterio del implementer; ref‑previo de `filtersKey` o bandera de reset pendiente): cuando `filtersKey` cambie y `page !== 1`, **saltar** el fetch de esa pasada — el efecto de reset (`setPage(1)`) dispara el único `fetchRows` con `page=1`. Con `page === 1`, el fetch ocurre como hoy. La paginación a mano (clic en «Página N») debe seguir emitiendo **exactamente un** `/rows`.

- [x] **Step 2: Verificar gate**

Run: `npx tsc --noEmit` y `npx vite build`
Expected: exit 0 y exit 0.

- [x] **Step 3: Generar dataset de 60 filas y medir con Resource Timing**

Script pandas en `%TEMP%` (patrón sub‑1) → archivo temporal `soc_60.xlsx`: 60 filas en varias horas/días. Subirlo, ir a **página 2**, aplicar un filtro (nivel Alto).
Expected (Resource Timing en consola): **1 sola** petición `/rows` en el cambio, con `page=1`.

- [x] **Step 4: Verificar que la paginación sigue viva**

Clic en «Página 2» del paginador.
Expected (Resource Timing): exactamente **1** `/rows` con `page=2`; la tabla muestra la página correcta.

- [x] **Step 5: Commit**

```bash
git add frontend/src/components/DataTable.tsx
git commit -m "fix: un solo /rows por cambio de filtros en pagina >1"
```

---

### Task 5: `TimelineBrush` — render estático del histograma global

**Files:**
- Create: `frontend/src/components/TimelineBrush.tsx`
- Modify: `frontend/src/components/Dashboard.tsx` (montaje antes de «Actividad temporal por criticidad»)

**Interfaces:**
- Consumes: `buildTimeBuckets`, `TimePoint` (charts.ts, Task 2); `useAnalysisStore((s) => s.summary)` (resumen **global**); `useFilterStore` con **selectores** (`(s) => s.from`, `(s) => s.to`) — no el store entero.
- Produces: `export const TimelineBrush: React.FC` — devuelve `null` cuando `points.length < 2`.

- [x] **Step 1: Crear el componente (sin gestos aún)**

Lógica: leer el summary global → `buildTimeBuckets` → `if (points.length < 2) return null`. Render: `<DashboardSection title="Línea de tiempo">` + `ResponsiveContainer` (height ~90) con `BarChart data={points}`:
- 4 `<Bar stackId="tl" dataKey={level} fill={LEVEL_COLORS[level]}>` apilados por
  nivel — mismos hijos y paleta que la serie; sin barra de total aparte.
- Sin `YAxis`; sin `CartesianGrid`; `XAxis` con `AXIS_TICK` e
  `interval="preserveStartEnd"` (solo primera/última etiqueta); **sin tooltip**
  (YAGNI).
- Región de ventana confirmada: `ReferenceArea` con los `label` de los puntos
  cubiertos por `from`/`to` del store, derivados con un helper interno
  `labelsForRange(points, from, to)`: **normalizar ambos lados** con
  `.replace('T', ' ')` antes de comparar (`detail` usa espacio; el store escribe
  `T`) y comparar por `detail`; si ningún punto queda dentro, sin región.
- Comentarios en español; sin indicación de gestos todavía.

- [x] **Step 2: Montar en `Dashboard.tsx`**

Justo antes de `{/* Actividad temporal… */}` (línea ~593): `<TimelineBrush />`.

- [x] **Step 3: Verificar gate**

Run: `npx tsc --noEmit` y `npx vite build`
Expected: exit 0 y exit 0.

- [x] **Step 4: Recorrido — la tira existe y oculta con <2 tramos**

a) `soc_sample.xlsx`: la tira aparece sobre «Actividad temporal», con los mismos tramos que la serie (comparar primera/última etiqueta — **Review Focus 3**).
b) Generar `soc_un_tramo.xlsx` (12 filas, todas `2026-01-10 09:xx`): subirlo.
Expected: **la tira no se renderiza** (1 solo punto); el resto del dashboard normal.

- [x] **Step 5: Commit**

```bash
git add frontend/src/components/TimelineBrush.tsx frontend/src/components/Dashboard.tsx
git commit -m "feat: tira de linea de tiempo con histograma global"
```

---

### Task 6: Gestos del brush, commit al soltar y semántica de ventana

**Files:**
- Modify: `frontend/src/components/TimelineBrush.tsx`
- Modify: `frontend/src/charts.ts` (`dayMonth`)
- Modify: `frontend/src/store/filters.ts` (solo doc de `from`/`to`)

**Interfaces:**
- Consumes: `setRange(from, to)` del store; `hourly` y `points` de `buildTimeBuckets`; `dayMonth` (charts.ts).
- Produces: ventanas ISO escritas con esta derivación exacta (rama según el flag `hourly`, **nunca** inspeccionando el string):
  - horario: `from = detail.replace(' ', 'T')` → `2026-01-10T08:00`; `to = from.slice(0, 13) + ':59:59.999'` → `2026-01-10T08:59:59.999`
  - diario: `from = detail` → `2026-01-12`; `to = detail + 'T23:59:59.999'`

- [x] **Step 1: `dayMonth` robusta ante datetime (cierra el minor de sub‑1)**

En `charts.ts`, formatos pineados por el comportamiento:

```ts
dayMonth('2026-01-10')        // '10/01'
dayMonth('2026-01-10T08:00')  // '10/01 08:00'  (HH:MM, sin segundos)
dayMonth('basura')            // 'basura'       (sin NaN ni throw)
```

- [x] **Step 2: Doc del store**

`from`/`to`: «ISO con fecha u hora (`YYYY-MM-DD` o `YYYY-MM-DDTHH:mm…`); null = sin filtro» (antes decía solo `YYYY-MM-DD`).

- [x] **Step 3: Gestos en `TimelineBrush.tsx`**

- Estado local del arrastre (labels de puntos) + punto de inicio en px; umbral **4 px** para distinguir clic de drag.
- `pointerdown` sobre el `BarChart`: localizar el punto por `label` (mismo patrón que `pickSlot`: `points.find(p => p.label === …)`).
- `mousemove`/`mouseup` en **`window`** mientras hay arrastre, con limpieza en `unmount` (**Review Focus 2**).
- Durante el drag: `ReferenceArea` entre labels min/max (regiones exacta de tramo a tramo — **Review Focus 5**). Al soltar: derivar `from`/`to` (reglas de Interfaces) y `setRange`. Clic (<4 px) = un solo tramo con la misma derivación. Arrastre nuevo = reemplaza la selección.
- Etiquetas de extremos de la región confirmada con `dayMonth`.
- Nota de ayuda (font‑mono, text‑dim, patrón de las notas existentes): «Arrastrá un tramo para acotar el análisis; con un clic seleccionás un solo tramo.»
- **Cero escrituras durante el drag**: `setRange` solo en `mouseup` (**D6 / criterio «una petición por cambio»**).

- [x] **Step 4: Verificar gate**

Run: `npx tsc --noEmit` y `npx vite build`
Expected: exit 0 y exit 0.

- [x] **Step 5: Recorrido A — `soc_sample.xlsx` (granularidad horaria)**

1. Arrastrar 2 tramos → contador «X de Y», KPIs y donut acotados; chip de rango con hora (p. ej. `10/01 08:00–10/01 11:00`); **la tira conserva el histograma global** con la región sombreada.
2. Resource Timing: **0** peticiones durante el drag; al soltar, **exactamente 1** `/summary` (**Review Focus 4**).
3. Arrastre que termina **fuera del `<svg>`**: la selección se completa igual.
4. Clic suelto en un tramo → ventana de 1 tramo.
5. «×» del chip de rango → región fuera, KPIs globales (12).
6. Cambiar de sección y volver → la ventana sobrevive (R21).
7. **Criterio 2**: arrastrar hasta el **último** tramo del archivo → el contador incluye los eventos de ese tramo (sample: las 18:00 del 12/01).

- [x] **Step 6: Recorrido B — granularidad diaria**

Generar `soc_largo.xlsx`: 460 filas distribuidas en 19 días × 24 h distintas (>400 tramos horarios → `hourlyTooLong` → cae a diario). Subirlo.
Expected: la tira es **diaria**; el chip de rango **sin hora**; en Resource Timing el query lleva `to=…T23%3A59%3A59.999`; arrastrar recorta todo correctamente.

- [x] **Step 7: Commit**

```bash
git add frontend/src/components/TimelineBrush.tsx frontend/src/charts.ts frontend/src/store/filters.ts
git commit -m "feat: brush de arrastre fija la ventana temporal (gestos y semantica)"
```

---

### Task 7: Verificación final del sub‑proyecto

**Files:**
- Modify: `docs/superpowers/plans/2026-10-27-linea-de-tiempo-brush.md` (checkboxes)

- [x] **Step 1: Suites**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` → **45 passed**; `npx tsc --noEmit` → 0; `npx vite build` → 0.

- [x] **Step 2: Checklist de aceptación (spec §5, recorrido en navegador)**

| # | Criterio | Dataset |
|---|---|---|
| 1 | Arrastrar recorta gráficos + tabla + contador sin recargar | soc_sample |
| 2 | Hasta el último tramo incluye sus eventos | soc_sample |
| 3 | Tira siempre global; «×» restaura | soc_sample |
| 4 | 1 petición por release; 0 con ventana puesta | soc_sample (Resource Timing) |
| 5 | Filtro en página>1 ⇒ un solo `/rows` | soc_60 |
| 6 | Limpiar tras teclear no reaplica `q` | soc_60 |
| 7 | <2 tramos ⇒ sin tira | soc_un_tramo |
| 8 | Red de regresión sub‑1 intacta | pytest |
| 9 | Gate verde (paso 1) | — |

- [x] **Step 3: Marcar checkboxes del plan y ledger; commit**

```bash
git add docs/superpowers/plans/2026-10-27-linea-de-tiempo-brush.md
git commit -m "test: verificacion end-to-end del brush (sub-proyecto 2)"
```
