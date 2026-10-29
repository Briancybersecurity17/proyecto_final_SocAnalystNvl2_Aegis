# Spec: harness de evaluación RAG+LLM

**Fecha:** 2026-10-29
**Estado:** aprobado en diseño; spec escrito y autorevisado, pendiente de aprobación del documento
**Alcance:** subproyecto A de la mejora del asistente (medición). Los subproyectos B (endurecimiento) y C (recuperación en capas) tienen sus propios ciclos.

---

## 1. Contexto y objetivo

El asistente del proyecto responde consultas sobre eventos de un SIEM con `qwen2.5:3b` en Ollama, con recuperación RAG, reranker y un guard de grounding que sustituye la respuesta del modelo si cita `Log_ID` inexistentes.

Hoy **no hay forma de saber si el sistema mejoró o empeoró**. Las afirmaciones de calidad se sostienen con una demostración aislada. El proyecto de referencia `sistema-delitos-argentina` —que usa el mismo patrón de few-shot con feedback humano— tampoco lo resuelve: sus tests de LLM son aserciones de forma del prompt (`assert "Chile" in prompt`), y su `estimate_confidence()` es una heurística de calidad de *artículo* en tiempo de ejecución, no una medida de si el LLM respondió bien. Ambos proyectos comparten el mismo hueco: un loop de feedback sin medida del efecto del loop.

**Objetivo:** un runner reproducible que produzca un número comparable entre corridas, de modo que cualquier mejora posterior al RAG se pueda demostrar con evidencia en lugar de impresión.

**Éxito:** corrida el mismo día contra la misma muestra produce el mismo número; dos corridas con una modificación intermedia imprimen un delta por categoría.

## 2. No objetivos

- No juzga calidad de prosa ni estilo. Los valores declarados del proyecto (*grounding estricto, agregados exactos, no inventar*) son medibles sin juez, y un juez LLM sería circular y no reproducible.
- No es una suite de CI: no hay umbral que haga fallar la build por puntuación. Es un runner que imprime un número y lo guarda. El único código de salida no-cero es cuando la corrida **no se puede confiar** (sección 9).
- No mide latencia como objetivo (los `bench_*.py` ya lo hacen); la reporta de pasada como contexto.
- No cubre el informe PDF ni el dashboard.

## 3. Ubicación y unidades

```
backend/eval/
├─ __init__.py
├─ cases.py       # las ~24 preguntas; cada una define su verdad calculable
├─ scoring.py     # funciones puras: (esperado, respuesta) → resultado
├─ runner.py      # subir → indexar → recuperar → responder → puntuar → informe
└─ fixtures/
   └─ sample.xlsx # muestra fija versionada (43,7 KB)

backend/scripts/eval_rag.py          # CLI, coherente con los bench_*.py existentes
backend/tests/test_eval_scoring.py   # tests puros de scoring.py (sin LLM, sin servidor)
```

Cuatro unidades, una responsabilidad cada una:

- **`cases.py`** — datos y verdad. Cada caso lleva `id`, `categoria`, `question` y una forma de calcular su expectativa desde el `DataFrame`. No hace I/O.
- **`scoring.py`** — evaluación pura. Toma lo esperado y la respuesta, devuelve el resultado. No hace I/O ni red, así que es testeable instantáneamente.
- **`runner.py`** — orquestación y reporte. Único con I/O (archivo, app, LLM).
- **`fixtures/sample.xlsx`** — fuente única del ground truth.

`cases.py` y `scoring.py` no dependen de FastAPI, Ollama ni ChromaDB.

## 4. Flujo

```
fixtures/sample.xlsx → pandas → índice en rag_store (eval-id fijo, una vez)
   │
   ├─ por cada caso:
   │    verdad    = case.expected(df)                        pandas, determinista
   │    respuesta = POST /api/analysis/{id}/ask              endpoint real
   │    resultado = scoring(verdad, respuesta, dataset_ids)
   │
   └─ informe por categoría + puntuación global + guardado opcional
```

**Decisión: se entra por `TestClient` de FastAPI, no por HTTP y no llamando `answer_question()` a mano.**

- Exigir el backend corriendo añade un paso manual y acopla el eval al estado de una instancia.
- Llamar `answer_question()` directo **duplicaría** `_ask_prep()` —recuperación, KB, referencia, guard— y dos implementaciones divergen sin que nadie lo note.
- `TestClient` ejercita el endpoint real (subida, indexación, guard de grounding) sin servidor. El patrón ya está en uso: la suite muestra `starlette.testclient`.

Se usa el endpoint **no-stream** (`/ask`), que devuelve la respuesta completa de una vez. El SSE ya tiene su propia cobertura en `test_chat_stream.py`.

### 4.1 Aislamiento: el eval no toca el estado del analista

`TestClient` corre en el mismo proceso que el código de producción, así que hay dos fugas de estado que contaminarían la medición y los datos reales:

1. **Memoria few-shot.** Cada turno dispara `_remember_turn()` → `remember_implicit()`, que escribe en `backend/data/feedback.json` — la memoria aprendida del analista. Sin aislamiento, cada corrida del eval **ensucia la memoria del producto** y, encima, la memoria creciente cambia las respuestas de la propia corrida: dos corridas idénticas no serían comparables.
   → El runner redirige `feedback_store.FEEDBACK_PATH` a un temporal propio (restaurable al terminar) **antes** de crear el cliente.
2. **Memoria multi-turno.** `_remember_turn()` guarda turnos en `entry["chat_history"]`, y el siguiente caso vería el historial del anterior: los casos dejarían de ser independientes.
   → Entre caso y caso, `DELETE /api/analysis/{id}/chat` (endpoint ya existente, línea 1383 de `main.py`).

Ninguno de los dos requiere cambios de producto: son medidas del runner. La precondición «no interferir con el análisis activo del navegador» se cumple igualmente con `analysis_id` propio y `rag_store.delete_analysis()` al terminar.

## 5. Verdad calculable por categoría

El ground truth sale de **pandas sobre la muestra versionada**. Nunca de texto escrito a mano: eso castigaría paráfrasis válidas y mentiría sobre la calidad.

`case.expected(df)` devuelve lo que hay que verificar; `scoring.py` lo evalúa.

| Categoría | `expected(df)` devuelve | Métrica | Casos aprox. |
|---|---|---|---|
| `aggregate` | número exacto (p. ej. `97`) | ¿aparece como token? | 5 |
| `filter_set` | conjunto de `Log_ID` (p. ej. todos los `Failure`) | precisión sobre lo citado + cobertura de recuperación | 7 |
| `refusal` | bandera | ¿niega **y** cita cero `Log_ID`? | 3 |
| `retrieval` | conjunto de `Log_ID` | precisión@k y hit@k, **sin LLM** | 6 |
| `evidence_qa` | conjunto de `Log_ID` relevantes | precisión sobre lo citado | 3 |

### 5.1 Matriz precisión/recall

`k` es el número de eventos recuperados: `n_results`, **8** por defecto.

**No se usa recall sobre preguntas anchas.** Para «¿qué pasó en DC-01?» (91 de 715 eventos) el recall@8 sería ~9 % por construcción — la ventana solo trae 8 — y el número afirmaría que el sistema falla cuando la limitación es aritmética.

- Pregunta **ancha** (esperado ≫ k) → **precisión@k**: de lo que citó, ¿cuánto es correcto?
- Pregunta **estrecha** (esperado ≈ k o < k) → **hit@k**: ¿al menos un relevante entró?

**`ancha` no se declara en el caso: se deriva.** Si cada caso pudiera declararlo a mano, un `ancha` mal puesto cambiaría la puntuación sin que se note. La regla automática es `len(expected) > 2 * k`.

### 5.1.1 Esquema de un caso

```python
Case(
    id="agg-criticos",            # id estable, se usa en el JSON guardado
    categoria="aggregate",        # aggregate | filter_set | refusal | retrieval | evidence_qa
    question="¿Cuántos eventos críticos hay?",
    # expected(df) → lo que scoring.py evalúa según `categoria`:
    #   aggregate   → int
    #   resto       → set[str] de Log_ID   (o bool para refusal)
    expected=lambda df: int((df["level"] == "critico").sum()),
    unidades="eventos",           # solo aggregate: desambigua cifras iguales
)
```

### 5.2 Números en respuestas

Los agregados comparan contra el número de pandas. Cuidados explícitos:

- Coincidencia por **frontera de token**, nunca subcadena: esperar `97` no debe aceptar `1977`.
- Aceptar formato español e inglés: `1.234`, `1,234`, `1234`.
- Distinguir por **unidad declarada en el caso** (`unidades: "eventos" | "hosts" | "registros"`), porque «97 eventos críticos» y «97 registros totales» son números distintos y ambos pueden aparecer en la misma respuesta.

### 5.3 Grounding: métrica global, no categoría

Aplica a **todas** las respuestas y es el número más importante del informe:

- **`grounding_rechazo`** — % de intentos del LLM sustituidos por el motor determinista. Se lee de los campos reales de la respuesta: `engine == "deterministic"` junto a un `warnings` que contiene `grounding_rechazado`. Es la señal directa de que el modelo intentó citar algo que no existe.
- **`ids_inventados`** — cualquier `Log_ID` citado que no esté en el dataset. Esperado **0**; no es lo mismo que lo anterior porque ya pasado el guard sería un fallo del propio guard.

**Hallazgo verificado en el código y requisito de implementación:** hoy el endpoint no-stream **oculta** el rechazo del guard. `answer_question()` llama a `_remote_answer()` (línea 1581 de `ai_service.py`), que devuelve `None` tanto si el LLM está apagado como si respondió y `_grounding_ok()` lo rechazó; el fallback vuelve sin `warning`. El aviso `grounding_rechazado` solo existe en el path de streaming (línea 1676). Consecuencias:

- Sin corregirlo, el eval no-stream **no puede distinguir** «el guard funcionó» de «el modelo ni se consultó»: la métrica más importante sería indetectable.
- Es además una incoherencia entre endpoints: el stream avisa y el no-stream no.

**Requisito:** `answer_question()` debe devolver `grounding_rechazado` en `warnings` cuando el modelo produjo texto y el guard lo descartó, distinguiéndolo de «LLM apagado/inaccesible» (p. ej. `_remote_answer()` pasa a devolver `(texto, motivo)` en vez de `Optional[str]`). Es un cambio mínimo que además corrige la incoherencia para el producto, no solo para el eval. Campos reales que el runner lee: `engine` ∈ `{"llm", "deterministic"}` y `mode` ∈ `{"aggregate", "evidence"}`.

## 6. `rag_examples_used`: diagnosticar, no puntuar

Adoptado de `schemas.IntelligenceResponse.rag_examples_used` de `sistema-delitos-argentina`.

La respuesta expone cuántos ejemplos few-shot se inyectaron. **Sin este campo el eval no distingue dos fallos distintos:**

- el few-shot **no se disparó** en esa pregunta (recall de la memoria), o
- **se disparó y no ayudó** (calidad de los ejemplos).

Tienen soluciones opuestas: la primera se arregla recuperando mejor, la segunda corrigiendo el criterio. Mezclarlas daría un diagnóstico inútil.

**No puntúa.** El runner reporta:

1. cobertura: % de casos con `rag_examples_used > 0`, por categoría;
2. desglose de fallos por cobertura: «de 4 fallos, 3 con 0 ejemplos y 1 con 2».

Cambios necesarios en el código existente:

- `ai_service.py` — `_qa_messages()` ya construye el bloque few-shot; pasa a contar los ejemplos inyectados y devolver `rag_examples_used` junto al resto de campos. `main.py` solo lo reenvía en la respuesta (no-stream y el `final`/`meta` del stream).
- `feedback_store.prompt_block()` — pasa a **bloques separados** para `negative` y `positive` en vez de intercalarlos, como hace `query_similar()` del proyecto de referencia. Cubierto por los tests existentes de `test_feedback_store.py`.

## 7. Motor determinista como línea base

`--motor {llm,determinista,ambos}`, default `llm`.

El motor determinista calcula con pandas los mismos números que el ground truth, así que **ya es la línea base** y no hace falta que nadie la escriba a mano. `--motor ambos` responde directamente: ¿el LLM le gana, le empata o le pierde?

Si en exactitud de agregados el LLM empata o pierde contra el determinista, el LLM no está aportando nada en esa categoría. Esa es la pregunta que el subproyecto C tendrá que responder con datos.

Nota: los casos `aggregate` se enrutan al determinista **por diseño** (`is_aggregate_question`). Ellos no cuentan como «el LLM falló».

**Cómo conmuta el motor:** `ai_config.get()` relee `backend/data/ai_config.json` en cada llamada, pero `ai_config.update()` **escribe a disco** — es la config real que usa la app del analista. El runner jamás la toca: para `determinista` parchea `ai_config.get` en su propio proceso (monkeypatch), de modo que `enabled` venga `False` sin un solo byte en disco. `ambos` es el mismo caso corrido dos veces, con el parche puesto y retirado.

## 8. Informe y comparación de corridas

```
python backend/scripts/eval_rag.py --guardar backend/eval/runs/antes.json
python backend/scripts/eval_rag.py --contra backend/eval/runs/antes.json
```

**Salida en tabla** (cifras ilustrativas, no una corrida real):

```
| categoría     | casos | aciertos |    % | métrica clave            |
|---------------|-------|----------|------|---------------------------|
| aggregate     |     5 |        5 | 100% | —                         |
| filter_set    |     7 |        4 |  57% | precisión citada 0,71     |
| refusal       |     3 |        3 | 100% | —                         |
| retrieval     |     6 |        5 |  83% | hit@8 0,83 · prec@8 0,44  |
| evidence_qa   |     3 |        1 |  33% | precisión citada 0,33     |
```

Más diagnósticos:

- **Grounding global** — `% sin Log_ID inventados` y `% de rechazos del guard`.
- **Cobertura few-shot** — `% de casos con ejemplos inyectados` y desglose de fallos (sección 6).
- **Motor** — cuántos casos usaron LLM vs determinista.
- **Latencia media** (contexto).

**Puntuación global = media macro**: cada categoría pesa igual, para que una categoría con muchos casos no domine el número. Se imprime también el desglose, porque un promedio solo esconde en qué se rompió.

**Guardado** (`--guardar`): JSON con `timestamp`, **hash SHA-256 de la muestra**, `model`, `num_ctx`, `temperature`, `motor`, `rag_examples_used` por caso, resultados por caso y totales. El hash es lo que permite saber si dos corridas se hicieron contra datos distintos — sin él, comparar sería engañoso.

**Comparación** (`--contra`): tabla de delta por categoría y en global, indicando si el hash de la muestra coincide. Si difiere, se avisa explícitamente que la comparación no es válida.

Salida por defecto en `backend/eval/runs/` — directorio en `.gitignore`.

## 9. Manejo de errores y honestidad

La regla rectora: **un eval que mide lo contrario de lo que dice es peor que no tener eval.**

| Situación | Comportamiento |
|---|---|
| `--motor llm` y >30 % de los casos en modo *evidence* volvieron deterministas | **Falla** con explicación. No publica puntuación. |
| `--motor llm` y Ollama apagado | Falla al inicio, antes de puntuar nada. |
| Muestra ausente | Falla con la ruta esperada. |
| Muestra alterada respecto al `--contra` | Compara **pero advierte** que no es válido. |
| Algún caso revienta | Se registra como error, se cuenta aparte y se muestra; no se cuenta como fallo de scoring ni se omite en silencio. |

`--forzar` desactiva el guard del motor para poder investigar una corrida rota. Sin esa salida, un runner con el guard puesto no deja diagnosticarse a sí mismo.

Los casos `aggregate` están excluidos del cálculo del 30 %, porque van al determinista por diseño.

## 10. Testing

**Tests puros** en `backend/tests/test_eval_scoring.py`, sin LLM, sin servidor, sin ChromaDB — instantáneos:

- número detectado como token, y **rechazo** de subcadena (`97` no acepta `1977`);
- formatos `1.234` / `1,234` / `1234`;
- distinción de unidades en la misma respuesta;
- precisión@k con conjunto esperado calculado a mano;
- ancho vs estrecho: la misma respuesta puntúa distinto según `ancha`;
- `refusal` aceptado solo si niega **y** cita cero `Log_ID`;
- `ids_inventados` detectado;
- media macro con pesos iguales.

**Validación de integridad** del runner (no unit tests): una corrida real con `--motor ambos` sobre la muestra. Es el uso previsto del runner.

La suite actual (106 tests) no cambia de tiempo de ejecución apreciable: los tests nuevos no tocan nada externo.

## 11. Decisiones explícitamente descartadas

Del análisis de `sistema-delitos-argentina`:

- **Respuestas doradas escritas a mano + similitud de embeddings o juez LLM** — castiga paráfrasis válidas y es circular. Los valores del proyecto son medibles sin juez.
- **Postgres para persistir ejemplos de feedback** — ese proyecto es multi-usuario con roles; aquí hay una sesión local. Añadiría dependencia sin beneficio.
- **`investigator_correction` como JSON estructurado de campos** — allá el investigador edita campos de una clasificación; aquí el analista escribe la respuesta narrativa completa. `bad_answer` + `answer` ya cubre el caso.
- **Capa de ejemplos estáticos hardcodeados en el prompt** (`EJEMPLOS DE CATEGORIZACIÓN` de `llm_classifier.py`) — cuesta tokens de ventana que ya medimos escasos. Con este eval se puede *medir* si aporta antes de meterlo; meterlo a ciegas repetiría el error de cambiar sin medir.

## 12. Precondiciones

- `qwen2.5:3b` disponible en Ollama (para `--motor llm` y `ambos`).
- Dependencias del venv de `backend/`.
- No requiere servidor corriendo.
- El índice RAG de prueba usa un `analysis_id` propio y se limpia al terminar, para no interferir con el análisis activo del navegador.
