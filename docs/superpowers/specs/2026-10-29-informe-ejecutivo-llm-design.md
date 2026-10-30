# Spec: informe ejecutivo con IA acotada y validada por campo

**Fecha:** 2026-10-29
**Estado:** aprobado en diseño; spec escrito y autorevisado, pendiente de aprobación del documento
**Alcance:** ciclo del informe ejecutivo (los 6 problemas del resumen + casos `report` en el harness del subproyecto A). Cambia la decisión «opción B» de la nota `docs/superpowers/notes/2026-10-29-informe-ejecutivo.md` a **C′**, tras la sonda empírica de esta sesión.

---

## 1. Contexto y objetivo

El enunciado exige **«IA integrada apoyando el análisis de datos y la redacción del informe»**: el LLM no se puede retirar del informe (eso descarta la opción «todo determinista»).

Sonda empírica de esta sesión (análisis `2ec90201-7af4-44fe-9dfd-31c9f19c191b`, 715 filas, riesgo Crítico; scripts desechables en el temp, no versionados):

| Problema de la nota | LLM (`qwen2.5:3b`) | Fallback determinista |
|---|---|---|
| 1 · «97 registros de Event ID 4625» (el conteo real es 102) | **reproduce** | correcto |
| 2 · hallazgos duplicados | **reproduce** (2× «Intento de autenticación fallido», 2× «Nuevo proceso creado») | sin duplicados |
| 3 · omisiones | peor: sin distribución, sin una sola IP, methodology de 1 frase | distribución y %; la historia de incidente no |
| 4 · hallazgos/niveles absurdos | «Autenticación exitosa» como hallazgo `alto`; hoy sin hallazgo HTTP | HTTP = `critico` (coherente) |
| 5 · ids `EVT-…` | **reproduce** | `H-01`…`H-05` |
| 6 · conclusiones sin cifras | **reproduce** | con cifras |

Además: el LLM **pierde** información (correlation sin una sola IP; plan de 3 items genéricos vs 6 con justificación numérica) y **no es determinista** — la corrida de ayer y la de hoy dan hallazgos distintos con los mismos datos, lo que hace inestable cualquier medida.

**Objetivo:** la IA redacta prosa del informe **sin poder degradar su corrección**: cifras ancladas al contexto, contenido mínimo garantizado, e informe siempre ≥ calidad del fallback, medido en el harness.

**Éxito:** ambas pasadas del eval (motor LLM y `LLM_OFF`) puntúan 100 % en la nueva categoría `report`; los 6 problemas quedan cubiertos por al menos un caso cada uno; la latencia del informe se reporta (objetivo orientativo ≤ 20 s con `qwen2.5:3b`; hoy ~40 s).

## 2. No objetivos

- Reintentos ni streaming del LLM para el informe: si la respuesta no pasa, manda el fallback.
- Cambiar el chat `/ask`, el dashboard, ni crear UI para `field_sources`.
- Segundo fixture de Excel en el eval. La app sí debe funcionar con otros Excel: el validador es data-driven por construcción; un segundo fixture sería su propio ciclo.
- Cambiar de modelo ni integrar un modelo cloud/OpenCode en runtime: `ai_config` ya acepta cualquier endpoint compatible con OpenAI (`AI_API_URL`), queda anotado como camino disponible, sin código en este ciclo.
- Narrativa de incidente (IPs externas `203.0.113.74` / `198.51.100.77` en correlation/conclusions): mejora de contenido pendiente, fuera de este ciclo.

## 3. Arquitectura: C′ (prosa LLM, estructura determinista)

Anclas de código hoy: `build_context` (`backend/ai_service.py:116`), `_fallback_narrative` (`:199`), `_remote_narrative` (`:485`), `_merge` (`:537`), `generate_report_narrative` (`:596`), endpoint `POST /report` (`backend/main.py:1022`).

```
summary ─► build_context ─► _fallback_narrative(ctx)        # 6 campos, tal cual hoy
                     └────► _remote_narrative(ctx)          # SOLO pide 4 claves de prosa
                               ─► _clean_json
                               ─► _validate_fields(ctx, remote)   # NUEVO
                     ◄─────── _merge(fallback, aceptados)   # overlay campo a campo
```

Decisiones:

1. `_remote_narrative` pide **solo** `executive_summary`, `methodology`, `correlation`, `conclusions`. Si la respuesta trae `findings` o `action_plan`, se ignoran.
2. `_merge` deja de confiar en lo remoto: `findings` y `action_plan` salen del fallback **por construcción** (mueren los problemas #2, #4 y #5 sin perseguirlos).
3. Nuevo `field_sources` en la respuesta: `{campo: "llm"|"local"}` para los 6 campos, con `"local"` fijo en `findings` y `action_plan`. `source` = `"llm"` si al menos un campo de prosa fue aceptado; `"local"` si ninguno. `model` igual que hoy.
4. Fallo total (LLM apagado, timeout, JSON inválido, excepción) → fallback completo, comportamiento idéntico al de hoy.
5. Config: **sin claves nuevas**. Único cambio: el suelo de `num_predict` para esta llamada baja de 3072 → 1600 (4 campos, no 6).
6. Verificar en implementación que el frontend ignora la key extra `field_sources` (no se agrega UI).

## 4. Validador por campo: `_validate_fields(ctx, remote)`

Entrada: `ctx` + dict remoto. Salida: `{campo: texto}` con los campos aceptados. **Todo se deriva del `ctx` del análisis vigente** — requisito explícito: funciona con cualquier Excel importado. Cero dependencias del eval.

Si un campo falla cualquier regla, se rechaza y ese campo usa el texto del fallback.

**A · Ancla numérica.** Extraer todos los números del texto. Enteros e identificadores: deben aparecer en algún lugar del `ctx` (barrido del JSON: conteos, IPs, Event_ID, códigos, fechas). Porcentajes (número seguido de `%`): deben coincidir con un porcentaje derivado de `level_counts`/`rows` o presente en el `ctx`, con tolerancia ±0,1 pp. Número no anclado → rechazo.

**B · Atribución (parejas número + etiqueta).** La regla A sola no caza el 97→4625: ambos números existen en el ctx. Parejas con ventana corta, cada una resuelta desde el ctx:

| Si el texto dice | Debe valer |
|---|---|
| «N … Event ID(s) X» | `event_id_counts[X]`; si X no está → rechazo |
| «N registros … nivel Crítico» (y análogos por nivel) | `level_counts[nivel]` |
| «N … sospechosos» | `suspicious_events` |
| «N … errores … (500/404)» | `http_errors.total` o `http_errors.codes[código]` |
| «Etiqueta: N (P %)» de la distribución | `level_counts[etiqueta]` y su porcentaje |

**C · Contenido obligatorio por campo** (solo exige lo que los datos permiten):

- `executive_summary`: `rows` con la palabra «registros», etiqueta de `risk`, la distribución completa (todos los niveles con count > 0), `suspicious_events`.
- `conclusions`: etiqueta de riesgo, `rows`, la distribución completa (todos los niveles con count > 0).
- `correlation`: ≥1 IP de `suspicious_by_ip` y su número de eventos.
- `methodology`: ≥2 marcadores de paso (importación/Excel · severidad/nivel · reglas de sospecha) y, si `severity_mapping` no está vacío, al menos una clave original del mapeo (p. ej. `Warning`).

**D · Anti-redundancia.** Frase repetida dentro del mismo campo (normalizada: minúsculas, sin puntuación) → rechazo. Entre campos distintos está permitida (es normal que `conclusions` repita la distribución).

Semántica: **sin reintentos**. Rechazo → texto fallback de ese campo. Una excepción dentro del validador cuenta como rechazo del campo: nunca propagar al caller (mismo criterio que `_merge` hoy).

El regex de números del validador vive en `ai_service` **a propósito**: no se importa `eval/scoring`. El eval debe re-detectar los mismos fallos con una implementación independiente calculada desde pandas, para que ambos se crucen.

## 5. Datos nuevos en el summary (`backend/main.py`)

1. **`event_id_counts`**: top-20 de `Event_ID` → conteo global (si existe la columna), junto a `level_counts` en el `return` del builder (`main.py:~684`). Debe agregarse **explícitamente** a `build_context` (`ai_service.py:116`): esa función tiene whitelist de claves y si no lo agrega, nunca llega ni al prompt ni al validador.
2. **Redacción de `risk_justification`** (`main.py:647-651`): hoy dice «Hosts con más registros: DC-01 (33)» siendo 33 el conteo *dentro del nivel* (el total real de DC-01 es 91 y lo contradice el hallazgo H-05 del propio informe). Las dos frases finales (Event IDs y Hosts) pasan a llevar «…en este nivel», que es su scope real.

Ambos cambios son aditivos: el frontend y el resto del backend solo leen claves conocidas.

## 6. Prompt endurecido (`_remote_narrative`)

Sobre el `SYSTEM_PROMPT` existente, el user prompt pasa a tener cuatro bloques:

1. **Alcance:** solo las 4 claves de prosa; hallazgos y plan de acción los genera el sistema; **no emitir ids** (hoy el propio prompt pide findings y de ahí salen los `EVT-…`).
2. **Estructura por campo:** los mismos requisitos de la regla C del validador, para que el modelo tenga oportunidad de pasar: executive con total/riesgo/distribución/sospechosos sin repetir; methodology en pasos numerados con el `severity_mapping` entregado; correlation con la primera IP de `suspicious_by_ip` y sus conteos; conclusions con riesgo/total/breakdown/reproducibilidad.
3. **Reglas duras:** todo número sale de los DATOS tal cual — prohibido recalcular, aproximar o **reasignar una cifra a otro sustantivo** (el ejemplo «97 registros de Event ID 4625» es literalmente lo prohibido); «N de Event ID X» solo si N = `event_id_counts[X]`; sin frases repetidas; sin inventar tooling (los datos son un archivo importado, no un «sistema SIEM» en vivo); sin recomendaciones genéricas vacías.
4. **Formato y presupuesto:** SOLO JSON válido con exactamente esas 4 claves; `num_ctx` ≥ 8192 como hoy; `num_predict` suelo 1600.

## 7. Harness: categoría `report`

Anclas: `eval/cases.py` (verdad pandas, `Case`), `eval/scoring.py` (`number_in_answer`, `cited_ids`, `invented_ids`), `eval/runner.py` (`run(motor)`, `LLM_OFF`, `compare_reports`, guards).

- **Una generación por pasada, no por caso:** el runner ya crea el análisis desde `fixtures/sample.xlsx` → un `POST /report` por pasada → todos los casos `report` puntúan sobre ese informe. Coste: 1× latencia por pasada (no 24×).
- **Verdad pandas**, nunca escrita a mano, re-verificada por `tests/test_eval_report_cases.py` como el resto de categorías: distribución vía `normalize_level` (módulo compartido), `rows`, conteo global de 4625, 500/404, IPs.
- **Casos** (cada uno cubre al menos uno de los 6 problemas):

  | Caso | Qué verifica | Problema |
  |---|---|---|
  | `rep-distribucion` | distribución completa y total en `executive_summary` (`number_in_answer` con unidades) | #3 |
  | `rep-atribucion-eventid` | toda pareja «N … Event ID X» con N ≠ conteo real del df → falla | #1 |
  | `rep-conclusiones-cifras` | `conclusions` con riesgo + total + distribución completa | #6 |
  | `rep-correlation-ip` | `correlation` con ≥1 IP que exista en el df | #3 |
  | `rep-hallazgos-estructura` | ids `^H-\d{2}$` únicos, títulos sin duplicar, niveles ∈ enum, hallazgo HTTP con nivel coherente con `severity_mapping` | #2, #4, #5 |
  | `rep-grounding-ids` | `invented_ids` = ∅ sobre texto y evidencias (todo Log_ID citado ∈ df) | #5 |
  | `rep-field-sources` | las 6 claves de `field_sources` presentes con valores `"llm"|"local"` | — |

- **Doble pasada:** con `LLM_OFF` y con motor LLM. Todas las aserciones aplican en ambas: el informe tiene que ser correcto aunque no haya modelo.
- **Integración con el contrato existente:** la sección `report` entra en el JSON de salida y en `compare_reports`, y la latencia del `POST /report` se reporta como la de `/ask`. La regla de corrida no-confiable del spec A (§9) se mantiene: un fallo de medida (no se pudo generar/puntuar el informe) marca la corrida; un caso `report` que falla se lista con su id — es un defecto de producto, no una medida inválida.
- **Modelo:** se registra `model` en la salida; comparar `qwen2.5:3b` vs `7b` es cambiar config y re-correr, sin código.

## 8. Errores y configuración

- LLM apagado / sin URL / timeout / JSON inválido / excepción → fallback completo (comportamiento actual, se conserva).
- Validador con input inesperado → rechazo del campo, nunca excepción hacia `generate_report_narrative`.
- `enabled`, `api_url`, `model`, `timeout`, `num_ctx`, `num_predict` siguen mandando; sin claves nuevas obligatorias.
- Un warning de log por campo rechazado, con la regla que falló (diagnóstico sin UI).

## 9. Tests (TDD) y criterios de aceptación

- **Unidad (`backend/tests/`):** `_validate_fields` con un test por regla A–D (acepta y rechaza, con `ctx` sintético); `_merge` campo a campo (nunca pisa `findings`/`action_plan`; `field_sources`; semántica de `source`); contrato del prompt (pide solo 4 claves y no pide findings); `build_context` propaga `event_id_counts`; redacción de `risk_justification`.
- **Verdad del eval:** `tests/test_eval_report_cases.py` re-verifica los `expected` de los casos report desde el fixture.
- La suite existente (~173 tests) sigue en verde.
- **Aceptación final:** corrida del harness en ambos motores con `report` al 100 %, y verificación manual contra el análisis `2ec90201…` de que ninguno de los 6 problemas reaparece (el texto LLM solo entra si pasa).

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Validador demasiado estricto → casi todo cae a fallback y «la IA redacta» queda de nombre | `field_sources` + log de reglas fallidas; umbrales de la regla C fijados en la sección 4; revisar la tasa de aceptación durante la implementación |
| Validador demasiado laxo → vuelve el 97→4625 | `rep-atribucion-eventid` es una segunda implementación independiente, calculada desde pandas |
| `num_predict` corta el JSON → fallback (informe válido pero sin IA) | suelo 1600 para 4 campos; el log de JSON inválido ya existe |
| Frontend rompe con `field_sources` | verificar en implementación; es una key adicional, no requerida |
| Latencia de Ollama variable | la mide el runner; ≤ 20 s es orientativo, no es gate |

## 11. Follow-ups anotados (fuera de este ciclo)

- Modelo cloud vía `ai_config` (compatible OpenAI) — camino ya soportado, cero código.
- A/B `qwen2.5:3b` vs `7b` con el harness (solo config + re-correr).
- Narrativa de incidente con las IPs externas en correlation/conclusions.
- Segundo fixture de Excel para el eval.
