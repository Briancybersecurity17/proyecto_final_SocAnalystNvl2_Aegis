# Motor de filtros para el dashboard — Plan de implementación (Sub‑proyecto 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que todo el dashboard responda a filtros activos (ventana temporal + facetas detectadas de las columnas del archivo) mediante un endpoint que devuelve el mismo resumen acotado.

**Architecture:** El backend enriquece el DataFrame una sola vez al ingerir, extrae el filtrado a un módulo propio compartido por `/rows` y el nuevo `/summary`, y detecta las facetas del archivo en lugar de tenerlas hard‑coded. El frontend guarda los filtros en un store de zustand y consume el resumen mediante un hook que, sin filtros, no hace ninguna petición extra.

**Tech Stack:** FastAPI + pandas (backend, venv en `backend/.venv`), pytest 9.x (a instalar en ese venv), React 18 + TypeScript + zustand + axios + Recharts (frontend, Vite en `frontend/`).

**Spec:** `docs/superpowers/specs/2026-10-26-motor-de-filtros-dashboard-design.md`

## Global Constraints

- Ejecutar los tests de backend con **`backend/.venv/Scripts/python.exe`** (Windows). Ese venv ya tiene fastapi, pandas, openpyxl y httpx; **no tiene pytest** → instalarlo en la Tarea 1.
- Ejecutar los comandos del frontend desde **`frontend/`**: `npx tsc --noEmit` y `npm run build`.
- Columnas derivadas siempre con prefijo `_` y **nunca** expuestas al navegador como si fueran dato del Excel.
- `level` solo admite `critico|alto|medio|bajo` (`backend/main.py:99`).
- Compatibilidad obligatoria: el comportamiento observable de `GET /rows` **no puede cambiar** (Tarea 6).
- El navegador nunca recibe más de 200 filas por petición.
- Estilo del repo: comentarios en español, docstrings explicando el porqué, sin dependencias nuevas en el frontend.

## Review Focus

Los cinco fallos que el spec implica pero que ninguna prueba aislada cubre. Cada línea tiene su test en la tarea dueña del código.

1. **El refactor de `/rows` rompe la tabla de registros existente** — comportamiento idéntico antes/después para `q`, `level`, `suspicious`, `result`. → Tarea 6, `test_rows_refactor_es_identico`.
2. **Filas sin fecha parseable (`NaT`)** — con filtro temporal deben quedar fuera; sin filtro deben seguir dentro. → Tarea 4, `test_na_sin_filtro_temporal_entra_y_con_filtro_sale`.
3. **Archivo sin columna `Timestamp` o sin `Severity`** — el filtro temporal se ignora y `_level` cae a `bajo`; nada debe explotar con 500. → Tarea 4, `test_summary_archivo_minimo_sin_timestamp_ni_severity`.
4. **Columna de texto libre detectada como faceta** (`Event_Description`) — el panel ofrecería miles de valores inútiles. → Tarea 2, `test_detect_facets_descarta_texto_largo_y_log_id`.
5. **Columnas derivadas filtrando la búsqueda libre** — `q=alto` no debe matchear por `_level` sino por datos del Excel. → Tarea 6, `test_q_no_busca_en_columnas_derivadas`.

---

## Dataset de prueba sintético (compartido por todas las tareas)

Las tareas de backend comparten un Excel en memoria de **12 filas / 3 días** con distribución
exacta. **Las filas están fijadas fila a fila**: las aserciones del plan dependen de esta
disposición, así que no se cambia sin reajustar los tests.

| Log_ID | Timestamp | Hostname | Event_ID | User | Source_IP | Destination | Log_Source | Event_Description | Result | Severity |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-01-10 08:00 | DC-01 | 4624 | jsmith | 10.10.2.15 | DC-01 | Windows | An account was successfully logged on to the system | Success | Informational |
| 2 | 2026-01-10 09:30 | WS-018 | 4625 | jsmith | 10.10.2.15 | WS-018 | Windows | An account failed to log on with an invalid account | Failure | High |
| 3 | 2026-01-10 11:00 | WEB-01 | 401 | anon | 10.10.2.40 | /login | Nginx | An account failed to log on with an invalid account | Failure | High |
| 4 | 2026-01-10 16:00 | DC-01 | 4740 | admin | 10.10.2.20 | DC-01 | Windows | A user account was locked out after failed attempts | Failure | Warning |
| 5 | 2026-01-11 08:00 | FS-SRV01 | 7045 | svc_backup | 10.10.2.30 | FS-SRV01 | Windows | A service was installed successfully in the system | Success | Medium |
| 6 | 2026-01-11 09:15 | WS-018 | 4625 | admin | 10.10.2.15 | WS-018 | Windows | An account failed to log on with an invalid account | Failure | High |
| 7 | 2026-01-11 12:00 | DC-01 | 4624 | jsmith | 10.10.2.15 | DC-01 | Windows | An account was successfully logged on to the system | Success | Low |
| 8 | 2026-01-11 17:30 | WEB-01 | 500 | anon | 10.10.2.40 | /api | Nginx | Internal server error returned while processing request | Failure | Critical |
| 9 | 2026-01-12 08:00 | DC-01 | 4624 | jsmith | 10.10.2.25 | DC-01 | Windows | An account was successfully logged on to the system | Success | Informational |
| 10 | 2026-01-12 10:00 | FS-SRV01 | 514 | svc_backup | 10.10.2.30 | FS-SRV01 | Windows | The print spooler service started successfully | Success | Low |
| 11 | 2026-01-12 13:00 | WS-018 | 4625 | anon | 10.10.2.15 | WS-018 | Windows | An account failed to log on with an invalid account | Failure | Medium |
| 12 | 2026-01-12 18:00 | WEB-01 | 200 | admin | 10.10.2.40 | /index | Nginx | Request completed successfully by the web server | Success | Informational |

Totales que las aserciones usan (derivados de la tabla):

| Dimensión | Reparto |
|---|---|
| `_level` | critico 2 (Log_ID 4, 8) · alto 3 (2, 3, 6) · medio 2 (5, 11) · bajo 5 (1, 7, 9, 10, 12) |
| Fecha | 2026-01-10 → 4 (1-4) · 2026-01-11 → 4 (5-8) · 2026-01-12 → 4 (9-12) |
| Hostname | DC-01 4 · WS-018 3 · WEB-01 3 · FS-SRV01 2 |
| User | jsmith 4 (1, 2, 7, 9) · admin 3 (4, 6, 12) · anon 3 (3, 8, 11) · svc_backup 2 (5, 10) |
| Result | Success 6 (1, 5, 7, 9, 10, 12) · Failure 6 (2, 3, 4, 6, 8, 11) |

Ojo con dos trampas que ya están resueltas arriba:

- `Severity` usa el catálogo real, y **`Warning` se clasifica como crítico**
  (`main.py:92`). Por eso el Log_ID 4 es crítico.
- Los textos de `Event_Description` superan los 40 caracteres de media, así que
  `detect_facets` los descarta solo. Ningún valor original contiene las cadenas
  `critico`, `alto`, `medio` ni `bajo` («Critical», «High», «Medium» y «Low» no las
  contienen), lo que permite probar que `q` no busca en columnas derivadas.

---

## Estructura de archivos

**Backend (crear):**

- `backend/filters.py` — `FilterParams`, `FacetField`, `parse_filters`, `apply_filters`, `detect_facets`.
- `backend/tests/__init__.py`, `backend/tests/conftest.py` — `sys.path` + fixtures.
- `backend/tests/test_filters.py` — tests unitarios de `apply_filters` y `detect_facets`.
- `backend/tests/test_endpoints.py` — tests de `/summary`, `/facets` y la regresión de `/rows`.

**Backend (modificar):**

- `backend/main.py` — enriquecimiento en `process_analysis` (~:690-702); refactor de `get_rows` (~:809-826); dos endpoints nuevos tras `/rows` (~:851).

**Frontend (crear):**

- `frontend/src/store/filters.ts` — `useFilterStore`.
- `frontend/src/hooks/useScopedSummary.ts` — hook de datos del dashboard.

**Frontend (modificar):**

- `frontend/src/api/client.ts` — `getScopedSummary`, `getFacets`, tipos.
- `frontend/src/store/index.ts` — campos opcionales `total` / `filtered_total` en `AnalysisSummary`.
- `frontend/src/components/Dashboard.tsx` — consumir el hook + barra de filtros activos.

---

### Tarea 1: `apply_filters` y `FilterParams`

**Files:**
- Create: `backend/filters.py`
- Test: `backend/tests/conftest.py`, `backend/tests/test_filters.py`

**Interfaces:**
- Consumes: nada (primer tarea).
- Produce: `FilterParams(from_dt: datetime | None, to_dt: datetime | None, level: str, suspicious: bool, result: str, q: str, facets: dict[str, list[str]])` y `apply_filters(df: pd.DataFrame, p: FilterParams) -> pd.DataFrame`. Las tareas 4, 5 y 6 las usan tal cual.

- [ ] **Step 1: Crear la infraestructura de tests (una vez)**

Crear `backend/tests/__init__.py` vacío y `backend/tests/conftest.py` con:

```python
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))
```

Instalar pytest en el venv del backend:

```bash
backend/.venv/Scripts/python.exe -m pip install pytest
```

- [ ] **Step 2: Escribir el test fallido**

`backend/tests/test_filters.py` — fixtures y el primer grupo de tests. El `df` se construye con pandas directamente (sin Excel) para que sea rápido:

```python
import pandas as pd
import pytest
from filters import FilterParams, apply_filters

@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame({
        "Log_ID": [1, 2, 3, 4, 5],
        "Timestamp": pd.to_datetime([
            "2026-01-10 08:00", "2026-01-10 09:30", "2026-01-11 10:00",
            "2026-01-12 08:00", "2026-01-12 09:00",
        ]),
        "Hostname": ["DC-01", "WS-018", "DC-01", "WS-018", "WEB-01"],
        "User": ["jsmith", "jsmith", "admin", "admin", "anon"],
        "Result": ["Success", "Failure", "Failure", "Success", "Success"],
        "Severity": ["High", "Critical", "Warning", "Informational", "Medium"],
        "Event_Description": ["cuenta iniciada" * 4, "fallo de acceso" * 4, "bloqueo" * 8,
                              "servicio instalado" * 3, "peticion http" * 4],
        "_level": ["alto", "critico", "critico", "bajo", "medio"],
        "_reason": [""] * 5,
        "_suspicious": [True, True, True, False, False],
    })

def vacio() -> FilterParams:
    return FilterParams()
```

Tests:

```python
def test_sin_filtros_devuelve_todo(df):
    assert len(apply_filters(df, vacio())) == 5

def test_filtro_por_nivel(df):
    out = apply_filters(df, FilterParams(level="critico"))
    assert sorted(out["Log_ID"]) == [2, 3]

def test_filtro_por_sospechosos(df):
    out = apply_filters(df, FilterParams(suspicious=True))
    assert sorted(out["Log_ID"]) == [1, 2, 3]

def test_filtro_por_result_insensible_a_mayusculas(df):
    out = apply_filters(df, FilterParams(result="failure"))
    assert sorted(out["Log_ID"]) == [2, 3]

def test_filtro_ventana_temporal_inclusiva(df):
    out = apply_filters(df, FilterParams(
        from_dt=pd.Timestamp("2026-01-10 09:00"),
        to_dt=pd.Timestamp("2026-01-11 23:59")))
    assert sorted(out["Log_ID"]) == [2, 3]

def test_q_busca_solo_en_columnas_originales(df):
    out = apply_filters(df, FilterParams(q="alto"))   # "alto" está en _level, no en los datos
    assert len(out) == 0

def test_q_encuentra_texto_de_una_columna_original(df):
    out = apply_filters(df, FilterParams(q="web-01"))
    assert list(out["Log_ID"]) == [5]

def test_dos_facetas_se_combinan_con_and(df):
    out = apply_filters(df, FilterParams(facets={"Hostname": ["WS-018"], "User": ["admin"]}))
    assert list(out["Log_ID"]) == [4]

def test_valores_de_una_misma_faceta_se_combinan_con_or(df):
    out = apply_filters(df, FilterParams(facets={"Hostname": ["DC-01", "WEB-01"]}))
    assert sorted(out["Log_ID"]) == [1, 3, 5]
```

- [ ] **Step 3: Ejecutar y verificar que falla**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_filters.py -v
```

Esperado: `ModuleNotFoundError: No module named 'filters'` (o `ImportError`).

- [ ] **Step 4: Implementar `backend/filters.py`**

Firmas exactas:

```python
@dataclass
class FilterParams:
    from_dt: datetime | None = None
    to_dt: datetime | None = None
    level: str = ""
    suspicious: bool = False
    result: str = ""
    q: str = ""
    facets: dict[str, list[str]] = field(default_factory=dict)

def apply_filters(df: pd.DataFrame, p: FilterParams) -> pd.DataFrame: ...
```

Criterios del spec §4.2, en este orden: ventana temporal sobre `Timestamp` (inclusiva, `NaT`
fuera solo si hay filtro), `_level`, `_suspicious`, `_result_label` insensible a mayúsculas
(importarlo de `main`), `q` con `str.contains(..., regex=False, na=False)` sobre las columnas que
**no** empiezan con `_`, y luego cada faceta con igualdad exacta insensible a mayúsculas.

**Importante:** importar `_result_label` desde `main` dentro de la función (no a nivel de módulo)
para evitar el import circular `main ↔ filters`.

- [ ] **Step 5: Ejecutar y verificar que pasa**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_filters.py -v
```

Esperado: todos PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/filters.py backend/tests/
git commit -m "feat: apply_filters con ventana temporal, facetas y texto libre"
```

---

### Tarea 2: `detect_facets`

**Files:**
- Modify: `backend/filters.py`
- Test: `backend/tests/test_filters.py`

**Interfaces:**
- Consumes: `FilterParams`, `apply_filters` (Tarea 1).
- Produce: `FacetField(name: str, label: str, distinct: int, values: list[dict])` y `detect_facets(df: pd.DataFrame) -> list[FacetField]`. La Tarea 5 las usa tal cual.

- [ ] **Step 1: Escribir el test fallido**

```python
def test_detect_facets_incluye_columnas_categoricas(df):
    nombres = [f.name for f in detect_facets(df)]
    assert {"Hostname", "User", "Result"} <= set(nombres)
    assert "Timestamp" not in nombres and "Log_ID" not in nombres

def test_detect_facets_descarta_texto_largo_y_log_id(df):
    nombres = [f.name for f in detect_facets(df)]
    assert "Event_Description" not in nombres   # >40 chars de media

def test_detect_facets_incluye_criticidad_derivada(df):
    nombres = [f.name for f in detect_facets(df)]
    assert "_level" in nombres and "_suspicious" in nombres

def test_detect_facets_trae_valores_con_conteo(df):
    campo = next(f for f in detect_facets(df) if f.name == "Hostname")
    assert campo.distinct == 3
    assert {"value": "DC-01", "count": 2} in campo.values   # sin depender del desempate
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_filters.py -v
```

Esperado: `ImportError: cannot import name 'detect_facets'`.

- [ ] **Step 3: Implementar `detect_facets` y `FacetField` en `backend/filters.py`**

```python
@dataclass
class FacetField:
    name: str
    label: str
    distinct: int
    values: list[dict]        # [{"value": str, "count": int}, ...] hasta 12, ordenados por conteo

def detect_facets(df: pd.DataFrame, top_n: int = 12) -> list[FacetField]: ...
```

Reglas del spec §4.4: entra la columna si no empieza con `_` y no es `Timestamp`/`Log_ID`; si no
es numérica de alta cardinalidad; si `nunique() <= max(30, 5% de las filas)`; y si la longitud
media de `astype(str)` es ≤ 40. Siempre se añaden `_level` (label «Criticidad») y `_suspicious`
(label «Solo sospechosos»). Orden: primero las columnas del archivo en orden de aparición, luego
las derivadas.

- [ ] **Step 4: Ejecutar y verificar que pasa**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_filters.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/filters.py backend/tests/test_filters.py
git commit -m "feat: detect_facets descubre columnas categoricas del archivo"
```

---

### Tarea 3: Enriquecer el DataFrame una vez al ingerir

**Files:**
- Modify: `backend/main.py:690-702` (bloque de `process_analysis`)
- Test: `backend/tests/test_endpoints.py`

**Interfaces:**
- Consumes: `normalize_level`, `row_reason`, `_is_suspicious` (ya existen en `main`).
- Produce: `analysis_store[id]["dataframe"]` con columnas `_level`, `_reason`, `_suspicious` y `Timestamp` en `datetime64`. Las Tareas 4, 5 y 6 dependen de esto.

- [ ] **Step 1: Crear fixtures compartidas de endpoint en `conftest.py`**

Añadir a `backend/tests/conftest.py`: función `xlsx_bytes()` que construye el dataset sintético
de 12 filas (tabla de arriba) con pandas y lo devuelve como `BytesIO` de `.xlsx`, más los
fixtures `client` (`TestClient(app)`) y `analysis_id` (hace `POST /api/analyze` con el archivo,
pulea `GET /api/analysis/{id}` hasta `done`, y devuelve el id).

- [ ] **Step 2: Escribir el test fallido**

`backend/tests/test_endpoints.py`:

```python
import pandas as pd
from main import analysis_store

def test_ingesta_enriquece_el_dataframe(client, analysis_id):
    df = analysis_store[analysis_id]["dataframe"]
    assert {"_level", "_reason", "_suspicious"} <= set(df.columns)
    assert pd.api.types.is_datetime64_any_dtype(df["Timestamp"])

def test_columnas_derivadas_no_filtran_en_rows(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/rows", params={"page_size": 50}).json()
    assert all(not c.startswith("_") for c in res["columns"])
    assert {"risk_level", "risk_reason", "suspicious"} <= set(res["rows"][0])
```

- [ ] **Step 3: Ejecutar y verificar que falla**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_endpoints.py -v
```

Esperado: FAIL en `test_ingesta_enriquece_el_dataframe` (las columnas `_level` no existen aún y
`Timestamp` sigue siendo `object`).

- [ ] **Step 4: Implementar el enriquecimiento en `process_analysis`**

En `backend/main.py`, tras `pd.concat` (línea ~690) y **antes** de llamar a `call_ai_service`,
construir una copia enriquecida (`_level` vía `normalize_level`, `Timestamp` vía
`pd.to_datetime(errors="coerce")`, `_reason` y `_suspicious` por fila) y guardar **esa** en
`analysis_store[analysis_id]["dataframe"]`, pasándola también a `call_ai_service`.

Manejar el caso `len(work) == 0` con `pd.Series([], dtype=...)` igual que hoy en `/rows`.

- [ ] **Step 5: Ejecutar y verificar que pasa**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_endpoints.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/
git commit -m "feat: enriquece el DataFrame una vez al ingerir (nivel, razon, sospechoso, timestamp)"
```

---

### Tarea 4: `GET /api/analysis/{id}/summary`

**Files:**
- Create: `backend/filters.py` → `parse_filters`
- Modify: `backend/main.py` (endpoint nuevo tras `/rows`, ~línea 851)
- Test: `backend/tests/test_endpoints.py`

**Interfaces:**
- Consumes: `apply_filters`, `FilterParams` (Tarea 1), DataFrame enriquecido (Tarea 3), `call_ai_service` (`main.py:410`, pura).
- Produce: `GET /api/analysis/{id}/summary` devolviendo el `AnalysisSummary` completo + `total` + `filtered_total`. Lo consume el frontend en la Tarea 7.

- [ ] **Step 1: Escribir los tests fallidos**

```python
def test_summary_sin_filtros_es_identico_al_global(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary").json()
    assert res["total"] == 12 and res["filtered_total"] == 12 and res["rows"] == 12
    assert res["level_counts"] == {"critico": 2, "alto": 3, "medio": 2, "bajo": 5}

def test_summary_ventana_temporal_acota(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"from": "2026-01-11T00:00:00", "to": "2026-01-12T23:59:59"}).json()
    assert res["total"] == 12 and res["rows"] == 8
    assert res["level_counts"]["critico"] == 1

def test_summary_facetas_de_campos_distintos_son_and(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params=[("Hostname", "DC-01"), ("User", "admin")]).json()
    assert res["rows"] == 1

def test_summary_misma_faceta_repetida_es_or(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params=[("Hostname", "DC-01"), ("Hostname", "WEB-01")]).json()
    assert res["rows"] == 7

def test_summary_level_mas_ventana(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"level": "critico", "from": "2026-01-11T00:00:00"}).json()
    assert res["rows"] == 1

def test_summary_sin_coincidencias_devuelve_cero(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"Hostname": "NOEXISTE"}).json()
    assert res["rows"] == 0 and res["total"] == 12

def test_summary_from_mayor_que_to_devuelve_400(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"from": "2026-01-12", "to": "2026-01-10"})
    assert res.status_code == 400

def test_summary_level_invalido_devuelve_400(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary", params={"level": "nivelfalso"})
    assert res.status_code == 400

def test_summary_faceta_inexistente_se_ignora(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"ColumnaFantasma": "x"}).json()
    assert res["rows"] == 12

def test_summary_desconocido_devuelve_404(client):
    assert client.get("/api/analysis/no-existe/summary").status_code == 404

def test_na_sin_filtro_temporal_entra_y_con_filtro_sale(client, analysis_id_na):
    sin_filtro = client.get(f"/api/analysis/{analysis_id_na}/summary").json()
    con_filtro = client.get(f"/api/analysis/{analysis_id_na}/summary",
                            params={"from": "2026-01-01"}).json()
    assert sin_filtro["rows"] == 2 and con_filtro["rows"] == 1

def test_summary_archivo_minimo_sin_timestamp_ni_severity(client, analysis_id_minimo):
    res = client.get(f"/api/analysis/{analysis_id_minimo}/summary",
                     params={"from": "2026-01-01"}).json()
    assert res["rows"] == 2 and res["level_counts"]["bajo"] == 2
```

Añadir en `conftest.py` los fixtures `analysis_id_na` (2 filas, una con `Timestamp` basura) y
`analysis_id_minimo` (solo `Log_ID` y `Event_Description`, sin `Timestamp` ni `Severity`).

- [ ] **Step 2: Ejecutar y verificar que fallan**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_endpoints.py -v -k summary
```

Esperado: `404` en todos (el endpoint no existe).

- [ ] **Step 3: Implementar `parse_filters` y el endpoint**

`parse_filters(request: Request, df: pd.DataFrame) -> FilterParams` en `backend/filters.py`:
lee `request.query_params`, separa los canónicos (`from`, `to`, `level`, `suspicious`, `result`,
`q`) del resto, que son facetas **solo si el nombre coincide con una columna del DataFrame**.
`from`/`to` se parsean con `pd.to_datetime`; si `from_dt > to_dt` o `level` no está en
`LEVEL_ORDER`, se lanza `ValueError` con mensaje legible.

En `main.py`, endpoint nuevo:

```python
@app.get("/api/analysis/{analysis_id}/summary")
async def get_scoped_summary(analysis_id: str, request: Request): ...
```

Flujo: validar análisis (404/409 como `/rows`), `parse_filters` (→ 400 con el detalle del
`ValueError`), `apply_filters`, `call_ai_service(work)`, y añadir `total` (len del DataFrame
completo) y `filtered_total` (len del filtrado) a la respuesta.

- [ ] **Step 4: Ejecutar y verificar que pasa**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_endpoints.py -v
```

Esperado: todos PASS, incluidas las Tareas 1-3.

- [ ] **Step 5: Commit**

```bash
git add backend/filters.py backend/main.py backend/tests/
git commit -m "feat: GET /summary devuelve el resumen acotado por filtros"
```

---

### Tarea 5: `GET /api/analysis/{id}/facets`

**Files:**
- Modify: `backend/filters.py` (`apply_filters` con parámetro `fields` opcional), `backend/main.py`
- Test: `backend/tests/test_endpoints.py`

**Interfaces:**
- Consumes: `detect_facets` (Tarea 2), `apply_filters` (Tarea 1).
- Produce: `GET /api/analysis/{id}/facets` → `{ total, filtered_total, fields: FacetField[] }` con conteos acotados. Lo consume el frontend en la Tarea 7.

- [ ] **Step 1: Escribir los tests fallidos**

```python
def test_facets_detecta_campos_del_archivo(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/facets").json()
    nombres = [f["name"] for f in res["fields"]]
    assert {"Hostname", "User", "Result"} <= set(nombres)
    assert "Timestamp" not in nombres and "Log_ID" not in nombres

def test_facets_conteos_acotados_por_el_filtro(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/facets", params={"User": "jsmith"}).json()
    campo = next(f for f in res["fields"] if f["name"] == "Hostname")
    assert res["filtered_total"] == 4
    assert [v["value"] for v in campo["values"]] == ["DC-01", "WS-018"]

def test_facets_no_ofrece_event_description(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/facets").json()
    assert "Event_Description" not in [f["name"] for f in res["fields"]]
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_endpoints.py -v -k facets
```

- [ ] **Step 3: Implementar el endpoint**

En `main.py`, tras `/summary`:

```python
@app.get("/api/analysis/{analysis_id}/facets")
async def get_facets(analysis_id: str, request: Request): ...
```

Detección sobre el **DataFrame completo** (lista estable de campos); conteos sobre el
DataFrame **filtrado** con `parse_filters` + `apply_filters` (spec §4.4). Devolver
`top_n=12` valores por campo.

Para contar por faceta **ignorando el filtro de esa misma faceta** (como hace Splunk, para que
el panel permita cambiar de valor), añadir a `apply_filters` un parámetro opcional
`skip_fields: set[str] | None = None`.

- [ ] **Step 4: Ejecutar y verificar que pasa**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_endpoints.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/filters.py backend/main.py backend/tests/
git commit -m "feat: GET /facets con campos detectados del archivo y conteos acotados"
```

---

### Tarea 6: Refactor de `/rows` (regresión obligatoria)

**Files:**
- Modify: `backend/main.py:809-826` (bloque de filtros de `get_rows`)
- Test: `backend/tests/test_endpoints.py`

**Interfaces:**
- Consumes: `parse_filters`, `apply_filters`, `FilterParams` (Tareas 1 y 4).
- Produce: `/rows` sin cambios observables. Ninguna tarea posterior depende de su implementación interna.

- [ ] **Step 1: Escribir primero la red de regresión (debe pasar sobre el código ACTUAL)**

```python
def test_rows_refactor_es_identico(client, analysis_id):
    base = f"/api/analysis/{analysis_id}/rows"
    todos = client.get(base, params={"page_size": 200}).json()
    assert todos["total"] == 12 and todos["total_rows"] == 12

    por_nivel = client.get(base, params={"page_size": 200, "level": "critico"}).json()
    assert por_nivel["total"] == 2

    sospechosos = client.get(base, params={"page_size": 200, "suspicious": "true"}).json()
    assert sospechosos["total"] >= 1

    por_result = client.get(base, params={"page_size": 200, "result": "Failure"}).json()
    assert por_result["total"] == 6

    por_q = client.get(base, params={"page_size": 200, "q": "jsmith"}).json()
    assert por_q["total"] == 4

def test_q_no_busca_en_columnas_derivadas(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/rows",
                     params={"page_size": 200, "q": "critico"}).json()
    assert res["total"] == 0      # "critico" solo aparece en _level, no en los datos
```

- [ ] **Step 2: Ejecutar y verificar que PASAN sobre el código actual (red de seguridad)**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_endpoints.py -v -k rows
```

Esperado: PASS. Si no pasan, el dataset sintético no reproduce el comportamiento real:
**arreglar el fixture antes de refactorizar**, nunca el test para que pase después.

- [ ] **Step 3: Refactorizar `get_rows`**

Reemplazar el bloque inline de filtros (`main.py:809-826`) por `parse_filters(request, df)` +
`apply_filters(work, params)`. Mantener la paginación y el shape de respuesta **tal cual**.

- [ ] **Step 4: Ejecutar la regresión completa**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests -v
```

Esperado: todo PASS. Este es el paso más delicado del lote: si algo cambia, se revierte el
refactor y se revisa el spec, no se maquilla el test.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/
git commit -m "refactor: /rows usa el motor de filtros compartido (sin cambio observable)"
```

---

### Tarea 7: Store de filtros y cliente API en el frontend

**Files:**
- Create: `frontend/src/store/filters.ts`
- Modify: `frontend/src/api/client.ts`, `frontend/src/store/index.ts`

**Interfaces:**
- Consumes: `getRows` como plantilla de estilo (`client.ts:54-79`), `useAnalysisStore` (`store/index.ts:132`).
- Produce: `useFilterStore` con `setRange/toggleFacet/setLevel/setQ/setResult/setSuspicious/clearAll/reset`; `getScopedSummary(analysisId, filters, signal)`; `getFacets(analysisId, filters, signal)`. Las Tareas 8 y 9 las usan tal cual.

- [ ] **Step 1: Crear `frontend/src/store/filters.ts`**

```ts
export type FilterState = {
  from: string | null
  to: string | null
  level: string
  suspicious: boolean
  result: string
  q: string
  facets: Record<string, string[]>
}
export const useFilterStore: UseBoundStore<StoreApi<FilterState & Actions>>
```

Incluye el selector `selectIsFiltered` (true si algún campo no vacío) y `reset()` para limpiar
al cambiar de análisis.

- [ ] **Step 2: Añadir los tipos y llamadas en `client.ts`**

```ts
export type FilterParams = {
  from?: string | null; to?: string | null; level?: string
  suspicious?: boolean; result?: string; q?: string
  facets?: Record<string, string[]>
}
export type ScopedSummary = AnalysisSummary & { total?: number; filtered_total?: number }
export type FacetValue = { value: string; count: number }
export type FacetField = { name: string; label: string; distinct: number; values: FacetValue[] }
export const getScopedSummary = (analysisId: string, f: FilterParams, signal?: AbortSignal): Promise<ScopedSummary>
export const getFacets = (analysisId: string, f: FilterParams, signal?: AbortSignal): Promise<{ total: number; filtered_total: number; fields: FacetField[] }>
```

Las facetas se serializan repitiendo el parámetro (`User=a&User=b`), igual que exige el backend.
Añadir `total?: number` y `filtered_total?: number` a `AnalysisSummary` en `store/index.ts`.

- [ ] **Step 3: Verificar**

```bash
npx tsc --noEmit
```

Desde `frontend/`. Esperado: sin errores.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/store/filters.ts frontend/src/api/client.ts frontend/src/store/index.ts
git commit -m "feat: store de filtros y cliente para /summary y /facets"
```

---

### Tarea 8: Hook `useScopedSummary`

**Files:**
- Create: `frontend/src/hooks/useScopedSummary.ts`

**Interfaces:**
- Consumes: `useFilterStore`, `getScopedSummary` (Tarea 7), `useAnalysisStore`.
- Produce: `useScopedSummary(): { summary: AnalysisSummary | null; total: number; filteredTotal: number; loading: boolean; error: string | null }`. Lo consume la Tarea 9.

- [ ] **Step 1: Implementar el hook**

Sin filtros → devuelve el `summary` global del store, **sin hacer ninguna petición**. Con filtros
→ `getScopedSummary` con debounce de 300 ms y cancelación por `AbortController` al cambiar los
filtros o el `analysisId`. Devuelve también `total` (del resumen global, para el denominador del
contador) y `filteredTotal`.

Al cambiar el `analysisId`, llamar a `reset()` del store de filtros.

- [ ] **Step 2: Verificar**

```bash
npx tsc --noEmit
npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useScopedSummary.ts
git commit -m "feat: useScopedSummary con debounce y cancelacion"
```

---

### Tarea 9: Dashboard con filtros activos

**Files:**
- Modify: `frontend/src/components/Dashboard.tsx:77` (fuente del resumen) y bloque nuevo de chips

**Interfaces:**
- Consumes: `useScopedSummary` (Tarea 8), `useFilterStore` (Tarea 7).
- Produce: el dashboard renderiza el resumen acotado y la barra de filtros activos.

- [ ] **Step 1: Cambiar la fuente del resumen**

Reemplazar en `Dashboard.tsx:77` la lectura directa del store por `useScopedSummary()`. Los 4
gráficos **no se modifican**: leen los mismos campos.

- [ ] **Step 2: Añadir la barra de filtros activos**

Debajo de las tarjetas KPI, visible solo si `selectIsFiltered`: chips removibles
(`Hostname: DC-01 ×`, `12/01–15/01 ×`, `Solo sospechosos ×`), contador **«243 de 715 registros»**
y botón «Limpiar filtros». Estado `loading` → resumen anterior atenuado (opacidad ~0.45); sin
coincidencias → `EmptyState`; error → aviso discreto + volver al resumen global.

- [ ] **Step 3: Verificar en el navegador**

```bash
npx tsc --noEmit
npm run build
```

Levantar backend y frontend, cargar el Excel de ejemplo y comprobar a mano: filtrar por host y
ventana temporal → cambian KPIs, donut, hosts, serie y tabla; contador correcto; «Limpiar
filtros» restaura todo; sin filtros no hay peticiones extra (pestaña Red del navegador).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Dashboard.tsx
git commit -m "feat: dashboard con resumen acotado y barra de filtros activos"
```

---

### Tarea 10: Verificación final

**Files:** ninguno nuevo.

- [ ] **Step 1: Suite completa de backend**

```bash
backend/.venv/Scripts/python.exe -m pytest backend/tests -v
```

- [ ] **Step 2: Verificación de frontend**

```bash
npx tsc --noEmit
npm run build
```

- [ ] **Step 3: Recorrido manual contra el criterio de aceptación 3 del spec**

Cargar un Excel con columnas distintas (sin `Destination`, con `Department` extra) y confirmar
que `Department` aparece como faceta disponible **sin tocar código**.

- [ ] **Step 4: Commit final**

```bash
git add -A
git commit -m "test: cobertura del motor de filtros y verificacion end-to-end"
```
