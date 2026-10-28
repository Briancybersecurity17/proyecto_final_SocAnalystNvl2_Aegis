# Panel único estilo Splunk (sub‑proyecto 3) — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Una sola pantalla en el Dashboard — KPIs, tarjetas de nivel, barra de filtros sticky, observaciones de IA plegables, histograma, **tabla de registros** y gráficos en dos filas — donde cada gráfico es un selector del filtro global (facet‑exclusion), «Carga de archivos» gana historial de subidas y desaparece el panel de Severity.

**Architecture:** Tres instancias de `useScopedSummary` con parámetro `omit` (completa / sin `level` / sin faceta `Hostname`), clics de tarjetas‑donut→`setLevel` y barras de host→`toggleFacet`, `DashboardSection` colapsable para el panel de IA, endpoint nuevo `GET /api/analyses` + componente `UploadHistory`, y el `DataTable` existente mudado al Dashboard sin rediseño.

**Tech Stack:** React + TypeScript + Vite, Zustand, Recharts; FastAPI + pandas + pytest (1 endpoint nuevo). Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-10-27-panel-splunk-dashboard-design.md` (el plan argumenta desde la spec; quien implementa lee ambas)

## Global Constraints

- Sin dependencias nuevas en el frontend. Comentarios en español.
- **Baseline verificado hoy:** `pytest` → **45 passed**; `npx tsc --noEmit` → exit 0. Tras el Task 1: **48 passed** (45 + 3 nuevos). Ningún test existente se modifica.
- Gate verde por tarea de frontend: `npx tsc --noEmit` (exit 0) + `npm run build` (exit 0, solo aviso preexistente de chunk >500 kB), ejecutados desde `frontend/`.
- Gate verde por tarea de backend: `backend\.venv\Scripts\python.exe -m pytest backend\tests -q`.
- **Cuota de peticiones (D4):** ≤3 peticiones `/summary` por *release* de filtros (completa + omit‑level + omit‑hostname), y **0** para una instancia `omit` cuyos filtros efectivos quedan vacíos. Nota para el verificador: el criterio de sub‑2 («1 petición por release») queda **supersedido** por este — 3 en un arrastre del brush es correcto, no una regresión.
- Sin cambios en los contratos de `/rows`, `/summary`, `/facets` ni en su comportamiento; el único backend nuevo es el endpoint del Task 1.
- `DataTable` se **mueve sin rediseño** (mismos 12 filtros/`hud-table`/25 filas/paginación). Clic en fila = nada.
- `analysis_store` es in‑memory: el historial muere al reiniciar el backend (aceptado en spec §4; el 404 del reabrir debe degradarse con aviso, nunca con error rojo).
- Servidores para recorridos: Vite `:5300` (proxy `/api` → `:8500`), uvicorn `:8500` con `--reload`.
- Commits por tarea, mensaje en español sin acentos (patrón de sub‑1/sub‑2).

## Review Focus

Lo que ningún test unitario cubre y la spec sí exige; cada punto queda anclado al task que posee el código:

1. **Cuota de peticiones** — un clic de nivel ⇒ 2 `/summary` (completa + omit‑hostname; omit‑level = 0), clic de host ⇒ 2, arrastre del brush ⇒ 3, y ninguna instancia emite con sus filtros efectivos vacíos → Task 6: medir con Resource Timing en el recorrido.
2. **`noMatches` deja histograma, tabla, chips e IA visibles** y solo oculta los 4 gráficos de las dos filas → Tasks 5 y 9: buscar un término sin coincidencias y comprobar que la tabla y la tira siguen ahí para poder deshacer.
3. **Donut con 4 sectores y hosts completos con su filtro activo** (el bug de sub‑1: filtrar «Alto» colapsaba el donut a 8/715) → Task 6: clic en «Crítico» y verificar 97/8/7/603 (o los del archivo subido) con el activo resaltado; clic en «DC‑01» y verificar todos los hosts visibles.
4. **Panel de IA colapsable sin perder texto** y arrancando expandido → Tasks 4/5: plegar/desplegar y comprobar que el texto completo vuelve íntegro (sin line‑clamp ni resumen).
5. **Barra sticky legible al fondo de la página** (fondo transparente ⇒ contenido encima; `top-0` mal calculado ⇒ se va bajo el encabezado) → Task 7: scroll hasta el final y verificar chips/contador/Limpiar filtros visibles y sin sangrado de texto.
6. **Reabrir análisis y degradación del 404** → Task 2: reabrir desde el historial (dashboard completo con filtros reseteados) y, tras reiniciar uvicorn, volver a pulsar «Reabrir»: aviso global + lista refrescada, sin excepción en consola.
7. **Borrado completo del panel Severity** (sin imports/estados huérfanos: `rawSeverityData`, `focusRaw`, `pickRaw`, `selectedRaw`) → Task 5: el `tsc` sin `noUnusedLocals` sueltos + grep de `severity_counts`/`focusRaw` en `Dashboard.tsx` sin restos.

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `backend/tests/test_endpoints.py` | +3 tests: contrato de `/api/analyses` (Task 1) |
| `backend/main.py` | `GET /api/analyses` (Task 1) |
| `frontend/src/api/client.ts` | +`RecentAnalysis`, +`getRecentAnalyses` (Task 2) |
| `frontend/src/components/UploadHistory.tsx` | historial de subidas + reabrir (Task 2) |
| `frontend/src/components/UploadSection.tsx` | monta `<UploadHistory />` (Task 2) y quita `<DataTable />` (Task 5, junto con su montaje en el Dashboard) |
| `frontend/src/hooks/useScopedSummary.ts` | +parámetro `omit` / `OmitSpec` / `applyOmit` (Task 3) |
| `frontend/src/components/DashboardSection.tsx` | +props `collapsible` / `defaultCollapsed` (Task 4) |
| `frontend/src/components/Dashboard.tsx` | orden de §3.1, 3 instancias del hook, clics como filtros, sticky, compacto, copys (Tasks 5–8) |
| `frontend/src/components/TimelineBrush.tsx` | pista de filtrado (Task 8) |

---

### Task 1: Backend — `GET /api/analyses` (TDD)

Contrato de §3.5: últimos 8 análisis con `status == 'done'`, más reciente primero, lista vacía si el store está vacío. No toca RAG.

**Files:**
- Modify: `backend/tests/test_endpoints.py` (añadir al final del archivo)
- Modify: `backend/main.py` (endpoint nuevo tras `get_analysis`, ~línea 826)

**Interfaces:**
- Consumes: `analysis_store` (`backend/main.py:805+`) y `summary["file_name"]` que `process_analysis` ya guarda (`backend/main.py:740`).
- Produces (para Task 2): `GET /api/analyses` → JSON **array** de `{ analysis_id: str, file_name: str, generated_at: str, rows: int, risk: str }`.

- [ ] **Step 1: Escribir los 3 tests (ROJO)**

Añadir al final de `backend/tests/test_endpoints.py`:

```python
# --- Historial de subidas (spec sub-3 §3.5) --------------------------------

def test_analyses_lista_solo_done_con_campos(client, analysis_id, analysis_id_minimo):
    """Los análisis completados aparecen con los 5 campos del contrato."""
    r = client.get("/api/analyses")
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list) and items
    ids = [item["analysis_id"] for item in items]
    assert analysis_id in ids
    assert analysis_id_minimo in ids
    for item in items:
        assert set(item) == {"analysis_id", "file_name", "generated_at", "rows", "risk"}
        assert item["file_name"]
        assert item["generated_at"]
        assert item["rows"] > 0


def test_analyses_orden_inverso_y_limite_8(client):
    """Lo más reciente primero y nunca más de 8 entradas."""
    from main import analysis_store

    sinteticos = {
        f"synth-{n}": {
            "status": "done",
            "summary": {
                "file_name": f"f{n}.xlsx",
                "generated_at": "2026-10-27T00:00:00",
                "rows": n + 1,
                "risk": "bajo",
            },
            "error": None,
        }
        for n in range(10)
    }
    analysis_store.update(sinteticos)
    try:
        items = client.get("/api/analyses").json()
        assert len(items) == 8
        assert items[0]["analysis_id"] == "synth-9"
        assert items[-1]["analysis_id"] == "synth-2"
    finally:
        for key in sinteticos:
            analysis_store.pop(key, None)


def test_analyses_store_vacio_devuelve_lista_vacia(client):
    """Reinicio del backend: lista vacía, nunca un 500."""
    from main import analysis_store

    backup = dict(analysis_store)
    analysis_store.clear()
    try:
        r = client.get("/api/analyses")
        assert r.status_code == 200
        assert r.json() == []
    finally:
        analysis_store.update(backup)
```

- [ ] **Step 2: Ejecutar solo los 3 tests nuevos**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_endpoints.py -k analyses -v`
Expected: **3 failed** (el endpoint aún no existe → 404). Si falla otro test existente con `-k`, revisar el patrón de nombres.

- [ ] **Step 3: Implementar el endpoint**

En `backend/main.py`, justo después del endpoint `get_analysis` (~línea 826):

```python
@app.get("/api/analyses")
async def list_analyses(limit: int = 8):
    """Historial de subidas (spec sub-3 §3.5): últimos análisis completados.

    Solo `status == 'done'`: un análisis pending/error no se puede reabrir.
    El orden de inserción del dict es cronológico, así que se invierte para
    mostrar lo más reciente primero. No depende de RAG: el dashboard y la
    tabla se alimentan del `analysis_store`, no del índice.
    """
    done = [
        (key, value)
        for key, value in analysis_store.items()
        if value.get("status") == "done"
    ]
    out = []
    for key, value in reversed(done[-limit:]):
        summary = value.get("summary") or {}
        out.append({
            "analysis_id": key,
            "file_name": summary.get("file_name") or "archivo importado",
            "generated_at": summary.get("generated_at"),
            "rows": summary.get("rows", 0),
            "risk": summary.get("risk") or "—",
        })
    return out
```

- [ ] **Step 4: Los 3 tests nuevos en verde**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_endpoints.py -k analyses -v`
Expected: **3 passed**.

- [ ] **Step 5: Suite completa**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests -q`
Expected: **48 passed** (45 existentes + 3 nuevos), 1 warning starlette preexistente.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_endpoints.py
git commit -m "feat(api): endpoint /api/analyses para el historial de subidas"
```

---

### Task 2: Frontend — `getRecentAnalyses` + componente `UploadHistory`

La sección «Carga de archivos» queda: drop‑zone + resultado (`UploadExcel`) + **historial** (nuevo). El `DataTable` se quita aquí (se monta en el Dashboard en el Task 5; hacerlo en el mismo task evita una pantalla sin tabla entre tasks).

**Files:**
- Modify: `frontend/src/api/client.ts` (añadir tipo + función cerca de `getScopedSummary`)
- Create: `frontend/src/components/UploadHistory.tsx`
- Modify: `frontend/src/components/UploadSection.tsx` (montar `<UploadHistory />` tras el `EmptyState`; **el `<DataTable />` se sigue viendo aquí hasta el Task 5**, que lo muda al Dashboard en un mismo commit — así nunca hay una pantalla sin tabla)

**Interfaces:**
- Consumes: `getAnalysis` (`client.ts:36`), `useAnalysisStore` (`setAnalysisId/setSummary/setStatus/setNotice`, `store/index.ts:114‑134`), `useUiStore.setSection` (`store/index.ts:157`), `LEVEL_COLORS` (`charts.ts:24`), `DashboardSection`.
- Produces:
  - `client.ts` → `export type RecentAnalysis = { analysis_id: string; file_name: string; generated_at: string | null; rows: number; risk: string }`
  - `client.ts` → `export const getRecentAnalyses = async (signal?: AbortSignal): Promise<RecentAnalysis[]>` (GET `/analyses` con `API`; el `baseURL: '/api'` ya pone el prefijo).

- [ ] **Step 1: Tipo y función en `client.ts`**

Junto a `getScopedSummary`:

```ts
/** Fila del historial de subidas (GET /analyses, spec sub-3 §3.5). */
export type RecentAnalysis = {
  analysis_id: string
  file_name: string
  generated_at: string | null
  rows: number
  risk: string
}

/** Últimos análisis completados en el backend, lo más reciente primero. */
export const getRecentAnalyses = async (signal?: AbortSignal): Promise<RecentAnalysis[]> => {
  const response = await API.get<RecentAnalysis[]>('/analyses', { signal })
  return response.data
}
```

- [ ] **Step 2: Crear `UploadHistory.tsx`**

Comportamiento (patrón HUD del proyecto, comentarios en español):

- Estado: `items: RecentAnalysis[]`, `loading`, `opening: string | null` (id en curso) y `loadFailed` opcional.
- **Carga**: al montar y en cada transición de `status` a `'done'` (subida nueva). Patrón anti doble‑fetch: un `useRef(true)` consume la primera ejecución del efecto `[status]`; después solo se recarga cuando `status === 'done'`. Usar `AbortController` guardado en ref y abortar en cleanup; un abort no pinta error.
- **Fila por análisis**: nombre del archivo, fecha (`generated_at` con `new Date(...).toLocaleString('es‑ES')` si existe), `rows` con `fmtCount`, chip de riesgo coloreado con `LEVEL_COLORS[risk]` (fallback `#1fc8ff`) y botón «Reabrir» (`btn-hud`, deshabilitado mientras `opening` no sea `null`).
- **Reabrir** (`reopen(id)`):

```ts
const res = await getAnalysis(id)
// Sin summary => el análisis terminó en error en el backend.
if (!res.summary) { setNotice('Ese análisis terminó con error y no se puede reabrir.'); return }
setStatus('done')
setAnalysisId(res.analysis_id)
setSummary(res.summary)
setSection('dashboard')
```

  `catch` (404 tras reiniciar uvicorn): `setNotice('Ese análisis ya no está en memoria (¿se reinició el backend?). Se actualiza la lista.')` y recargar la lista.
  El reset de filtros por cambio de `analysisId` ya lo hace `useScopedSummary` (líneas 64‑70): no replicarlo aquí.
- **Lista vacía**: párrafo tranquilo «Aún no hay análisis previos — subí un archivo para empezar.», sin alerta ni color de error.
- **Render**: `<DashboardSection title="Historial de subidas">` con filas `border-b border-hud-soft` tipo leyenda del donut (nombre · fecha · N registros · chip riesgo · botón).

- [ ] **Step 3: Montar en `UploadSection.tsx`**

- Añadir `import UploadHistory from './UploadHistory'` y `<UploadHistory />` al final del `<section>` (después del `EmptyState` de `idle`).
- Actualizar el JSDoc del componente: «drop‑zone + resultado + historial; los registros se ven en el Dashboard (se mudan en el Task 5)». **No quitar todavía `<DataTable />`.**

- [ ] **Step 4: Gate frontend**

Run (desde `frontend/`): `npx tsc --noEmit` → exit 0; `npm run build` → exit 0 (solo aviso preexistente de chunk).

- [ ] **Step 5: Recorrido rápido**

`localhost:5300` → sección «Carga de archivos»: historial visible con los análisis del backend en marcha (o texto vacío si uvicorn acaba de arrancar); subir un Excel nuevo ⇒ la lista lo incluye al terminar; pulsar «Reabrir» ⇒ cambia a «Dashboard» con el análisis cargado y filtros limpios.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/UploadHistory.tsx frontend/src/components/UploadSection.tsx
git commit -m "feat(front): historial de subidas con reapertura de analisis"
```

---

### Task 3: `useScopedSummary` gana el parámetro `omit` (facet‑exclusion, D2/D3)

Refactor con contrato opcional: la llamada existente `useScopedSummary()` **no cambia de comportamiento** (0 peticiones sin filtros, mismo debounce/abort/reset). La lógica vive una sola vez para las tres instancias.

**Files:**
- Modify: `frontend/src/hooks/useScopedSummary.ts`

**Interfaces:**
- Consumes: `FilterState` y `selectIsFiltered` (`store/filters.ts`, ambos ya exportados).
- Produces (los Tasks 6 consume):
  - `export type OmitSpec = { level?: boolean; facets?: readonly string[] }`
  - `export const useScopedSummary = (omit?: OmitSpec): ScopedSummaryResult` (firma con parámetro opcional)

- [ ] **Step 1: `OmitSpec` + `applyOmit`**

```ts
/**
 * Filtros que una vista ignora (facet‑exclusion, spec sub‑3 D2/D3):
 * un gráfico omite el filtro de SU dimensión y respeta el resto.
 */
export type OmitSpec = {
  /** Ignora el filtro de nivel (donut y tarjetas de nivel). */
  level?: boolean
  /** Ignora estas facetas (gráfico de hosts: ['Hostname']). */
  facets?: readonly string[]
}

/** Copia de los filtros quitando los que la vista decide ignorar. */
const applyOmit = (f: FilterState, omit?: OmitSpec): FilterState => {
  if (!omit) return f
  let facets = f.facets
  if (omit.facets?.length) {
    const fuera = omit.facets
    facets = Object.fromEntries(Object.entries(f.facets).filter(([campo]) => !fuera.includes(campo)))
  }
  return { ...f, level: omit.level ? '' : f.level, facets }
}
```

- [ ] **Step 2: Cablear `omit` en el hook**

Tres puntos (numeraciones del archivo actual):

1. Firma (~línea 32): `export const useScopedSummary = (omit?: OmitSpec): ScopedSummaryResult => {`.
2. Snapshot efectivo (~líneas 34‑35): `const filters = useFilterStore()` → `const effective = applyOmit(filters, omit)`; `const isFiltered = selectIsFiltered(effective)`.
3. `filtersKey` (~líneas 44‑54): serializar `effective.*` en vez de `filters.*` (mismo formato, mismas claves ordenadas).
4. Dentro del efecto (~línea 88): `const f = applyOmit(useFilterStore.getState(), omit)` — el resto del bloque (guardia `selectIsFiltered(f)`, construcción de params, abort, error) **queda idéntico**: `level: f.level || undefined` ya llega vacío cuando se omite.
5. El efecto de reset por `analysisId` (~líneas 64‑70) y los returns (~133‑145) no cambian.

Nada de esto mete `omit` en los `deps` del efecto: los call sites pasan constantes de módulo (Task 6), así el contenido de `omit` nunca cambia durante la vida del componente.

- [ ] **Step 3: Gate frontend + smoke**

`npx tsc --noEmit` y `npm run build` desde `frontend/`. Smoke manual: sin filtros la pestaña Network no ve ningún `/summary` (la llamada existente no cambió).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/useScopedSummary.ts
git commit -m "feat(front): useScopedSummary acepta omit para facet-exclusion"
```

---

### Task 4: `DashboardSection` colapsable (panel de IA, D9)

**Files:**
- Modify: `frontend/src/components/DashboardSection.tsx`

**Interfaces:**
- Produces (consume el Task 5): props opcionales `collapsible?: boolean` y `defaultCollapsed?: boolean`.

- [ ] **Step 1: Implementar**

- Convertir el componente a función con `useState(defaultCollapsed ?? false)` (los hooks exigen cuerpo con nombre).
- Props nuevas documentadas en el `interface` (JSDoc en español): `collapsible` = «hace plegable el panel (chevron en la cabecera); solo el panel de IA lo usa»; `defaultCollapsed` = «estado inicial; por defecto expandido (D9)».
- Cuando `collapsible && title`: en el `panel-head`, tras el `h3.panel-title`, un botón (`ml-auto`) con `aria-expanded={!collapsed}`, `aria-label={collapsed ? 'Mostrar <título>' : 'Ocultar <título>'}` y un chevron (`▸` colapsado / `▾` expandido), estilo `border border-hud-soft bg-inset px-1.5 text-hud-line hover:border-hud-line/60 [clip-path:var(--chamfer-sm)]`.
- El `panel-body` se renderiza solo cuando `!collapsed`: **nunca se recorta ni se resume `children`** — colapsar oculta, expandir muestra el texto completo.
- El resto (clases `panel`, hover de sombra) intacto: los paneles sin `collapsible` se ven idénticos a hoy.

- [ ] **Step 2: Gate frontend**

`npx tsc --noEmit` + `npm run build` → exit 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/DashboardSection.tsx
git commit -m "feat(front): DashboardSection admite colapsar (panel de IA)"
```

---

### Task 5: Estructura del Dashboard — orden §3.1, tabla mudada, Severity fuera, `noMatches` acotado

Tarea estructural (JSX + borrados), **sin** cambiar aún de instancia de resumen ni de estado de selección: eso llega en el Task 6. Al terminar, la pantalla tiene el orden aprobado y compila.

**Files:**
- Modify: `frontend/src/components/Dashboard.tsx`
- Modify: `frontend/src/components/UploadSection.tsx` (quitar `<DataTable />` y su import **en este mismo commit**, al montarlo en el Dashboard: la tabla nunca desaparece de la app)

**Interfaces:**
- Consumes: `<DataTable />` (tiene su propio `DashboardSection title="Registros importados (visualización del Excel)"`, sin props), `collapsible` del Task 4.

- [ ] **Step 1: Borrar el panel «Valores originales del campo Severity»** (D13)

- Bloque completo `<DashboardSection title="Valores originales del campo Severity">…</DashboardSection>` (~líneas 780‑833).
- Estados y datos que quedarían huérfanos: `const [focusRaw, setFocusRaw]` (~79), `rawSeverityData` (~156‑158), `pickRaw` (~234‑239), `const selectedRaw = …` (~242, la línea completa; `totalHostEvents` de la 241 se queda).
- Verificar con grep que `Dashboard.tsx` no menciona `focusRaw`, `pickRaw`, `rawSeverityData` ni `selectedRaw`. `severity_counts` puede seguir viniendo del backend (sin cambios).

- [ ] **Step 2: Borrar el desplegable «Nivel» local del gráfico de hosts** (D8)

- `const [hostLevel, setHostLevel] = useState<string>('todos')` (~73).
- El `<label>…Nivel <select>…</select></label>` del header del panel (~694‑704) y el copy «podés enfocar un nivel de riesgo concreto» → por ahora solo acortar a «Registros por host.» (el copy interactivo fino lo pule el Task 6).
- En `hostsData` (~172‑178): el `total` pasa a ser siempre `critico + alto + medio + bajo` (sin el ternario de `hostLevel`).
- `const shownLevels = …` (~181): eliminar y usar `LEVEL_ORDER` directamente en el `map` del JSX (~724).
- `stackId={hostLevel === 'todos' ? 'hosts' : undefined}` → `stackId="hosts"` (~729).
- `ChartInsight` de hosts: el `note` con ramas de `hostLevel` (~769‑773) → `'Reparto calculado de los registros agrupados por host en el resumen del análisis.'`.

- [ ] **Step 3: Reordenar paneles (orden aprobado, spec §3.1)**

Dentro del `return` principal (`<section className="space-y-8 …">`), el orden final es:

1. Cabecera de archivo (ya está, ~249‑259).
2. KPIs `lg:grid-cols-5` (ya está, ~262‑276).
3. Tarjetas de nivel `lg:grid-cols-4` (ya está, ~279‑312).
4. Barra de chips (~317‑348; se hace sticky en el Task 7 — aquí solo se mueve si hace falta, no se toca su estilo).
5. **Panel de IA** (~357‑379): sacarlo del ternario de `noMatches` y dejarlo siempre visible, con `collapsible` y `defaultCollapsed={false}`:
   `<DashboardSection title="Observaciones del análisis asistido por IA" collapsible>`. Dentro, sin cambios (el texto completo se conserva: D9).
6. **`<TimelineBrush />`** (hoy en ~515, dentro del ternario): mover a continuación de la IA — el histograma es control global y debe verse siempre (criterio 7).
7. **`<DataTable />`** nuevo: `import DataTable from './DataTable'` y montarlo tras el brush, a ancho completo.
8. **Fila 2 columnas** — envolver donut (~382‑512) y hosts (~688‑778) en:
   `<div className="grid grid-cols-1 gap-6 xl:grid-cols-2">` (el donut lleva su propia leyenda a la derecha; `xl` evita apretarla por debajo de 1280 px).
9. **Fila 2 columnas** — envolver «Actividad temporal por criticidad» (~518‑686) y «Justificación del nivel de riesgo» (~836‑…) en otro `grid xl:grid-cols-2` (la actividad conserva área suave, eje Y, tooltip y `ChartInsight`: D14).
10. El panel de Severity ya no existe.

- [ ] **Step 4: `noMatches` acotado a los 4 gráficos** (D12)

El ternario pasa a envolver **solo las dos filas nuevas**:

```tsx
{/* Sin coincidencias: solo ceden los 4 gráficos. Cabecera, KPIs, tarjetas,
    chips, IA, tira y tabla siguen a la vista: son los controles con los que
    se sale del callejón (spec sub-3 D12). */}
{noMatches ? (
  <EmptyState hint="Ningún registro coincide con los filtros activos" cta={false} />
) : (
  <>
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">…donut…hosts…</div>
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">…actividad…justificación…</div>
  </>
)}
```

Actualizar el comentario de `noMatches` (~139‑141) para reflejar que ya no se ocultan tira/tabla/IA.

- [ ] **Step 5: Gate frontend + recorrido estructural**

`npx tsc --noEmit` + `npm run build` → exit 0. Navegador: comprobar 1) el orden 1‑9 con un archivo subido; 2) la tabla dentro del Dashboard y **fuera** de «Carga de archivos»; 3) sin panel de Severity; 4) al filtrar a cero, la tira, la tabla, la IA y los chips siguen visibles y solo los gráficos se sustituyen por el `EmptyState`. (El donut/tarjetas aún leen el resumen completo: hasta el Task 6 no reaccionan bien a `level`.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Dashboard.tsx frontend/src/components/UploadSection.tsx
git commit -m "feat(front): panel unico - orden aprobado, tabla en dashboard, sin severity"
```

---

### Task 6: Facet‑exclusion + clics de gráficos como filtros globales (D2/D3/D7)

**Files:**
- Modify: `frontend/src/components/Dashboard.tsx`

**Interfaces:**
- Consumes: `useScopedSummary(OmitSpec)` (Task 3 — importar también `type OmitSpec` desde `'../hooks/useScopedSummary'`), `setLevel` / `toggleFacet` (`store/filters.ts:63‑74`), `selectionCells` (`ChartTools`).
- Produces: comportamiento de los criterios 2‑4.

- [ ] **Step 1: Tres instancias del hook**

```ts
// Qué filtra ignora cada vista (facet-exclusion, spec sub-3 D2/D3).
// Constantes de módulo: la identidad del `omit` no cambia entre renders.
const OMIT_LEVEL: OmitSpec = { level: true }
const OMIT_HOSTS: OmitSpec = { facets: ['Hostname'] }

const { summary, total, filteredTotal, loading, error } = useScopedSummary()               // KPIs, tabla, IA, actividad, justificación, chips
const { summary: summaryNoLevel } = useScopedSummary(OMIT_LEVEL)                           // donut + tarjetas de nivel
const { summary: summaryNoHosts } = useScopedSummary(OMIT_HOSTS)                           // gráfico de hosts
```

La guardia `if (!summary) return <EmptyState …/>` (~82) pasa a comprobar los tres (`!summary || !summaryNoLevel || !summaryNoHosts`) — nunca divergen en la práctica (mismo global).

- [ ] **Step 2: Reencablear los datos derivados**

- `rows`/`suspicious`/KPIs/`timeData`/`risk_justification`/`ai_insights`/`noMatches`: **resumen completo** (sin cambios).
- `rowsNoLevel = toCount(summaryNoLevel.rows)`; `levelCounts = summaryNoLevel.level_counts ?? {}` (de ahí salen `severityData` de tarjetas y donut); en las tarjetas el `pct(count, rows)` pasa a `pct(count, rowsNoLevel)`.
- `topHostsForLevel` (~190‑196) y el denominador del `ChartInsight` del donut: `summaryNoLevel.top_hosts_by_level` / `rowsNoLevel`.
- `hostsData` (~164) y `totalHostEvents` (~241): `summaryNoHosts.top_hosts_by_level`.
- `riskBlock` (~186): `summary.risk_levels` del resumen completo (metadato del análisis, no se acota).

- [ ] **Step 3: Tarjetas de nivel y donut → `setLevel` con toggle** (D7)

- Borrar `const [focusLevel, setFocusLevel]` (~76). Definir:
  `const activeLevel = filters.level || null` y
  `const toggleLevel = (nivel: string) => filters.setLevel(filters.level === nivel ? '' : nivel)`.
- `selectedIndex`/`selectedLevel` (~184‑185) y `riskBlock` (~186) leen `activeLevel`. Al terminar, un grep de `focusLevel`/`setFocusLevel` en `Dashboard.tsx` debe dar 0 resultados.
- **Tarjetas** (~289‑310): añadir `onClick={() => toggleLevel(level)}`, `role="button"`, `aria-pressed={filters.level === level}`, `tabIndex={0}` con `onKeyDown` (Enter/Espacio ⇒ `toggleLevel`) y, cuando esté activa, clase extra `border-hud-line/70 shadow-[0_0_18px_rgba(31,200,255,0.45)]` (el `borderLeft` de color se mantiene). `cursor-pointer` en la tarjeta.
- **Donut** (~418‑422): `onClick` del `Pie` → `entry?.level && toggleLevel(entry.level)` (el sector activo se resalta vía `activeIndex`/`selectedLevel`; **los 4 sectores se pintan siempre** — los datos ya vienen de `summaryNoLevel`, nunca colapsan).
- **Leyenda del donut** (~456‑460): `onClick={() => toggleLevel(item.level)}`, `aria-pressed={activeLevel === item.level}`, atenuado con `activeLevel !== null && activeLevel !== item.level`.
- `ChartInsight` del donut: `onClose={() => filters.setLevel('')}`.
- Intro del panel (~386): «Pulsá un nivel —o su fila— para filtrar toda la pantalla por él.»
- `setFocusLevel(...)` no debe sobrar en ningún sitio (grep).

- [ ] **Step 4: Barras de hosts → `toggleFacet('Hostname', host)`** (D7)

- Borrar `const [focusHost, setFocusHost]` (~78). Derivar del store:
  `const activeHosts = filters.facets['Hostname'] ?? []` y `const focusHost = activeHosts.length === 1 ? activeHosts[0] : null`.
  (Con una sola selección se resalta la barra con `selectionCells` y se abre el `ChartInsight`; con varias, la selección vive en los chips y el resaltado se apaga — simplificación aceptada.)
- `pickHost` (~228‑232) → `filters.toggleFacet('Hostname', host)` (misma lectura de `state.activePayload?.[0]?.payload?.host`).
- `onClose` del `ChartInsight` de hosts: liberar todas las facetas activas del campo:
  `onClose={() => activeHosts.forEach(h => filters.toggleFacet('Hostname', h))}`.
- Hint del panel (~749‑751): «Pulsá una barra para filtrar por ese host; volvé a pulsar para liberarlo.» y el copy del header (~691‑693): «Registros por host, repartidos por criticidad.»

- [ ] **Step 5: Gate frontend + recorrido de clics (criterios 2, 3, 4)**

`npx tsc --noEmit` + `npm run build` → exit 0. Navegador (con el Excel de 715 filas o el que haya):

1. Clic en «Crítico» (tarjeta y sector del donut) ⇒ KPIs, tabla, hosts, actividad, justificación e IA se acotan; **donut y tarjetas siguen mostrando los 4 niveles** con el activo resaltado; re‑clic limpia.
2. Clic en la barra «DC‑01» ⇒ chip `Hostname: DC‑01` en la barra, **todos los hosts visibles** con DC‑01 resaltado, resto de la pantalla acotado; re‑clic (o «×» del chip) limpia.
3. Resource Timing: clic de nivel/host ⇒ **2** `/summary` por release; arrastre del brush ⇒ **3**; con solo el filtro de nivel activo, la vista omit‑level no emite (**0**).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Dashboard.tsx
git commit -m "feat(front): facet-exclusion y clics de graficos como filtro global"
```

---

### Task 7: Barra de filtros sticky + tarjetas compactas (D14/D15)

**Files:**
- Modify: `frontend/src/components/Dashboard.tsx` (barra de chips ~317‑348, tarjetas)
- Modify: `frontend/src/components/SummaryCard.tsx` (solo padding)

**Interfaces:**
- Consumes: clases HUD existentes (`.chip`, `.hud-label`, `.btn-hud`, `--chamfer-sm`).

- [ ] **Step 1: Barra sticky siempre visible**

- La barra deja de renderizarse solo con `isFiltered &&`: se pinta siempre (contador «X de Y» útil también sin filtros); los chips siguen saliendo de `activeChips` (vacío ⇒ sin chips) y «Limpiar filtros» solo cuando `isFiltered`.
- Envolver en:

```tsx
<div
  role="group"
  aria-label="Filtros activos"
  className="sticky top-0 z-20 -mx-6 flex flex-wrap items-center gap-2 border-b border-hud-soft bg-[var(--bg-void)]/95 px-6 py-3 backdrop-blur-sm"
>
```

  (`main` es el contenedor de scroll — `App.tsx:99`; `-mx-6 px-6` compensa el `p-6` de la sección para que la barra llegue a los bordes sin scroll horizontal). El bloque de `error` naranja y el contador se quedan dentro.
- Comentario en español actualizado: menciona D15 (sticky, siempre visible).

- [ ] **Step 2: Tarjetas compactas**

- `SummaryCard.tsx`: `p-4` → `p-3`, `mb-2` → `mb-1.5` (tipografías **sin bajar**: el usuario pidió letras algo mayores y ya están ≥ mockup).
- Tarjetas de nivel (Dashboard ~293): `p-4` → `p-3`. El alto lo marca el contenido (D14).

- [ ] **Step 3: Gate frontend + recorrido (criterio 5)**

`npx tsc --noEmit` + `npm run build` → exit 0. Navegador: con un filtro activo, hacer scroll hasta el final — la barra queda visible con chips/contador/«Limpiar filtros» legibles (sin texto del contenido asomando por debajo del fondo); «Limpiar filtros» restaura la pantalla completa.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Dashboard.tsx frontend/src/components/SummaryCard.tsx
git commit -m "feat(front): barra de filtros sticky y tarjetas compactas"
```

---

### Task 8: Copys corregidos (D16, spec §3.7)

**Files:**
- Modify: `frontend/src/components/Dashboard.tsx`
- Modify: `frontend/src/components/TimelineBrush.tsx`

- [ ] **Step 1: Leyenda del donut — «de la selección»** (§3.7.1, ~línea 496)

```ts
caption={`${pct(selectedLevel.value, rowsNoLevel)}% de los ${rowsNoLevel} registros de la selección`}
```

(El denominador ya es el del alcance sin `level` desde el Task 6; aquí cambia el texto «del archivo» — con filtros activos era mentira.)

- [ ] **Step 2: Fallback diario — causa real** (§3.7.2)

- Caja azul de `hourlyTooLong` (~533‑535): «…se muestra la serie diaria, con los mismos registros **de la selección**.» (antes «del archivo»).
- Caja ámbar `!hourly && timeData.length === 1` (~539‑545): el motivo real es que la selección cayó en un **único tramo horario** (`hourlyData.length === 1` en `charts.ts`), no que «la respuesta no incluye desglose». Texto nuevo (conservando `timeData[0].detail`):

  > Con los filtros actuales los registros quedan en un solo tramo horario ({timeData[0].detail}); se muestra el total del día. Con un solo tramo no hay tendencia que graficar.

- [ ] **Step 3: Pista del histograma** (§3.7.3, `TimelineBrush.tsx:314`)

```tsx
Arrastrá un tramo o pulsá una columna: eso filtra el resto del panel.
```

- [ ] **Step 4: Gate frontend**

`npx tsc --noEmit` + `npm run build` → exit 0; revisar en navegador los 3 textos (con filtros activos y con el archivo de un solo día si se tiene).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Dashboard.tsx frontend/src/components/TimelineBrush.tsx
git commit -m "fix(front): textos de seleccion, fallback diario y pista del histograma"
```

---

### Task 9: Verificación final (criterios de aceptación 1‑13)

**Files:**
- Modify solo si aparecen arreglos (commit `fix(front): ajustes del recorrido de verificacion`).

- [ ] **Step 1: Gates automáticos**

- `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` → **48 passed** (45 + 3), 1 warning preexistente.
- `npx tsc --noEmit` → exit 0; `npm run build` → exit 0 (solo aviso de chunk >500 kB).

- [ ] **Step 2: Recorrido manual completo** (uvicorn `:8500` + Vite `:5300`, Excel subido por la UI)

1. **Orden** (§3.1): cabecera → KPIs ×5 → tarjetas ×4 → barra sticky → IA → tira → tabla → donut ∥ hosts → actividad ∥ justificación; sin panel de Severity; tabla presente en Dashboard y ausente en «Carga de archivos» (criterios 1, 9, 11).
2. **Clic de nivel** (criterio 2): tarjeta y donut filtran el resto; donut/tarjetas con los 4 niveles y el activo resaltado; re‑clic limpia.
3. **Clic de host** (criterio 3): chip activo, todos los hosts visibles con el elegido resaltado; re‑clic y «×» del chip limpian.
4. **Peticiones** (criterio 4): Resource Timing — ≤3 `/summary` por release; 0 de la vista omit‑level con solo `level` activo.
5. **Sticky** (criterio 5): scroll al fondo, barra visible; «Limpiar filtros» restaura.
6. **IA plegable** (criterio 6): arranca expandido; chevron pliega/despliega; texto completo en cada apertura.
7. **Sin coincidencias** (criterio 7): búsqueda imposible ⇒ `EmptyState` solo donde están los 4 gráficos; tira, tabla, IA y chips visibles; al limpiar, los gráficos vuelven.
8. **Historial** (criterio 8): subir un 2º archivo ⇒ aparece en el historial; «Reabrir» carga el primero con filtros limpios y se ve en Dashboard; reiniciar uvicorn y pulsar «Reabrir» en una entrada vieja ⇒ aviso global + lista refrescada, sin excepción en consola.
9. **Copys** (criterio 10): los 3 textos del Task 8 en pantalla.
10. **Regresiones sub‑1/sub‑2** (criterio 12): los 4 filtros (chips, debounce de `q`, un solo `/rows` por cambio, «Limpiar filtros» y el epoch) y el brush (arrastre, clic de tramo, `to` de fin de día, tira global) siguen comportándose; los 45 tests originales en verde.
11. **Observaciones IA** (requisito del usuario): plegar y desplegar N veces — el texto nunca se trunca ni se resume.

- [ ] **Step 3: Arreglos y commit final (solo si hubo hallazgos)**

```bash
git add -A
git commit -m "fix(front): ajustes del recorrido de verificacion"
```

Si todo quedó verde en los tasks anteriores, no hay commit extra: dejar el estado actual y reportar el resultado.
