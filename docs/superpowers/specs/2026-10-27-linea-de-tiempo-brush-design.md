# Diseño — Línea de tiempo con brush (sub‑proyecto 2)

**Fecha:** 2026-10-27
**Estado:** aprobado en conversación (brainstorming), pendiente de revisión del dueño del proyecto
**Predecesor:** [Motor de filtros del dashboard](2026-10-26-motor-de-filtros-dashboard-design.md) (sub‑proyecto 1, fusionado en `master` @ `0a08d63`)
**Sucesor planeado:** panel de campos/facetas (sub‑proyecto 3)

---

## 1. Intento

El analista arrastra un tramo sobre una línea de tiempo y eso fija la ventana
temporal `from`/`to` en el store de filtros: el dashboard, la tabla, el contador
«X de Y» y los chips reaccionan con el motor de filtros ya cableado en el
sub‑proyecto 1, sin recargar la página y sin tocar el backend.

**Éxito:** arrastrar → ver el recorte aplicarse → poder deshacerlo con el «×»
del chip de rango. El sub‑proyecto 1 dejó todo el camino de filtrado listo
(`setRange` existe sin llamador, `/summary` y `/rows` aceptan `from`/`to`, el
chip de rango ya se renderiza); este sub‑proyecto construye el control que le da
vida y cierra los menores diferidos que le pertenecen.

**Constraints heredados:** sin dependencias nuevas en el frontend; comentarios
en español; los 4 filtros de sub‑proyecto 1 intactos (red de regresión existente);
verde = `pytest` + `tsc --noEmit` + `npm run build` + recorrido manual en
navegador; checkpoints (commits) por tarea.

## 2. Decisiones de diseño (aprobadas)

| # | Decisión | Alternativa descartada y por qué |
|---|---|---|
| D1 | **Tira dedicada con datos GLOBALES** (`TimelineBrush.tsx`), sobre la tarjeta de la serie existente. El brush es *control* (siempre el histograma completo); la serie sigue siendo *visualización* scopeda. | Recharts `<Brush>` sobre la serie: los datos se recortan al filtrar y el eje se contrae a la propia selección (auto‑referencial); reemplazar el clic‑para‑fijar: perdería la tarjeta de desglose y violaría el criterio 1 de sub‑1. |
| D2 | **Bucketing adaptativo compartido**: la tira usa la misma regla que la serie (horas si hay >1 tramo y ≤400 puntos, si no días), extraída a una función auxiliar usada por ambos. | Regla propia en la tira: dos fuentes de verdad divergentes. |
| D3 | **Commit al soltar** el puntero: mientras se arrastra solo hay estado local (cero peticiones); `setRange` se escribe en `mouseup`. | Commit en vivo: cada pausa >300 ms del arrastre dispararía un `/summary` (violaba el criterio «una petición por cambio»). |
| D4 | **`to` = fin del último tramo con milisegundos** (`…T23:59:59.999` diario, `…T08:59:59.999` horario). `from` = inicio del primer tramo. | `to` date‑only a medianoche: el `<=` inclusivo del backend excluiría todo el día final (bug latente heredado de sub‑1, hoy no provocable porque no había setter). |
| D5 | **Sin asas redimensionables**: un arrastre nuevo reemplaza la selección; el «×» del chip y un nuevo arrastre cubren deshacer/ampliar. | Asas tipo brush clásico: complejidad de manejo de puntero adicional sin necesidad (YAGNI). Iteración futura posible. |

## 3. Arquitectura

### 3.1 Componente nuevo: `frontend/src/components/TimelineBrush.tsx`

- **Datos:** lee `useAnalysisStore.summary` → `events_per_hour_by_level` /
  `events_per_day_by_level` (el resumen **global**, el mismo que ya usa la
  tabla). Mientras los filtros acotan el resto del dashboard, la tira no cambia.
- **Bucketing:** `buildTimeBuckets(summary)` — función auxiliar nueva en
  `frontend/src/charts.ts` (el módulo compartido donde ya viven `toCount`,
  `fmtCount` y `LEVEL_LABELS`) que devuelve los puntos
  (`label/detail/total/…`) y la granularidad efectiva (horaria/diaria).
  Para ello `hourLabel` y `dayMonth` se mueven de `Dashboard.tsx` a `charts.ts`
  (exportadas). `Dashboard.tsx` reutiliza `buildTimeBuckets` para su serie
  scopeda: una sola regla, cero divergencia (D2).
- **Visual:** `BarChart` de Recharts con el sistema de estilos existente
  (`charts.ts`), altura baja (~80 px), sin eje Y, eje X con etiquetas mínimas.
  La selección activa se pinta con `ReferenceArea` sombreado y las etiquetas
  de los extremos; durante el arrastre la región sigue al puntero (estado local).
- **Estado y acciones:** `useFilterStore` → `from`, `to`, `setRange`. El chip
  de rango existente (`Dashboard.tsx`, ya implementado) refleja la ventana y la
  limpia; R21 (reset solo al cambiar `analysisId`) garantiza que la selección
  sobreviva al cambio de sección.
- **Renderizado condicional:** no se renderiza si el bucketing produce **menos
  de 2 tramos** (un rango de un tramo no puede recortar nada).

### 3.2 Gestos

- **Arrastrar** (≥ ~4 px) = elegir rango, en cualquier dirección; se normaliza
  a `from ≤ to`. Sin peticiones hasta soltar.
- **Soltar** = `setRange(from, to)` → debounce 300 ms del hook
  `useScopedSummary` → **1 sola petición `/summary`** → KPIs, gráficos y chip
  reaccionan. La tabla no está montada en el Dashboard ⇒ sin `/rows`.
- **Clic sin arrastrar** (< ~4 px) = seleccionar un único tramo (día/hora
  pulsado).
- La selección de la tira y el `focusSlot` de la serie son estados
  independientes: fijar un tramo en la serie **no** filtra; arrastrar en la
  tira **no** toca el desglose.

### 3.3 Semántica de la ventana (contrato)

- `from` = ISO del inicio del primer tramo (`2026-01-10` o `2026-01-10T08:00`).
- `to` = ISO del **fin** del último tramo con precisión de milisegundos
  (`2026-01-12T23:59:59.999` / `2026-01-10T08:59:59.999`).
- El store `filters.ts` pasa a documentar que admite fecha u hora ISO (antes
  solo `YYYY-MM-DD`).
- El chip de rango muestra fecha simple en granularidad diaria y fecha+hora en
  horaria; `dayMonth` se hace robusta ante datetime (cierra el minor «`dayMonth`
  quiebra con datetime» de sub‑1).
- El backend **no cambia**: `_parse_ventana` ya usa `pd.to_datetime` y acepta
  datetime; `from > to` → 400 ya existe y desde la UI es inalcanzable con la
  normalización.

### 3.4 Menores de sub‑proyecto 1 incluidos aquí

1. **Carrera «Limpiar filtros» vs debounce de `q`**: el store añade un
   `clearEpoch` (contador) incrementado en `clearAll`; los timers de debounce
   capturan la época al programarse y no hacen nada si cambió. El brush añade
   otra vía de escritura de la ventana, misma clase de carrera.
2. **Doble `/rows` en página >1**: un cambio de filtro estando en página >1
   dispara hoy reset de página *después* del fetch (la petición vieja se aborta,
   pero se emite). Requisito: **exactamente un `/rows` por cambio** incluso en
   página >1 (mecanismo a criterio del plan: orden de efectos o reset pendiente).

### 3.5 Test backend nuevo

Caso que fija la semántica D4: ventana con `to` de fin de tramo en milisegundos
incluye los eventos del propio tramo (ya hay cobertura de ventana en
`test_filters.py` / `test_endpoints.py`; se añade el caso datetime con ms que
la tira emite).

## 4. Casos límite

| Caso | Comportamiento |
|---|---|
| < 2 tramos (archivo de un solo día/hora) | La tira no se renderiza. |
| Tramos sin actividad | La tira muestra las mismas lagunas que la serie (sin inventar datos); un rango solo en laguna ⇒ «0 de Y» + `EmptyState` (comportamiento ya probado en sub‑1). |
| > 400 tramos horarios | Cae a diario con la misma regla que la serie. |
| Carga con filtros activos (`loading`) | La tira no parpadea: sus datos son globales. |
| Error de red | Igual que hoy: aviso discreto + resumen global; la tira (global) no se afecta. |

## 5. Criterios de aceptación

1. Arrastrar en la tira recorta **todos** los gráficos, la tabla (al navegar) y
   el contador «X de Y», sin recargar la página.
2. Seleccionar hasta el último día/hora **incluye los eventos de ese día
   completo** (regresión del bug latente de `to` a medianoche).
3. La tira sigue mostrando el histograma **global** mientras el resto se acota;
   el «×» del chip limpia la ventana y todo vuelve al estado previo.
4. **Una sola petición** por *release* (medida con Resource Timing) y **cero
   adicionales** con la ventana ya puesta (D6 heredado).
5. Cambiar cualquier filtro estando en página >1 dispara **exactamente un
   `/rows`** (menor 3.4‑2).
6. «Limpiar filtros» justo después de teclear en la búsqueda no reaplica la
   búsqueda al vencer el debounce (menor 3.4‑1).
7. Archivo de un solo tramo ⇒ la tira no aparece.
8. Los 4 filtros de sub‑1 siguen intactos: la red de regresión existente
   (`pytest`, 44 tests) pasa sin modificaciones salvo la adición del test 3.5.
9. `pytest` en verde; `npx tsc --noEmit` y `npm run build` limpios (solo el
   aviso preexistente de chunk >500 kB).

## 6. Fuera de alcance (YAGNI)

- Asas redimensionables de la selección.
- Tira en la sección de «Carga de archivos».
- Cualquier cambio de backend.
- Serializar filtros en la URL.
- El panel de campos/facetas (sub‑proyecto 3) y su fix `NEW-1`
  (`detect_facets` vs nombres canónicos).
- Menores diferidos sin relación con la ventana: `q`→«nan», ChromaDB en
  fixtures, `filterKey()` (se extraerá cuando aparezca el tercer consumidor de
  fetch, en sub‑3), NEW‑2 (reset en consumidores).

## 7. Verificación (gate por tarea)

- `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` (44 + el test
  nuevo).
- Desde `frontend/`: `npx tsc --noEmit` y `npm run build`.
- Recorrido manual en navegador (protocolo sub‑1): datasets `soc_sample.xlsx`
  (3 días, resolución horaria), un Excel de un solo día (tira oculta) y uno con
  >25 filas (página >1, criterio 5) — generados con pandas, como en sub‑1.

## 8. Riesgos abiertos

| Riesgo | Mitigación |
|---|---|
| Mapeo de coordenadas de puntero → tramos de un eje de categorías impreciso | Se usa `activeLabel` de Recharts (mismo mecanismo que `pickSlot` ya probado); si la precisión es dudosa, anclar por índice de tramo. |
| `ReferenceArea` sobre eje de categorías resalta de más (comentario conocido en `Dashboard.tsx:282-287`) | En la tira la región se limita a los tramos seleccionados exactamente (índice a índice); verificar en el recorrido. |
| Archivos con miles de tramos diarios ensucian la tira | Misma regla de 400 puntos que la serie; la tira es un mini‑histograma sin etiquetas finas. |
| Arrastre que empieza en la tira y termina fuera del `<svg>` | Escuchar `mouseup` en `window` durante el arrastre (patrón estándar) y limpiar en `unmount`. |
