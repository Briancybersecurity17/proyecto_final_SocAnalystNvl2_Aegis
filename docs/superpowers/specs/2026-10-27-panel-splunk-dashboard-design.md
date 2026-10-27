# Diseño — Panel único estilo Splunk: tabla en el dashboard y gráficos como filtros (sub‑proyecto 3)

**Fecha:** 2026-10-27
**Estado:** aprobado en conversación (brainstorming), pendiente de revisión del dueño del proyecto
**Predecesores:** [Motor de filtros del dashboard](2026-10-26-motor-de-filtros-dashboard-design.md) (sub‑proyecto 1) y [Línea de tiempo con brush](2026-10-27-linea-de-tiempo-brush-design.md) (sub‑proyecto 2)
**Mockup de referencia:** «Opción B, versión 8» aprobado en el companion visual de la sesión de brainstorming (paneles con estilo HUD real del proyecto: aro neón, esquinas achaflanadas, Orbitron/JetBrains Mono)

---

## 1. Intento

El analista trabaja en **una sola pantalla, estilo Splunk**: sube un Excel y en el
Dashboard ve —de arriba abajo— los KPIs, las tarjetas de nivel, la barra de
filtros, las observaciones de IA, el histograma de la línea de tiempo, **la tabla
de registros**, y debajo los gráficos. Cualquier gráfico es un **selector**: un
clic en un sector, tarjeta o barra escribe en el **único filtro global** que
comparten dashboard, tabla y chips. La tabla deja de vivir en «Carga de archivos»
(ya no se duplica el concepto) y esa sección se queda con la ingesta más un
**historial de subidas** para reabrir análisis previos.

**Éxito:** desde cualquier gráfico se filtra el resto de la pantalla sin recargar,
la tabla muestra exactamente los mismos registros que los KPIs, el panel de IA se
pliega cuando ya se leyó, y la página completa baja de ~4.400 px a ~3.400‑3.700 px
por tarjetas compactas, scroll interno de la tabla y paneles en dos columnas.

**Constraints heredados:** sin dependencias nuevas en el frontend; comentarios en
español; los 4 filtros de sub‑1 y el brush de sub‑2 intactos (red de regresión
existente); verde = `pytest` + `tsc --noEmit` + `npm run build` + recorrido manual
en navegador; checkpoints (commits) por tarea.

## 2. Decisiones de diseño (aprobadas)

| # | Decisión | Alternativa descartada y por qué |
|---|---|---|
| D1 | **Un solo filtro global bidireccional**: la tabla, el brush y los clics de gráficos escriben en el mismo `useFilterStore`. | Filtros independientes por gráfico: dos estados de verdad incomprensibles para el analista (rechazado explícitamente en brainstorming). |
| D2 | **Facet‑exclusion** (matiza R14 de sub‑1): *un gráfico ignora el filtro de su propia dimensión y respeta los demás*. Donut y tarjetas de nivel ignoran `level`; el gráfico de hosts ignora la faceta `Hostname`; el brush sigue inmune (lee el resumen global); KPIs, tabla, IA, actividad y justificación usan el filtro completo. | Pedir al backend resúmenes «multi‑vista» en una petición: más complejo en el contrato; se pospone. Sin esto, filtrar «Alto» colapsaba el donut a 8/715 y el gráfico de hosts se vaciaba (bug reproducido en vivo). |
| D3 | **`useScopedSummary` gana parámetro `omit`** (`{ level?: boolean; facets?: string[] }`): el hook recalcula `isFiltered`/`filtersKey` sobre los filtros efectivos y, si al omitir quedan vacíos, devuelve el resumen global **sin petición**. Instancias: completa (KPIs/tabla/IA/actividad/justificación), `omit level` (donut + tarjetas), `omit facets: ['Hostname']` (hosts). | Tres hooks duplicados a mano: duplicarían debounce/abort/reset; el parámetro mantiene una sola lógica. |
| D4 | **Máximo 3 peticiones `/summary` por release** de filtros (completa + omit‑level + omit‑hostname), cada una con su debounce; típicamente 1. El backend ejecuta `call_ai_service` por cada una (CPU ×3 en el peor caso, aceptado). | Sincronizar las tres en una sola llamada: requiere cambiar el contrato de `/summary` (YAGNI por ahora; anotado en riesgos). |
| D5 | **La tabla es el componente `DataTable` existente, movido sin rediseño** (mismos filtros, `hud-table` de 12,5 px, 12 columnas del Excel + chip Nivel, scroll interno `max‑h‑96`, 25 filas/página, paginación HUD). | Rediseñarla: ya está aprobada por el usuario y probar en vivo; tocarla solo para arreglos de copy. |
| D6 | **Clic en fila = nada** (solo lectura). | Resaltar/fijar registro (pin estilo Splunk): fuera de alcance por ahora. |
| D7 | **Clic en gráfico**: sector del donut y tarjetas de nivel → `setLevel` (toggle: re‑clic en el activo lo limpia); barra de host → `toggleFacet('Hostname', host)`. El donut mantiene los 4 sectores y **resalta** el elegido (no se colapsa); el gráfico de hosts muestra **todos** los hosts con el seleccionado resaltado (`selectionCells` ya existente). | Que los gráficos se acoten con su propio clic: se vacían a sí mismos y dejan de ser navegación (era el bug de sub‑1). |
| D8 | **El desplegable «Nivel» local del gráfico de hosts se elimina** y el panel pasa a alimentarse de la instancia `omit facets`. | Mantenerlo: producía gráficos vacíos (reproducido: nivel Bajo + facetas = gráfico sin datos) y contradice el filtro global. |
| D9 | **Panel de IA expandible/retraíble, arranca expandido, texto completo** (la calidad del análisis no se toca: ni truncado ni resumen). Estado local, sin persistencia. | Arrancar retraído o recordar la última elección: el usuario eligió «Expandido»; persistir en localStorage, YAGNI. |
| D10 | **«Carga de archivos» = drop‑zone + panel de resultado + historial de subidas** (hasta 8 análisis recientes; clic = reabrir). Sin tabla. | Conservar una copia de la tabla allí: duplicidad confusa (rechazado por el usuario). |
| D11 | **Reabrir = `GET /analysis/{id}` → `setAnalysisId` + `setSummary`**: reutiliza el reset de filtros por cambio de análisis ya cableado en sub‑1 (DataTable, `useScopedSummary` y el brush reaccionan al `analysisId`). | Reprocesar el Excel: lento e innecesario; el DataFrame ya vive en `analysis_store`. |
| D12 | **`noMatches` no oculta la tabla ni el histograma** (son los controles de recuperación: cambiar filtros o ampliar la ventana). El `EmptyState` reemplaza únicamente a los 4 gráficos. | El `EmptyState` global actual tapaba también histograma y tabla: el usuario no podía salir del callejón. |
| D13 | **Se elimina el panel «Valores originales del campo Severity»**; el backend sigue enviando `severity_counts` (sin cambios de backend por esto). | Dejarlo: medio panel de datos que el usuario ya usa poco (la columna Severity se queda en la tabla). |
| D14 | **Tarjetas compactas**: KPIs `lg:grid‑cols‑5` y tarjetas de nivel `lg:grid‑cols‑4` con padding reducido (el alto lo marca el contenido). **Actividad temporal + Justificación** en una fila de 2 columnas; la actividad conserva su área suave con eje Y, tooltip y `ChartInsight`. | Paneles altos con aire muerto (complainto repetido del usuario en las versiones 1‑4 del mockup). |
| D15 | **Barra de filtros sticky** (`position: sticky; top: 0`) con chips + contador «X de Y» + «Limpiar filtros»: visible en cualquier scroll. | Barra normal: perdía de vista al bajar a la tabla. |
| D16 | **Tres arreglos de copy** (§3.9): «de la selección» en vez de «del archivo», causa real del fallback diario, y pista del histograma diciendo que **filtra**. | Textos actuales ambiguos o incorrectos con filtros activos (señalados por el usuario). |

## 3. Arquitectura

### 3.1 Orden de paneles del Dashboard (aprobado en mockup)

1. Cabecera de archivo (nombre + «Resumen generado») — ya existe.
2. KPIs (`grid-cols-2 lg:grid-cols-5`, compactos).
3. Tarjetas de nivel (`lg:grid-cols-4`, compactas) — selector de nivel (D7).
4. **Barra de filtros sticky** (D15): chips de filtros activos + «X de Y registros» + «Limpiar filtros».
5. Panel **Observaciones IA** (expandible, D9).
6. Panel **Línea de tiempo — histograma global** (`TimelineBrush`, inmune a filtros, sub‑2).
7. Panel **Registros importados** con `<DataTable />` (movido, D5).
8. Fila 2 columnas: **Distribución por criticidad** (donut + leyenda compacta, selector) ∥ **Hosts con mayor volumen** (barras apiladas, selector de faceta, sin desplegable).
9. Fila 2 columnas: **Actividad temporal por criticidad** (área suave con eje Y, respeta filtros completos) ∥ **Justificación del nivel de riesgo** (recalculada con filtros completos).
10. ~~Panel Severity~~ eliminado (D13).

### 3.2 Facet‑exclusion en `useScopedSummary` (D2/D3)

- **Firma:** `useScopedSummary(omit?: { level?: boolean; facets?: string[] })`.
- `isFiltered` y `filtersKey` se calculan sobre los filtros **efectivos** (los reales menos los omitidos). Con omit‑level y el único filtro activo es `level`, la instancia usa el resumen global con **cero peticiones**.
- El request omite los campos omitidos (`level: undefined`, faceta `Hostname` fuera del mapa `facets`).
- El reset por cambio de `analysisId` y el comportamiento de error (volver al global + mensaje) son idénticos para las tres instancias (la lógica vive una vez en el hook).
- Consumidores: KPIs, tabla (vía filtro global), IA, actividad y justificación → instancia completa (la existente, sin cambios de comportamiento). Donut + tarjetas de nivel → `omit { level: true }`. Hosts → `omit { facets: ['Hostname'] }`.

### 3.3 Clics como selectors (D7)

- Tarjetas de nivel y sectores del donut llaman `setLevel(nivel)` (toggle). El donut pinta los 4 sectores siempre (datos de la instancia omit‑level) y resalta el sector del `level` activo; las tarjetas marcan la activa con su borde de color.
- Barras del gráfico de hosts llaman `toggleFacet('Hostname', host)`. El panel se alimenta de la instancia `omit facets`, así que **todos** los hosts siguen visibles; el host activo se resalta con `selectionCells` (el `focusHost` local se sincroniza con el valor de la faceta).
- `ChartInsight` se conserva en los paneles que ya lo usan, con el resumen completo (filtros aplicados).

### 3.4 Cambios en componentes existentes

- `UploadSection.tsx`: quita `<DataTable />`; añade `<UploadHistory />` tras `UploadExcel`.
- `Dashboard.tsx`: reordena paneles (§3.1), instancia los resúmenes omit‑level/omit‑facet, sincroniza clics, elimina el panel Severity y el desplegable de nivel del gráfico de hosts, hace sticky la barra de chips y ajusta paddings de tarjetas (todo className + wiring, sin rediseño).
- `DashboardSection.tsx`: gana props opcionales `collapsible?: boolean` y `defaultCollapsed?: boolean` (chevron en `panel-head`; solo el panel IA los usa).
- `DataTable.tsx`: sin cambios de diseño; únicamente el copy de §3.9.1 si aparece en su ámbito.

### 3.5 Historial de subidas (D10/D11)

- **Backend — `GET /api/analyses`**: devuelve los últimos 8 análisis con `status == 'done'` (reutiliza la idea de `_recent_analysis_ids`, con su propio límite): lista de `{ analysis_id, file_name, generated_at, rows, risk }` leídos de `analysis_store`. El listado **no** depende de RAG.
- **Frontend — `UploadHistory.tsx`**: panel HUD que pide `/analyses` al montarse y tras cada carga nueva (`status === 'done'`). Fila por análisis: nombre, fecha, registros, chip de riesgo y botón «Reabrir». Clic → `GET /analysis/{id}` → `setAnalysisId` + `setSummary` (el reset de filtros por cambio de análisis ya existe). 404 (backend reiniciado) → aviso reutilizando `setNotice` + refresco de la lista. Lista vacía → texto tranquilo («Aún no hay análisis previos»), sin error.

### 3.6 Estado vacío `noMatches` (D12)

- El bloque condicional deja de envolver todo: `EmptyState` + los 4 gráficos están dentro del `noMatches`; **histograma, tabla, IA y barra de filtros se renderizan siempre**. Al limpiar filtros los gráficos vuelven solos.

### 3.7 Copys corregidos (D16)

1. `Dashboard.tsx` (~línea 496, leyenda del donut): «…% de los N **registros de la selección**» (antes «del archivo»; con filtros activos era mentira).
2. `Dashboard.tsx` (~líneas 535/542, fallback diario): explicar la causa real (el fallback se dispara cuando `hourlyData.length === 1`, `charts.ts:274`) — «Con los filtros actuales los registros quedan en un solo tramo horario; se muestra el total del día».
3. `TimelineBrush`: pista bajo la tira — «Arrastra un tramo o pulsa una columna: **eso filtra** el resto del panel».

### 3.8 Tests backend nuevos

- `GET /api/analyses`: lista solo `done`, orden cronológico inverso, campos completos; límite respetado; store vacío → lista vacía (no 500).

## 4. Casos l�mite

- **Solo filtro de nivel activo** → la instancia omit‑level ve cero filtros efectivos ⇒ resumen global, sin petición (no se cuelga ni parpadea).
- **Archivo sin columna Severity** (todo «bajo») → donut/tarjetas con 1 sector; los clics siguen funcionando.
- **Un solo tramo horario** → no hay brush (sub‑2) y la actividad cae a diario con el copy corregido (§3.9.2).
- **Backend reiniciado** → historial vacío (lista con texto amable) e `analysisId` de localStorage inválido (mecanismo de aviso ya existente).
- **Reabrir un análisis con RAG purgado** (solo los últimos 5 indexados): dashboard, gráficos y tabla funcionan; el chat QA puede no encontrar documentos — limitación aceptada y documentada.
- **Recarga de página con filtros activos**: el restore existente y el historial conviven sin pisarse (el restore ya setea `analysisId` + summary).

## 5. Criterios de aceptación

1. Con el Excel de prueba (715 registros), el Dashboard muestra el orden de §3.1; la tabla está en el Dashboard y **ya no** en «Carga de archivos».
2. Clic en «Crítico» (tarjeta o donut) → KPIs, tabla, hosts, actividad, justificación e IA se acotan; **donut y tarjetas muestran los 4 niveles** (97/8/7/603) con el activo resaltado. Re‑clic limpia.
3. Clic en la barra «DC‑01» → faceta `Hostname` activa en los chips; el gráfico de hosts **sigue mostrando todos los hosts** con DC‑01 resaltado; el resto de la pantalla se acota. Re‑clic (o «×» del chip) limpia.
4. Un release de filtros dispara **≤3** peticiones `/summary` (medida con Resource Timing); con un solo filtro de nivel, la instancia omit‑level dispara **0**.
5. La barra de filtros queda visible al hacer scroll hasta el final de la página; «Limpiar filtros» restaura la pantalla completa.
6. El panel de IA arranca expandido, se pliega/despliega con su chevron y el texto se ve **completo** (sin truncar) en ambos estados.
7. Con un filtro sin coincidencias: aparece el aviso `noMatches` y **histograma, tabla y chips siguen visibles**; los 4 gráficos se ocultan; al limpiar, todo vuelve.
8. «Carga de archivos» muestra drop‑zone + resultado + historial (≤8); «Reabrir» carga el análisis completo (KPIs, tabla y gráficos) reseteando filtros; un id inexistente muestra aviso y refresca la lista.
9. El panel «Valores originales de Severity» ya no existe; `severity_counts` sigue llegando del backend sin cambios.
10. Los 3 arreglos de copy (§3.7) aplicados.
11. Tarjetas KPI/nivel compactas (alto acorde al contenido) y actividad + justificación en fila de 2 columnas; la actividad conserva área suave, eje Y, tooltip y `ChartInsight`.
12. Los 4 filtros de sub‑1 y el brush de sub‑2 siguen intactos: la red de regresión existente pasa sin modificaciones salvo la adición del test §3.8.
13. `pytest` en verde; `npx tsc --noEmit` y `npm run build` limpios (solo el aviso preexistente de chunk >500 kB); recorrido manual: los 6 combinados ya probados en sub‑1/2 más los clics de donut, tarjetas y hosts.

## 6. Fuera de alcance (YAGNI)

- Panel de facetas independiente: el endpoint `/facets` sigue sin consumidor UI (y con él, el fix NEW‑1 «detect_facets vs nombres canónicos»).
- Persistir el historial en disco/BD (in‑memory como el resto del store) y más de 8 entradas.
- Persistir el estado plegado del panel IA.
- Reordenar paneles, layout configurable o temas.
- Filtros independientes por gráfico (rechazado: D1) y acciones al pulsar una fila (D6).
- Contrato `/summary` multi‑vista para bajar de 3 peticiones (D4).
- Exportar el CSV filtrado.

## 7. Verificación (gate por tarea)

Cada tarea termina en commit verde: `pytest` para tareas de backend; `npx tsc --noEmit` + `npm run build` para frontend; la tarea de wiring añade el recorrido manual de los criterios 2‑8 en el navegador (aprovechando el servidor ya levantado en `localhost:5300`).

## 8. Riesgos abiertos

- **CPU ×3 en `/summary`** con los tres scopes simultáneos: aceptado por el tamaño actual; si duele, el contrato multi‑vista es la salida (rompe compat, sería sub‑proyecto propio).
- **`analysis_store` sin evicción**: el historial suma visibilidad a un store que crece con cada subida (preexistente; no se empeora, pero se hace más visible).
- **RAG purgado vs historial**: reabrir análisis viejos deja el chat QA sin documentos (documentado en §4; comportamiento degradado, no roto).
