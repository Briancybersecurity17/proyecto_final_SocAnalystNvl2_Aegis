# Motor de filtros para el dashboard — Diseño (Sub‑proyecto 1)

| | |
|---|---|
| **Estado** | Aprobado en diseño, pendiente de implementación |
| **Fecha** | 2026‑10‑26 |
| **Alcance** | Backend (FastAPI/pandas) + frontend (React/zustand) |
| **Repo** | Comunidad Dojo — SOC Analyst Nivel 2 |

---

## 1. Objetivo

Que **todo el dashboard responda a un conjunto de filtros activos**: ventana temporal y facetas
sobre las columnas del archivo (Hostname, User, Source_IP, Event_ID, Log_Source, Result…),
igual que el panel de búsqueda de Splunk.

Criterio de éxito: filtrar por *«ventana de tiempo + usuario»* y ver que los KPIs, el donut de
criticidad, la serie temporal, la barra de hosts y la tabla de registros muestran **solo ese
subconjunto**, con un contador visible «243 de 715 registros».

**Requisito explícito del usuario**: la app debe reconocer los campos de cada tabla para ser
funcional con **cualquier** Excel cargado. No se admite una lista de campos fija.

## 2. Contexto verificado (hechos del código)

- `call_ai_service(dataframe)` (`backend/main.py:410`) es **pura**: recibe un DataFrame, no llama
  a ningún modelo externo (el nombre es heredado y engañoso) y devuelve el dict del resumen
  (`main.py:650-673`).
- `process_analysis` guarda en `analysis_store` el DataFrame **original, sin enriquecer**
  (`main.py:701`); el parseo de `Timestamp` ocurre dentro de `call_ai_service` (`main.py:491`)
  sobre una copia descartable.
- El filtrado de `/rows` está **pegado dentro del endpoint** (`main.py:809-826`) y recalcula
  `_level`, `_reason` y `_suspicious` en **cada request** (`main.py:801-807`).
- Columnas esperadas del archivo (`main.py:74-77`): `Log_ID, Timestamp, Hostname, Event_ID, User,
  Source_IP, Destination, Log_Source, Event_Description, Result, Severity`. Un archivo puede traer
  columnas adicionales.
- `page_size` máximo 200 (`main.py:783`); el navegador nunca recibe el dataset completo.
- El store de zustand solo guarda agregados (`frontend/src/store/index.ts:79-121`); no hay estado
  de filtro compartido: todo es `useState` local.
- El dashboard lee el resumen en `Dashboard.tsx:77` y renderiza 4 gráficos desde campos del mismo
  (`level_counts`, `events_per_*_by_level`, `top_hosts_by_level`, `severity_counts`).

## 3. Decisiones de diseño

| # | Decisión | Motivo |
|---|---|---|
| D1 | **Mismo shape de resumen, DataFrame acotado** | Los 4 gráficos existentes no se reescriben: cambian de dónde leen. Una petición por cambio de filtro, una sola fuente de verdad, cero tipos duplicados. |
| D2 | **Enriquecer una vez al ingerir** | Filtrar pasa a ser enmascarar filas. Evita recalcular `row_reason`/`_is_suspicious` por fila en cada arrastre. |
| D3 | **Extraer `apply_filters` a un módulo propio** | `/rows` y `/summary` comparten la misma lógica. Un filtro nuevo se agrega en un solo lugar. |
| D4 | **Facetas detectadas del archivo, no hard‑coded** | Requisito explícito: servir para cualquier Excel. |
| D5 | **Filtros por query string (GET)** | Compartible/bookmarkeable, consistente con `/rows`, cacheable por el navegador. |
| D6 | **Sin filtros ⇒ sin petición extra** | El dashboard arranca idéntico al actual: usa el resumen global ya almacenado. |

## 4. Backend

### 4.1 Enriquecer una vez al ingerir (`process_analysis`)

Tras `pd.concat` (`main.py:690`), antes de guardar en `analysis_store`:

```python
work = df.copy()
if "Severity" in work.columns:
    work["_level"] = work["Severity"].map(normalize_level)
else:
    work["_level"] = "bajo"
if "Timestamp" in work.columns:
    work["Timestamp"] = pd.to_datetime(work["Timestamp"], errors="coerce")
work["_reason"] = work.apply(row_reason, axis=1) if len(work) else pd.Series([], dtype=str)
work["_suspicious"] = work.apply(_is_suspicious, axis=1) if len(work) else pd.Series(dtype=bool)
analysis_store[analysis_id]["dataframe"] = work
```

- `call_ai_service(work)` recibe el enriquecido: es **idempotente** (sobrescribe `_level` en su
  copia), así que no hay que tocarla.
- Los prefijos `_` ya están protegidos en la salida: `_records`, `_enrich_records` y
  `out_cols` (`main.py:837`) filtran columnas que empiezan con `_`. **Ningún campo derivado llega
  al navegador como si fuera dato del Excel.**
- `Timestamp` pasa a `datetime64` en el frame almacenado. Todas las lecturas posteriores deben
  tolerar `NaT`.

### 4.2 Módulo nuevo `backend/filters.py`

```python
@dataclass
class FilterParams:
    from_dt: datetime | None = None
    to_dt: datetime | None = None
    level: str = ""            # ∈ LEVEL_ORDER
    suspicious: bool = False
    result: str = ""           # match exacto sobre _result_label
    q: str = ""                # texto libre en columnas originales
    facets: dict[str, list[str]] = field(default_factory=dict)   # OR dentro de un campo

def parse_filters(request: Request, df: pd.DataFrame) -> FilterParams: ...
def apply_filters(df: pd.DataFrame, p: FilterParams) -> pd.DataFrame: ...
def detect_facets(df: pd.DataFrame) -> list[FacetField]: ...
```

Reglas de `apply_filters` (todas combinadas con **AND**; los valores de una misma faceta con **OR**):

1. `from_dt`/`to_dt`: filtro por rango **inclusivo** sobre `Timestamp`. Las filas con `NaT`
   quedan **fuera** cuando hay filtro temporal y **dentro** cuando no lo hay.
2. `level`: igualdad exacta sobre `_level`.
3. `suspicious`: igualdad booleana sobre `_suspicious`.
4. `result`: igualdad insensible a mayúsculas sobre `_result_label` (misma regla que
   `/rows:813-815`).
5. `q`: `str.contains` sin regex, insensible a mayúsculas, sobre las columnas **originales**
   (que no empiezan con `_`), igual que `/rows:816-826`.
6. Cada faceta: igualdad **exacta insensible a mayúsculas** sobre la columna original indicada.

`parse_filters` separa los parámetros canónicos de las facetas: **cualquier parámetro que no sea
canónico y cuyo nombre coincida con una columna del DataFrame se trata como faceta**. Parámetros
canónicos desconocidos o facetas sobre columnas inexistentes se **ignoran** (tolerancia a URLs
viejas). `level` fuera de `LEVEL_ORDER` y `from_dt > to_dt` devuelven **400** con mensaje claro.

### 4.3 `GET /api/analysis/{id}/summary`

```
?from=2026-01-12T00:00:00&to=2026-01-15T23:59:59
&level=alto&suspicious=true&result=Failure&q=kerberos
&Hostname=DC-01&Hostname=WS-018&User=jsmith
```

Flujo: `analysis_store[id]["dataframe"]` → `parse_filters` → `apply_filters` →
`call_ai_service(df_filtrado)` → respuesta con dos campos añadidos:

```json
{ ...mismo AnalysisSummary..., "rows": 243, "total": 715, "filtered_total": 243 }
```

- `rows` = filas filtradas → **las tarjetas KPI reflejan el filtro sin tocar su código**.
- `total` = tamaño del dataset completo; `filtered_total` = resultado del filtro (redundante con
  `rows` pero explícito para el consumidor del contador).
- Sin filtros activos el frontend **no llama a este endpoint**: usa el resumen global.
- 404 si el `analysis_id` no existe; 409 si el análisis aún no está listo (misma semántica que
  `/rows:794-798`).

### 4.4 `GET /api/analysis/{id}/facets`

Mismos parámetros de filtro que `/summary`. Devuelve los campos detectados **ya acotados** por los
filtros activos, para que los conteos del panel sean coherentes con lo que se ve:

- **Detección sobre el dataset completo** (no sobre el filtrado): así la lista de campos es
  estable y un campo no desaparece del panel solo porque el filtro actual lo dejó vacío.
- **Conteos sobre el DataFrame filtrado**: los números del panel siempre coinciden con lo que el
  dashboard está mostrando.

```json
{ "total": 715, "filtered_total": 243,
  "fields": [
    { "name": "Hostname", "label": "Hostname", "distinct": 9,
      "values": [ {"value": "DC-01", "count": 33}, {"value": "WS-018", "count": 22} ] },
    { "name": "_level", "label": "Criticidad", "distinct": 4, "values": [...] }
  ] }
```

**Detección de facetas** (`detect_facets`) — entra una columna si cumple todo:

- no empieza con `_` y no es `Timestamp` ni `Log_ID`;
- no es numérica de alta cardinalidad;
- cardinalidad baja: `nunique() <= max(30, 5% de las filas)`;
- no es texto libre: longitud media de los valores ≤ 40 caracteres (descarta
  `Event_Description` de forma automática);
- al menos un valor no vacío.

Siempre se añaden además `_level` (etiqueta «Criticidad») y `_suspicious` (etiqueta «Solo
sospechosos»), que son conceptos del dashboard y no del archivo.

Cada campo devuelve sus **hasta 12 valores más frecuentes** con su conteo, más `distinct` real
para que la UI pueda decir «+ 4 valores más».

### 4.5 Refactor de `/rows`

`/rows` deja de filtrar en línea y pasa a `parse_filters` + `apply_filters`. Comportamiento
observable **idéntico** (mismos parámetros, mismas reglas), salvo que el filtrado ahora ocurre
sobre el frame ya enriquecido: sin recalcular `_level`/`_reason`/`_suspicious` por request.

## 5. Frontend

### 5.1 `useFilterStore` (`frontend/src/store/filters.ts`)

```ts
type FacetValue = { field: string; value: string }
type FilterState = {
  from: string | null; to: string | null;          // ISO
  level: string; suspicious: boolean; result: string; q: string
  facets: Record<string, string[]>
  // acciones
  setRange(from: string | null, to: string | null): void
  toggleFacet(field: string, value: string): void
  setLevel(level: string): void
  setQ(q: string): void
  clearAll(): void
}
```

Selector `selectActiveFilterCount` (chips a mostrar) y `isFiltered` (¿hay algo activo?).
**Al cambiar el `analysisId` se limpia todo el store**: los filtros de un dataset no pueden apuntar
a otro (mismo criterio que `DataTable.tsx:70-80`).

### 5.2 `useScopedSummary()` (hook nuevo, `frontend/src/hooks/useScopedSummary.ts`)

- Sin filtros activos → devuelve el `summary` global del `useAnalysisStore`. **Cero peticiones.**
- Con filtros → `getScopedSummary(filters)` con **debounce de 300 ms** y cancelación vía
  `AbortController` (una respuesta vieja nunca pisa a una reciente).
- Devuelve `{ summary, total, filteredTotal, loading, error }`.

Cambio en `Dashboard.tsx:77`: `useAnalysisStore(s => s.summary)` → `useScopedSummary()`.
**Los 4 gráficos no se modifican**: leen los mismos campos del mismo objeto.

### 5.3 Barra de filtros activos

Bajo las tarjetas KPI, visible solo si `isFiltered`:

- chips removibles: `Hostname: DC-01 ×` · `12/01–15/01 ×` · `Solo sospechosos ×`
- contador **«243 de 715 registros»**
- botón «Limpiar filtros»

Sin esta barra el usuario no sabe que el filtro está activo, que es el fallo más común de este
tipo de dashboards.

### 5.4 Estados

| Estado | Comportamiento |
|---|---|
| Carga | Resumen anterior **atenuado** (opacidad ~0.45), sin spinner que salte. |
| Respuesta obsoleta | `AbortController` cancela la petición anterior al cambiar filtros. |
| Sin coincidencias | `EmptyState` existente: «Ningún registro coincide con los filtros activos». |
| Error de red | Aviso discreto + **volver al resumen global**. Nunca un dashboard en blanco. |

## 6. Contratos de datos (frontend)

```ts
// frontend/src/api/client.ts
export type ScopedSummary = AnalysisSummary & { total?: number; filtered_total?: number }
export type FacetValue = { value: string; count: number }
export type FacetField = { name: string; label: string; distinct: number; values: FacetValue[] }
export const getScopedSummary = (f: FilterParams, signal?: AbortSignal) => Promise<ScopedSummary>
export const getFacets = (f: FilterParams, signal?: AbortSignal) => Promise<{ total: number; filtered_total: number; fields: FacetField[] }>
```

`total` y `filtered_total` se agregan como **opcionales** a `AnalysisSummary` para no romper el
análisis inicial, que no los trae.

## 7. Rendimiento y escalabilidad

- El navegador **nunca** recibe más de 200 filas por petición; todo lo demás son agregados.
- El enriquecimiento se paga **una vez** por análisis (§4.1); después filtrar es un `loc` booleano.
- `call_ai_service` se ejecuta sobre el subconjunto filtrado: con filtros estrechos es más rápido
  que el cálculo inicial, no más lento.
- Sin caché de respuestas: el DataFrame en memoria es la única fuente de verdad. Si un día el
  dataset crece al punto de doler, se cacheará el resultado por hash de filtros — **fuera de
  alcance hoy**.

## 8. Manejo de errores

| Situación | Respuesta |
|---|---|
| `analysis_id` desconocido | 404 (igual que `/rows`) |
| Análisis aún procesando | 409 |
| `from > to` | 400 con detalle legible |
| `level` fuera del catálogo | 400 |
| Faceta sobre columna inexistente | se ignora (tolerancia a URLs viejas) |
| Archivo sin columna `Timestamp` | `from`/`to` se ignoran; la faceta temporal no se ofrece |
| Archivo sin `Severity` | `_level` = `"bajo"` en todas las filas (comportamiento actual) |
| Todos los valores filtrados quedan fuera | 200 con `rows: 0`; la UI muestra `EmptyState` |

## 9. Pruebas

**Backend (pytest)** — con un Excel sintético de 2 hojas y ~40 filas (host, usuario, IP, Event_ID,
Result y Timestamp conocidos):

1. `/summary` sin filtros ⇒ idéntico al resumen global, `total == filtered_total == rows`.
2. `/summary` con ventana temporal ⇒ solo filas del rango; `rows` acotado.
3. `/summary` con 2 facetas de campos distintos ⇒ AND entre campos.
4. `/summary` con 2 valores de la misma faceta ⇒ OR dentro del campo.
5. `/summary` con `level` + ventana temporal combinados.
6. `/summary` sin coincidencias ⇒ `rows == 0`, sigue siendo 200.
7. `/facets` ⇒ columnas de baja cardinalidad detectadas, `Event_Description` y `Log_ID` ausentes.
8. `/facets` con filtro activo ⇒ conteos acotados.
9. `from > to` ⇒ 400. Faceta inexistente ⇒ ignorada.
10. `/rows` tras el refactor ⇒ mismos resultados que antes para los 4 filtros existentes
    (**regresión obligatoria**: es el refactor más delicado del lote).

**Frontend**: `npx tsc --noEmit` + `npm run build`; recorrido manual: filtrar por host + ventana
temporal y verificar que KPIs, donut, hosts, serie y tabla cambian, que el contador muestra
«X de Y», y que «Limpiar filtros» restaura todo.

## 10. Criterios de aceptación

1. Filtrar por una ventana temporal cambia **todos** los gráficos del dashboard y la tabla de
   registros, sin recargar la página.
2. Combinar ventana temporal + una faceta + «solo sospechosos» funciona y el contador es correcto.
3. Cargar un Excel con columnas distintas (p. ej. sin `Destination`, con `Department` extra) deja
   nuevas facetas disponibles **sin tocar código**.
4. Con filtros activos, el dashboard hace **una sola** petición por cambio (más la de facetas si el
   panel está abierto).
5. Sin filtros, el arranque del dashboard **no hace ninguna petición adicional** respecto de hoy.
6. `/rows` sigue comportándose igual que antes (prueba 10).
7. `npx tsc --noEmit` y `npm run build` limpios; `pytest` en verde.

## 11. Fuera de alcance (YAGNI)

- La línea de tiempo con brush → **sub‑proyecto 2**.
- El panel visual de campos/facetas → **sub‑proyecto 3**.
- Serializar filtros en la URL (compartible vía link).
- Caché de respuestas y paginación de facetas.
- Filtros aplicados al informe técnico.
- Cualquier cambio en el modelo de IA o en el RAG.

## 12. Riesgos abiertos

| Riesgo | Mitigación |
|---|---|
| El refactor de `/rows` rompe la tabla existente | Prueba 10 de regresión obligatoria antes de tocar el frontend. |
| `row_reason`/`_is_suspicious` por fila se vuelven lentos en ingestas grandes | Se pagan una sola vez; si en el futuro duelen, se vectorizan. No bloquea este diseño. |
| La detección de facetas elige campos inútiles en algún archivo | Umbral de cardinalidad documentado y ajustable en un solo lugar (`detect_facets`). |
| El resumen filtrado tarda en datasets enormes | Ya se calcula sobre el subconjunto; si hace falta, cachear por hash de filtros (fuera de alcance). |
