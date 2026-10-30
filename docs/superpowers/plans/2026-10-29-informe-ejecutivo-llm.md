# Plan de implementación: informe ejecutivo con IA acotada y validada por campo (C′)

> **Para el ejecutor:** leer primero `docs/superpowers/specs/2026-10-29-informe-ejecutivo-llm-design.md`. Este plan traduce ese spec en tareas TDD. Cada tarea termina en verde y commiteada. Sin dependencias nuevas, sin cifras hardcodeadas.

**Orden:** Tareas 1→8, secuenciales (T2–T5 comparten archivos; T6–T8 dependen de T5). Ejecución: `cd backend && python -m pytest -q`. Los servicios (backend :8500, Ollama :11434) están arrancados, pero los tests NO los necesitan (usan `TestClient` + monkeypatch).

## Restricciones globales

- **Cero números mágicos de datos:** validador y casos del eval derivan todo del `ctx`/DataFrame del análisis vigente (requisito: funcionar con cualquier Excel de seguridad).
- **El eval no importa `ai_service`** para scoring. Implementaciones independientes que se cruzan.
- Valores fijos del spec: suelo `num_predict` informe = **1600** (hoy 3072); `num_ctx` informe ≥ 8192 (sin cambios); tolerancia porcentaje **±0,1 pp**; ventana número↔etiqueta **40** chars; límite de números ancla = top-20 derivado de `value_counts()`.
- Textos de usuario en español, tono del proyecto existente.
- Commits estilo `feat(informe): …`, `test(informe): …`, `fix(informe): …` en español.

---

## Tarea 1 — `event_id_counts` en el summary + redacción de `risk_justification`

**Archivos:** `backend/main.py`, `backend/tests/test_endpoints.py` (o archivo nuevo `backend/tests/test_summary_event_ids.py`).

### 1a. `call_ai_service` (builder del summary, `main.py:441`, return en `:684`)

Añadir, junto a `level_counts` (que se construye en torno a `:500-520`):

```python
event_id_counts: Dict[str, int] = {}
if "Event_ID" in df.columns:
    event_id_counts = {str(k): int(v) for k, v in
                       df["Event_ID"].value_counts().head(20).items()}
```

Incluir `"event_id_counts": event_id_counts` en el `return` de `:684`. Es aditivo: el frontend y el resto del backend solo leen claves conocidas.

**Test (TDD, rojo→verde):** subir `DATASET_12_FILAS` (conftest) y `GET /summary`: `event_id_counts` debe contener `{"4624": 4, "4625": 4, "4720": 1, ...}` (el conteo exacto sale de pandas en el propio test, no escrito a mano: `pd.Series([...]).value_counts()` sobre los Event_ID del dataset, o simplemente asertar `sum(event_id_counts.values()) == 12` y `event_id_counts["4624"] == 4`). Dataset sin columna Event_ID (si algún test ya usa otro dataset) → `{}`.

### 1b. `risk_justification` (`main.py:647-651`)

Hoy: `f"Event IDs dominantes: {events_txt}. Hosts con más registros: {hosts_txt}."` — ambos conteos son *dentro del nivel* (`sub = df[df["_level"] == highest]`), pero el texto los presenta como globales (bug DC-01 33 vs 91). Cambiar a:

```python
f"Event IDs dominantes en este nivel: {events_txt}. "
f"Hosts con más registros en este nivel: {hosts_txt}."
```

**Test:** con `DATASET_12_FILAS` (riesgo crítico, DC-01 tiene registros también en otros niveles), `GET /summary` → `risk_justification` contiene «en este nivel» y NO contiene la frase vieja «Hosts con más registros: ». Verificar además que el número de DC-01 en la justificación coincide con `df[df._level=='critico'].Hostname.value_counts()['DC-01']` calculado en el test.

**Commit:** `feat(informe): event_id_counts en el summary y justificacion acotada al nivel`

---

## Tarea 2 — `_validate_fields`: reglas A (ancla numérica) y D (anti-redundancia)

**Archivo:** `backend/ai_service.py`. **Tests:** `backend/tests/test_informe_validador.py` (nuevo).

### 2a. Extracción de números y anclas (`ai_service.py`, módulo privado cerca de `_merge:537`)

```python
_NUM_RE = re.compile(r"(?<![\w.\-/])(\d{1,3}(?:[.,]\d{3})+|\d+)(?:\s*%|\b)(?![\w-])")
```

- `def _numbers(text) -> list[tuple[str, float]]`: todas las apariciones `(texto crudo, valor float)` — «1.234», «1,234», «1234» → 1234. Descartar lo pegado a punto/guion/barra (IPs, fechas) como hace `scoring.number_in_answer` (lookbehind/lookahead de arriba).
- `def _anchors(ctx) -> set[float]`: barrido recursivo del `ctx` (dicts/listas/scalars): todos los enteros de conteos, más los valores IP/Event_ID/códigos como **strings numéricos** parseados («4625» → 4625.0; «10.10.2.15» NO se parsea entero, pero sus octetos no deben anclarse — solo aceptar strings que sean *enteramente* numéricos, p.ej. `re.fullmatch(r"\d+", s)`).
- `def _percentages(ctx) -> list[float]`: porcentajes derivados — `level_counts[l]/rows*100` por cada nivel, `suspicious_events/rows*100`, más cualquier `percentage` ya presente en el ctx. Tolerancia ±0,1.

### 2b. Regla A

```python
def _rule_anchor(text: str, ctx: dict) -> list[str]:  # lista de motivos de rechazo
```

Para cada número del texto: si va seguido de `%` → debe caer en `_percentages` (±0,1); si es un entero sin más contexto → debe estar en `_anchors`. **Excepción:** los porcentajes con coma decimal «33,6 %» también válidos. Motivos legibles (`"número 97 sin ancla"`) para el log de la tarea 5.

### 2c. Regla D

```python
def _rule_redundancy(text: str) -> list[str]:
```

Frases = split por `[.;\n]`, normalizar (minúsculas, sin puntuación, colapsar espacios). Frase normalizada que aparece ≥2 veces y tiene ≥5 palabras → motivo `frase repetida: «…»`. Frases cortas (<5 palabras) no cuentan («Nivel crítico.» repetido en una lista es ilegítimo rechazo).

### 2d. Tests (uno por regla, acepta y rechaza, `ctx` sintético mínimo)

- A acepta: texto con `rows`, `suspicious_events`, IP de `suspicious_by_ip` y % exacto de un nivel.
- A rechaza: el «97 registros de Event ID 4625» con un ctx donde `event_id_counts["4625"]==102` (97 está en el ctx como conteo de otro lado? NO — para el test de rechazo, 97 simplemente no existe en el ctx → rechazo por ancla; el caso de atribución 97-existe-pero-es-de-otro-lado es T3 regla B).
- A con contexto numérico trampa: «10.10.2.15» y «2026-01-10» en el texto no generan anclas falsas ni rechazos.
- D rechaza: campo con la frase «174 eventos sospechosos presentan correlación» duplicada.
- D acepta: mismo contenido en campos distintos (la llamada es por campo).
- D acepta: texto con frases cortas repetidas en una enumeración.

**Commit:** `feat(informe): validador reglas A (ancla numerica) y D (anti-redundancia)`

---

## Tarea 3 — Reglas B (atribución) y C (contenido obligatorio)

**Archivo:** `backend/ai_service.py`. **Tests:** mismos `test_informe_validador.py`.

### 3a. Regla B — parejas número + etiqueta con ventana 40 chars

```python
_WINDOW = 40

def _pairs(text: str) -> list[tuple[float, str]]:
    """(número, etiqueta) con etiqueta en ventana ≤40 chars tras el número."""
```

Regex de etiquetas (en minúsculas sobre texto normalizado), cada una resuelta contra el ctx:

| Etiqueta en texto (regex) | Fuente de verdad |
|---|---|
| «N … Event ID X» con X numérico | `ctx["event_id_counts"].get(str(X))`; si X no está en el dict → motivo de rechazo |
| `nivel\s+(crítico\|criticos?\|alto\|altos?\|medio\|medios?\|bajo\|bajos?)` | `ctx["level_counts"][lvl]` |
| `sospechos[oa]s?` | `ctx["suspicious_events"]` |
| `errores?\s*(?:http)?` o `(404\|500\|403)` cercano | `ctx["http_errors"]["total"]` o `["codes"][código]` |

Si la etiqueta existe y el número asociado (el más cercano en la ventana) difiere de la verdad → motivo `«97 … Event ID 4625» ≠ 102`. Si dos números compiten por la misma etiqueta, usar el más cercano (mismo criterio que `number_in_answer` con unidades).

**Atención conocida (evitar falsos negativos):** frases legítimas como «715 registros … clasificados en este nivel» — «este nivel» NO debe matchear el regex de nivel: exige la palabra del nivel concreta, no pronombres.

### 3b. Regla C — contenido obligatorio derivado del ctx (solo lo que los datos permiten)

```python
def _rule_required(field: str, text: str, ctx: dict) -> list[str]:
```

- `executive_summary`: `str(rows)` con la palabra «registros» en la misma frase; etiqueta de `risk` (mapear `critico`→`crític` para matchear crítico/críticos/crítica); todos los niveles con count>0 presentes (`nivel X` en el texto, vía el mismo helper de B); `str(suspicious_events)`.
- `conclusions`: etiqueta de riesgo + `rows` + todos los niveles con count>0.
- `correlation`: ≥1 IP de `suspicious_by_ip` y `str(events)` de esa misma IP en el texto.
- `methodology`: ≥2 marcadores de paso de `[importación|archivo|excel, severidad|nivel|clasific, sospech|regla]` y, si `ctx["severity_mapping"]` no está vacío, ≥1 clave original (p. ej. `Warning`).

**Tests C:** un test por campo con ctx sintético (acepta prosa válida; rechaza prosa que omite la distribución o una IP). Un test de «solo lo que los datos permiten»: ctx con `severity_mapping == {}` → methodology sin claves de mapeo pasa.

**Commit:** `feat(informe): validador reglas B (atribucion) y C (contenido obligatorio)`

---

## Tarea 4 — Prompt endurecido + `num_predict` 1600

**Archivo:** `backend/ai_service.py` (`_remote_narrative:485` y el user prompt actual). **Tests:** `backend/tests/test_informe_prompt.py` (nuevo, estilo `test_chat_stream.py` con monkeypatch).

### 4a. User prompt en 4 bloques (reemplazar el actual «Redacta el informe… SOLO JSON…»)

1. **Alcance:** solo `executive_summary`, `methodology`, `correlation`, `conclusions`; hallazgos y plan de acción los genera el sistema; **no emitir ids**.
2. **Estructura por campo:** idénticos a los requisitos de la regla C (para que el modelo tenga oportunidad de pasar): executive con total/riesgo/distribución completa/sospechosos sin repetir; methodology en pasos numerados con el `severity_mapping` entregado; correlation con la primera IP de `suspicious_by_ip` y sus conteos; conclusions con riesgo/total/distribución/reproducibilidad.
3. **Reglas duras:** todo número sale de los DATOS tal cual — prohibido recalcular, aproximar o **reasignar una cifra a otro sustantivo** (incluir el ejemplo literal «97 registros de Event ID 4625» como LO PROHIBIDO); «N de Event ID X» solo si N = `event_id_counts[X]`; sin frases repetidas; sin inventar tooling (es un archivo importado, no un SIEM en vivo).
4. **Formato:** SOLO JSON válido con exactamente esas 4 claves.

### 4b. Presupuesto

- Suelo de `num_predict` en esta llamada: **1600** (constante `_REPORT_NUM_PREDICT = 1600`; el piso compartido de 3072 no se toca para otros usos).
- `num_ctx ≥ 8192` sin cambios. **Contingencia (documentada, no implementada salvo que falle):** si en la corrida de aceptación el JSON llega truncado, subir a 12288.

### 4c. Tests (sin tocar Ollama)

Estilo `test_chat_stream.py`: `monkeypatch.setattr(ai_service.ai_config, "chat", fake)` donde `fake` captura el prompt enviado y devuelve una respuesta fija. Asserts:

- el user prompt menciona las 4 claves y NO pide `findings`/`action_plan` como claves a producir;
- contiene el ejemplo prohibido 97→4625 y la instrucción de no reasignar;
- el payload incluye `event_id_counts`;
- `num_predict` enviado para el informe == 1600.

**Commit:** `feat(informe): prompt endurecido de 4 campos y num_predict 1600`

---

## Tarea 5 — `_merge` campo a campo + `field_sources` + orquestación

**Archivo:** `backend/ai_service.py` (`_merge:537`, `generate_report_narrative:596`). **Tests:** `backend/tests/test_informe_merge.py` (nuevo).

### 5a. `_merge` → overlay de campos validados

Cambiar la semántica: `_merge` recibe `(fallback, aceptados: dict[str,str])` y produce los 6 campos **siempre**:

```python
def _merge(fallback: dict, aceptados: dict[str, str]) -> dict:
    campos = ("executive_summary", "methodology", "findings",
              "correlation", "conclusions", "action_plan")
    out = {c: aceptados.get(c) or fallback[c] for c in campos}
    out["field_sources"] = {c: ("llm" if c in aceptados and aceptados[c] else "local")
                            for c in campos}
    return out
```

`findings` y `action_plan` **nunca** aparecen en `aceptados` (el prompt no los pide; si el modelo los emite, `_validate_fields` los descarta). `source` = `"llm"` si algún campo de prosa es `"llm"`, si no `"local"` — en `generate_report_narrative`, donde hoy se fija `source`.

### 5b. Orquestación en `generate_report_narrative`

```
fallback = _fallback_narrative(ctx)            # tal cual hoy
try:
    remote = _remote_narrative(ctx)            # 4 claves (tarea 4)
    aceptados = _validate_fields(ctx, remote)  # NUEVO: aplica A–D por campo
except Exception:
    log; aceptados = {}                         # fallo total → fallback completo
narrative = _merge(fallback, aceptados)
```

`_validate_fields(ctx, remote)` itera los 4 campos, ejecuta A, B, C, D en orden (cortocircuito: un motivo basta), y por **excepción individual** dentro de una regla → solo ese campo rechazado, log con `warnings` (`"campo correlation rechazado (regla C): falta IP"`), nunca excepción al caller.

`build_context` (`:116`): añadir `"event_id_counts": summary.get("event_id_counts", {})` a la whitelist (sin esto nunca llega ni al prompt ni al validador — trampa conocida).

### 5c. Tests

- `_merge`: nunca pisa `findings`/`action_plan` aunque `aceptados` los traiga; `field_sources` completo con las 6 claves; `source=="llm"` solo con ≥1 prosa aceptada.
- Orquestación con `monkeypatch` de `ai_config.chat`: respuesta remota válida → campos de prosa del LLM + `field_sources` mixto; respuesta remota con número inventado → ese campo = fallback y `field_sources` `"local"`; `chat` que lanza excepción → narrativa idéntica al fallback completo y `source=="local"`; JSON inválido → igual.
- `build_context` propaga `event_id_counts` (test con summary sintético).
- Contrato de endpoint: `POST /report` (con chat parcheado) devuelve `field_sources` con las 6 claves — verificación rápida de que el contrato HTTP no cambió para las claves viejas.

### 5d. Verificación frontend (sin commits de UI)

Leer `frontend/src/components/` el render del informe y confirmar que lee claves concretas (`report.executive_summary`…) y no itera `Object.keys(report)` — si iterara, filtrar la key. Confirmación anotada en el mensaje del commit; sin cambios de UI salvo que se itere.

**Commit:** `feat(informe): merge campo a campo, field_sources y orquestacion con validador`

---

## Tarea 6 — Scoring puro del eval (verdad pandas)

**Archivos:** `backend/eval/scoring.py`, `backend/eval/cases.py`. **Tests:** `backend/tests/test_eval_report_scoring.py` (nuevo) y `backend/tests/test_eval_cases.py` (extender).

### 6a. `scoring.py` — funciones puras nuevas (independientes del validador)

```python
def level_count_near(text: str, label: str, count: int, window: int = 40) -> bool:
    """«N … nivel Crítico» con N == count, ventana `window` chars."""
```

(Necesaria porque `number_in_answer` con unidades no sirve para contar distribución: exige que el número sea el más cercano a la unidad y «nivel» aparece muchas veces.) Más:

```python
def eventid_pairs_bad(text: str, counts: dict[str, int]) -> list[str]:
    """Parejas «N … Event ID X» con N != counts[X] (o X ausente). Lista de motivos."""
def ip_cited(text: str, ips: list[str]) -> bool
def required_tokens(text: str, tokens: list[str]) -> list[str]  # faltantes
def sentences_repeated(text: str) -> bool
def field_sources_ok(payload: dict) -> bool
```

Cada una con test unitario directo (positivo y negativo). OJO con la semántica de `number_in_answer` (unidades): leer su docstring antes de reutilizarla; para los conteos de distribución se usa `level_count_near`, no `number_in_answer`.

### 6b. `cases.py` — `ReportCase` y verdad derivada del DataFrame

```python
@dataclass
class ReportCase:
    id: str
    descripcion: str
    verifica: Callable[[dict, pd.DataFrame], bool]   # payload del informe + df
    cubre: str                                        # problema #1..#6
```

`def report_truth(df) -> dict` calcula con pandas: `level_counts` vía el mismo criterio de normalización del backend (importar la función compartida de severidad si existe en un módulo importable sin FastAPI; si no, reimplementar el mapping y **documentarlo** — prioridad: no importar `main.py`), `rows`, conteo global de 4625 (del df, no del summary), 500/404, IPs de `Source_IP` con sus conteos.

`REPORT_CASES: list[ReportCase]` con los 7 ids del spec (`rep-distribucion`, `rep-atribucion-eventid`, `rep-conclusiones-cifras`, `rep-correlation-ip`, `rep-hallazgos-estructura`, `rep-grounding-ids`, `rep-field-sources`), cada `verifica` usando scoring de 6a contra `report_truth(df)` — **cero cifras escritas a mano**.

### 6c. Tests

- `test_eval_report_scoring.py`: unitarios de cada función de 6a.
- `test_eval_cases.py` (extender, patrón existente de re-verificación de verdad): para `DATASET_12_FILAS` y/o el fixture, `report_truth` coincide con conteos pandas hechos inline en el test; los 7 `verifica` aceptan un payload construido a mano correcto y rechazan payload con los 6 problemas de la nota (97→4625, duplicados, ids `EVT-…`, niveles absurdos, correlation sin IP, conclusions sin cifras).

**Commit:** `feat(eval): scoring y casos report con verdad derivada del df`

---

## Tarea 7 — Integración de casos `report` en el runner

**Archivo:** `backend/eval/runner.py`. **Tests:** extender `backend/tests/test_eval_report.py`.

### 7a. Generación única por pasada

En `_run_pasada` (dentro del bloque donde ya existe `analysis_id` y el índice listo — ver flujo `:534-596`): tras el indexado, `POST /api/analysis/{id}/report` **una vez** por pasada, midiendo `ms` (misma mecánica que el `started`/`ms` de `/ask`). Guardar `informe = resp.json()` y `informe_ms`. Si el POST falla → registro de fallo de medida que marca la corrida (spec §7: un fallo de medida invalida; un caso report que falla es defecto de producto).

### 7b. Puntuación de casos report

Los `dataset_ids` se recomputan en `:606-617` DESPUÉS del `finally:596` — puntuar los casos report ahí mismo (o guardar el df/informe antes del finally, según convenga). Para cada `ReportCase` de `cases.REPORT_CASES`, `ok = caso.verifica(informe, df)` y anexar a `resultados` registros con `categoria="report"` para que entren al `build_report` sin tocar su cómputo.

### 7c. Salida, comparación e impresión

- `build_report` (`:268`): no cambia estructuralmente (ya agrupa por `categoria`); añadir parámetro opcional `informe_meta` (default `None`) para llevar `informe_ms` a la sección de meta.
- `compare_reports` (`:355`): verificar que la categoría `report` entra al diff automáticamente (mecanismo por categoría) — test lo prueba.
- `print_report` (`:444`): imprimir la sección `report` como las demás.
- `LLM_OFF` (`:172`): NO requiere cambios — parchea el chat globalmente, así que el `POST /report` de esa pasada usa fallback automáticamente. Verificar en test.

### 7d. Tests (estilo `test_eval_report.py`)

Con `monkeypatch` de `ai_config.chat` (respuesta fija válida) y con `LLM_OFF`:

- la pasada produce `categoria=="report"` con los 7 casos y ninguno con `ok is None`;
- `build_report` de una pasada sintética con los 7 ok incluye la sección `report` con 7 casos;
- `compare_reports` con scores de report iguales no marca diff;
- un informe manipulado con el bug 97→4625 hace fallar `rep-atribucion-eventid` end-to-end (vía el chat parcheado).

**Commit:** `feat(eval): seccion report en runner (un POST /report por pasada)`

---

## Tarea 8 — Aceptación: suite + harness en ambos motores + manual

Sin código nuevo: ejecución y verificación. Si algo falla, arreglar en su tarea correspondiente (commits de fix).

### 8a. Suite completa

```powershell
cd backend; python -m pytest -q
```

Todo en verde (~173 tests existentes + nuevos).

### 8b. Harness en ambos motores

```powershell
cd backend; python scripts\eval_rag.py --motor ambos
```

Criterios de éxito (spec §1):

- categoría `report` = 100 % en **ambas** pasadas (LLM y `LLM_OFF`);
- las 7 aserciones `field_sources` presentes;
- latencia del `POST /report` reportada (objetivo orientativo ≤ 20 s con `qwen2.5:3b`; no es gate);
- `warnings` de campos rechazados visibles — **revisar la tasa de aceptación**: si los 4 campos caen a `local` en la pasada LLM, «la IA redacta» queda de nombre → ajustar reglas (tareas 2–3) y re-correr;
- guardar el JSON de salida como baseline comparativo.

**Inspección manual:** del run LLM, confirmar por `field_sources` que al menos un campo de prosa es `"llm"` (si todos son `"local"` el informe es correcto pero no prueba la IA integrada — investigar los `warnings`).

### 8c. Verificación manual con el Excel real del usuario (si está disponible)

El archivo del bootcamp NO está en el repo. Si el usuario lo aporta: subirlo a la app (:8500), generar el informe y verificar que ninguno de los 6 problemas reaparece. Si no, la aceptación se apoya en `fixtures/sample.xlsx` (715 filas) vía el harness + `DATASET_12_FILAS` en los tests.

### 8d. Reinicio de servicios

El backend :8500 corre con el código viejo: **reiniciarlo** tras implementar (los summaries en memoria `analysis_store` no tienen `event_id_counts` → el validador rechazará atribuciones, comportamiento seguro, pero para verlo bien hace falta backend reiniciado y re-subida del Excel).

**Commit (solo si 8a-8c generan cambios):** `fix(informe): ajustes de aceptacion del harness`

---

## Notas para el ejecutor

1. **TDD estricto:** rojo → verde por tarea. Los tests nuevos se escriben ANTES del cambio en cada tarea (los «Tests» de cada tarea describen el rojo inicial).
2. **No tocar:** `/ask`, chat, dashboard, `frontend/` (salvo la verificación 5d), fixture `sample.xlsx`, `num_predict` de usos que no sean el informe.
3. **Orden de reglas del validador:** A → B → C → D con cortocircuito por campo; los motivos de rechazo se loguean con `warnings` para la revisión de tasa (8b).
4. Si `build_context` ya no tiene whitelist estricta al llegar a la tarea 5, verificar que `event_id_counts` llega al prompt (test de 5c lo cubre).
5. Commits pequeños por tarea; la rama trabaja sobre `master` limpio (hoy en `a3a6155`).



