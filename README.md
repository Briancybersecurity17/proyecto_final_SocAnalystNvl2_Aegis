# Comunidad Dojo – SOC Analyst Nivel 2

Aplicación web para el **proyecto final del bootcamp de Comunidad Dojo (SOC Analyst Nivel 2)**:
importa un archivo Excel con registros de seguridad, analiza y correlaciona eventos
sospechosos, clasifica el riesgo en cuatro niveles con justificación, y genera un
**informe técnico en PDF** con hallazgos, evidencias y plan de acción.

> Sin integración con Microsoft Sentinel (requisito de la consigna). Todo el análisis se
> realiza sobre los registros que tú importas, sin inventar hallazgos.

---

## Requisitos cubiertos

| # | Funcionalidad | Dónde |
|---|---|---|
| 01 | **Carga de archivos**: importa `.xlsx` (todas las hojas) **y visualiza sus filas y todas sus columnas** (`Event_Description`, `Result`, `Process_CommandLine`, `Severity`…) con paginación, búsqueda y filtros por criticidad, sospecha y **valor de `Result`** (Success/Failure/Allowed y códigos HTTP) | `UploadSection` + `DataTable` |
| 02 | **Análisis de seguridad**: identifica eventos sospechosos, **correlaciona IP ↔ hosts ↔ tiempo** y justifica cada registro con severidad + resultado | `SecurityAnalysis` + `backend/main.py` |
| 03 | **Dashboard**: KPIs y **gráficos interactivos por criticidad** (distribución, eventos/día, top hosts) más observaciones ancladas a los datos (p. ej. reparto de `Result` y errores HTTP) | `Dashboard` |
| 04 | **Clasificación de riesgos**: Crítico / Alto / Medio / Bajo **con justificación y ejemplos** | `RiskClassification` |
| 05 | **Informe técnico**: documento estructurado completo (7 secciones) | `ReportGenerator` |
| 06 | **Exportación**: descarga del informe en **PDF** | `ReportGenerator` (`@react-pdf/renderer` + `file-saver`) |

**Requisitos adicionales**

- Interfaz moderna e intuitiva en **español**, con navegación lateral y Dashboard como pantalla principal.
- **IA integrada** que apoya el análisis de datos **y** la redacción del informe (`backend/ai_service.py`).
- **Rigor analítico**: cada hallazgo cita sus evidencias (`Log_ID`, `Timestamp`, `Host`, `Event_ID`, `Source_IP`);
  la IA solo recibe datos ya calculados y nunca agrega cifras propias.
- **Mapeo de severidad fiel a la fuente**: el campo `Severity` original se conserva sin alterar y se
  traduce a la escala de cuatro niveles según la política de la organización (`Warning → Crítico`,
  `High → Alto`, `Medium → Medio`, `Informational/Low → Bajo`), con respaldo por palabras clave para
  otros datasets. La correspondencia queda a la vista en *Clasificación de riesgos*
  (campo `severity_mapping`, calculado en `backend/main.py`).
- Renderizado seguro: los textos del backend se insertan con `textContent` (`frontend/src/utils/dom.ts`).

---

## Estructura del proyecto

```
proyecto_final_bootcamp_ComunidadDojo_SocAnalystNvl2/
├─ .gitignore                 # node_modules, .venv, .env, logs, editores, cachés RAG/KB/IA
├─ README.md
├─ test_backend.py            # prueba end-to-end del backend
├─ test_rag.py                # prueba del índice semántico (RAG)
├─ test_kb.py                 # prueba de la base de conocimiento (ATT&CK + Event IDs)
├─ backend/
│  ├─ main.py                 # FastAPI: /analyze, /rows, /report, /ask, SSE, feedback (8500)
│  ├─ ai_service.py           # IA de redacción con grounding estricto + fallback local
│  ├─ ai_config.py            # config del LLM en caliente (modelo, num_ctx, keep_alive…)
│  ├─ feedback_store.py       # criterio aprendido del analista (RAG few-shot)
│  ├─ rerank.py               # reranker cross-encoder sobre los candidatos del RAG
│  ├─ rag_store.py            # índice semántico de los eventos (ChromaDB, colección soc_events)
│  ├─ kb_store.py             # base de conocimiento: ATT&CK + glosario (colección soc_kb)
│  ├─ knowledge/
│  │  ├─ mitre_attack.json    # 79 técnicas (generada por scripts/build_kb.py)
│  │  └─ event_ids.json       # 44 Event IDs de Windows (curado a mano)
│  ├─ scripts/                # build_kb.py + benches de latencia, contexto y recuperación
│  ├─ tests/                  # pytest: 106 tests (config, streaming SSE, few-shot…)
│  └─ requirements.txt
└─ frontend/                  # React + Vite + TypeScript (puerto 5300)
   ├─ index.html
   ├─ vite.config.ts          # proxy /api → 127.0.0.1:8500
   └─ src/
      ├─ App.tsx              # layout + cabecera por sección + estado del backend
      ├─ charts.ts            # sistema compartido de estilos de gráficos (Recharts)
      ├─ store/index.ts       # Zustand (tipado: análisis + navegación)
      ├─ api/client.ts        # axios + SSE
      └─ components/
         ├─ Sidebar.tsx
         ├─ BackendStatus.tsx / EmptyState.tsx   # cabecera y estados vacíos con atajo
         ├─ UploadSection.tsx / UploadExcel.tsx / DataTable.tsx
         ├─ Dashboard.tsx
         ├─ ChartTools.tsx          # SelectionCells + ChartInsight (brillo/contorno y tarjeta de detalle)
         ├─ SecurityAnalysis.tsx
         ├─ RiskClassification.tsx
         ├─ ReportGenerator.tsx
         ├─ AssistantChat.tsx / ReferenceBlock + FeedbackControl (corrección del analista)
         ├─ AttackCorrelations.tsx  # técnicas ATT&CK ancladas a Event_ID observados
         ├─ AiOptions.tsx           # panel del LLM + memoria de criterio aprendido
         └─ SummaryCard.tsx / DashboardSection.tsx
```

---

## Cómo ejecutarlo

```bash
# 1) Backend (Python 3.11+) — http://127.0.0.1:8500
cd backend
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# OPCIONAL – habilita el índice semántico (RAG) del asistente
pip install -r requirements-rag.txt

uvicorn backend.main:app --host 127.0.0.1 --port 8500   # desde la raíz del proyecto

# 2) Frontend (Node 20+) — http://localhost:5300
cd ../frontend
npm install
npm run dev
```

Abrí **http://localhost:5300** → *Carga de archivos* → seleccioná el Excel
(`SOC_L2_Logs_Ampliados_Multiples_Incidentes.xlsx`) → navegá por Dashboard,
Análisis de seguridad y Clasificación de riesgos → *Informe técnico* → **Descargar informe PDF**.

No se usan puertos adicionales: backend **8500**, frontend **5300**.

---

## IA integrada (Ollama local + panel «Opciones IA»)

`backend/ai_service.py` redacta el informe y las respuestas del asistente a
partir de un contexto que contiene **únicamente datos calculados** de los
registros (estadísticas + evidencias).

- **Modo local determinista (siempre disponible):** generador con plantillas
  basadas en los mismos números → nunca falta informe ni respuesta.
- **Modo LLM (por defecto):** Ollama local en `localhost:11434` con
  **qwen2.5:3b** (Q4_K_M, ≈1.9 GB → GTX 1060 de 6 GB holgada). Se configura
  desde la sección **Opciones IA** del sidebar (modelo, `num_ctx`,
  `temperature`, `keep_alive`, `num_predict`, on/off) y **aplica al instante,
  sin reiniciar**: Ollama recibe las opciones en cada petición y el backend
  relee `backend/data/ai_config.json` en la próxima llamada. El botón
  **Probar conexión** mide latencia real y muestra la VRAM en uso.
- **Configuración por entorno (opcional):** las variables siguen aportando
  los valores por defecto si preferís configurar por entorno:

```bash
export AI_API_URL="http://localhost:11434/api/chat"  # u otro compatible OpenAI
export AI_API_KEY="sk-..."          # opcional
export AI_MODEL="qwen2.5:3b"        # opcional
export AI_ENABLED=0                 # opcional: 0 desactiva el LLM
export AI_TIMEOUT=60                # opcional (segundos)
```

Si el LLM está apagado, no responde o la respuesta no es JSON válido, el
servicio **cae automáticamente al modo local**: el informe nunca queda vacío.
La interfaz muestra en todo momento la fuente utilizada (LLM / motor determinista).

---

## Asistente IA con RAG («preguntar a los datos»)

Nueva sección **Asistente IA (RAG)** en el sidebar: una consulta en lenguaje
natural devuelve una respuesta con las evidencias recuperadas.

| Pieza | Archivo | Qué hace |
|---|---|---|
| Índice semántico | `backend/rag_store.py` | ChromaDB persistente + `paraphrase-multilingual-MiniLM-L12-v2` (ES↔EN), indexa cada evento al subir el Excel |
| Recuperación híbrida | `backend/main.py` → `_hybrid_retrieve()` | combina **semántica** («fuerza bruta» ↔ `brute force`) + **léxica** (IP, `Log_ID`, `Event_ID`, host exactos) |
| Respuesta fundamentada | `backend/ai_service.py` → `answer_question()` | redacta citando solo los eventos recuperados; si hay modelo remoto lo usa y **valida que cada Log_ID citado exista** (uno inventado → se descarta) |
| Chat | `frontend/src/components/AssistantChat.tsx` | conversación, filtros por criticidad/sospecha, tarjetas de evidencia con barra de similitud |

**Motor local semántico (gratuito, sin API keys):** los datos nunca salen del
equipo. `sentence-transformers` instala PyTorch (varios GB); si preferís un peso
menor, instalá solo `chromadb` y el RAG usará el modelo ONNX por defecto.

> **Primera ejecución:** el modelo `paraphrase-multilingual-MiniLM-L12-v2`
> (~470 MB) se descarga una sola vez desde Hugging Face y queda en caché en
> `~/.cache/huggingface/`. El backend lo **precarga al arrancar** (`lifespan` →
> `rag_store.warmup()`) para que la primera consulta del asistente no pague ese
> coste. Indexar los 715 eventos del archivo de ejemplo tarda ~2 min en CPU y se
> hace en un hilo aparte: el análisis queda `done` igualmente y el banner del
> chat avanza de «Indexando…» a «Recuperación híbrida activa».

**Expansión de consulta ES→EN:** los logs están en inglés (`An account failed to
log on`) y el analista pregunta en español. `rag_store.expand_query()` amplía la
consulta con sinónimos técnicos («fuerza bruta» → `brute force failed logon
attempts`) — solo la consulta, nunca los datos. Sin esto, «fuerza bruta» puntuaba
0.091 y devolvía eventos 4688 irrelevantes; con expansión sube a 0.267 y
recupera los Event ID 4625 reales del archivo.

**Degradación elegante:** si `chromadb` no está instalado, el backend avisa
(`RAG desactivado`) y el asistente funciona igualmente con búsqueda léxica.
La app **nunca** falla por el RAG. Si la evidencia recuperada es débil (similitud
< 0.30), la respuesta **niega la conclusión** en lugar de inventarla.

**Consultas de agregación (cifras exactas, no filas sueltas):** preguntas como
«¿qué hosts concentran los eventos críticos?», «¿cuántos eventos hay por
criticidad?» o «hazme un resumen» no se responden con 8 registros recuperados
(pueden ser irrelevantes) sino con los **conteos exactos del resumen del
dataset**: distribución Crítico/Alto/Medio/Bajo (incluido «0 de 715» si el
nivel no existe), hosts que concentran cada nivel, sospechosos y hosts
distintos — todo calculado sobre los registros importados, `confidence: alta`
y `mode: "aggregate"` en la respuesta. Las preguntas listado («¿qué eventos
sospechosos hay?») siguen el camino normal con evidencia.

**Autoreparación del índice:** el índice Chroma persiste en disco y se comparte
con las suites de prueba, que purgan lo que no usan. Si `/ask` detecta que un
análisis ya está hecho pero sus eventos no están indexados, **relanza la
indexación en un hilo** (`_ensure_semantic_index` + registro en `_INDEX_TASKS`
para no duplicar trabajo con la propia subida) **sin bloquear la petición**:
mientras tanto responde con recuperación léxica y lo avisa en el propio texto
(«El índice semántico local se está construyendo…»). Las consultas de
agregación no lo necesitan: responden al instante.

Endpoints nuevos:

```bash
POST /api/analysis/{id}/ask      # {question, level?, suspicious?, n_results?}
GET  /api/rag/status             # estado del índice (?analysis_id=...)
GET  /api/kb/status              # estado de la base de conocimiento
```

---

## Base de conocimiento RAG (MITRE ATT&CK + significado de eventos)

El asistente no solo recupera registros: también **explica qué significan**. Dos
fuentes locales, versionadas en el repositorio y sin API keys:

| Fuente | Archivo | Contenido |
|---|---|---|
| **MITRE ATT&CK** | `backend/knowledge/mitre_attack.json` | **79 técnicas** generadas por `backend/scripts/build_kb.py` desde el bundle STIX oficial (`github.com/mitre/cti` → `enterprise-attack.json`). Cada entrada guarda la descripción **oficial en inglés** + la URL en `attack.mitre.org` (verificable) y, en español, qué es y **cómo se detecta**. |
| **Event IDs de Windows** | `backend/knowledge/event_ids.json` | **44 entradas** curadas a mano (Security, System, PowerShell, IIS/Proxy/Firewall): título, significado, **por qué importa** y técnicas asociadas. |

| Pieza | Archivo | Qué hace |
|---|---|---|
| Motor de la KB | `backend/kb_store.py` | indexa los JSON en su propia colección Chroma (`soc_kb`, mismo cliente que `rag_store`), `search()` semántica con respaldo léxico, `lookup_event_ids()`, `build_reference()` |
| Referencia | `backend/main.py` → `/ask` | `build_reference(pregunta, evidencia)` combina 3 vías: Event_IDs **observados en la evidencia** → su significado → técnicas; IDs **citados en la pregunta** («¿qué es el 1102?»); y búsqueda semántica sobre la consulta |
| Respuesta | `backend/ai_service.py` | añade el bloque **«Referencia técnica»** al texto y entrega `reference` estructurada; al modelo remoto se le instruye para usarla **solo como explicación**, jamás como hallazgo |
| UI | `AssistantChat.tsx` → `<ReferenceBlock>` | chips de Event ID («en tus registros» / «citado en tu consulta») + tarjetas de técnica con táctica, detección y enlace a `attack.mitre.org` |

**Rigor analítico:** la KB es **material de marco**, separado de la evidencia en
todo momento (etiqueta `SOURCE_NOTE` en la API, en el texto de la respuesta y en
el prompt del modelo). Nunca afirma que una técnica haya ocurrido: eso solo lo
deciden los registros importados.

**Renumeraciones de MITRE:** el builder sigue las relaciones `revoked-by` del
bundle (T1562 + T1562.001 → **T1685**, T1562.004 → **T1686**, T1070.001 →
**T1685.005**) y conserva los IDs antiguos como `aliases`, así una búsqueda de
«T1562.004» sigue resolviendo a la técnica vigente.

```bash
python backend/scripts/build_kb.py            # regenera mitre_attack.json (usa la caché)
python backend/scripts/build_kb.py --force    # fuerza la re-descarga del bundle STIX (~49 MB)
```

La caché `backend/data/kb_cache/` (~49 MB) está en `.gitignore`; los dos JSON
generados **sí** se versionan, así el proyecto funciona sin red.

**Degradación elegante:** sin `chromadb`, `kb_store.search()` cae a búsqueda
léxica sobre los propios JSON y la referencia sigue disponible
(`status().available = false` con el motivo).

---

## Criterio aprendido del analista (RAG few-shot, sin reentrenar)

La KB explica *qué significan* los eventos. Este módulo aprende **cómo quiere
que se respondan**, y lo hace sin tocar los pesos del modelo.

| Pieza | Archivo | Qué hace |
|---|---|---|
| Memoria | `backend/feedback_store.py` | captura, persiste y recupera los ejemplos de criterio |
| Inyección | `backend/ai_service.py` → `_feedback_note()` | inserta los ejemplos afines en el prompt, presupuestados contra `num_ctx` |
| Endpoints | `backend/main.py` | `POST /api/analysis/{id}/feedback`, `GET/DELETE /api/feedback` |
| UI | `AssistantChat.tsx` (botón «Corregir») · `AiOptions.tsx` (panel) | captura explícita y estado de la memoria |

**Dos vías de captura, ambas asíncronas y nunca bloqueantes:**

- **Implícita** — el analista vuelve a preguntar sobre el mismo tema tras
  recibir una respuesta del LLM ⇒ esa respuesta funcionó ⇒ ejemplo positivo.
  Cero fricción.
- **Explícita** — el botón «Corregir» de cada respuesta, donde el analista
  escribe la respuesta **buena** ⇒ ejemplo negativo con la versión corregida.
  Es el dato más valioso porque trae el criterio humano literal.

**Persistencia:** JSON espejo en `backend/data/feedback.json` (fuente de
verdad, legible y editable a mano) + índice semántico en ChromaDB
(colección `soc_feedback`, reutiliza el cliente y el modelo de embeddings
del RAG). Sin Chroma la recuperación cae a léxica. El JSON está en
`.gitignore` porque es aprendizaje propio de cada máquina.

**Prioridad deliberada del presupuesto:** el bloque few-shot se inyecta
**antes** de la evidencia, pero se ajusta al espacio libre de `num_ctx`. Si
la memoria de turnos ya llenó la ventana, se inyectan **cero ejemplos**.
Perder el few-shot cuesta estilo; perder el system prompt cuesta las reglas
de no inventar Log_ID. Un plus nunca puede ser lo que rompa las
instrucciones.

**Anti-bucles:** una respuesta no se autovalida a sí misma, no se repite un
ejemplo ya guardado, y se exige solape de términos ≥ 0,34 entre la consulta
y la siguiente para considerar que el tema continuó.

```bash
GET  /api/feedback            # estado: total, positivos, correcciones, índice
POST /api/analysis/{id}/feedback   # {question, corrected_answer, bad_answer?}
DELETE /api/feedback          # vacía la memoria (no toca el modelo ni los datos)
```

---

## Pruebas

```bash
# desde la raíz del proyecto
python test_backend.py                                  # archivo por defecto
python test_backend.py ruta/archivo.xlsx                # archivo propio
set SOC_SAMPLE_FILE=ruta\archivo.xlsx && python test_backend.py
```

Comprueba: subida → análisis (`done`) → resumen y niveles → endpoint `/rows`
con filtros → generación del informe → asistente (evidencias citables con
`Log_ID`, **consulta de agregación resuelta con cifras exactas** y rechazo de
consultas demasiado cortas).

Prueba del asistente con recuperación semántica (requiere `requirements-rag.txt`):

```bash
python test_rag.py                                      # indexación + consultas ES→EN
python test_rag.py ruta/archivo.xlsx
```

Comprueba: motor disponible → indexación → 6 consultas en español sobre logs en
inglés → filtros por criticidad y sospecha → degradación elegante.

Prueba de la base de conocimiento (MITRE ATT&CK + glosario de Event IDs):

```bash
python test_kb.py                                   # coherencia + búsqueda + referencia
```

Comprueba: carga de los JSON → referencias cruzadas sin roturas → búsqueda
semántica («fuerza bruta» → T1110, «ransomware» → T1486) → alias renumerados
(T1562.004 → T1686) → referencia anclada a la evidencia (4625 → T1110) → IDs
citados en la pregunta → bloque «Referencia técnica» en la respuesta →
degradación sin Chroma.

Suite unitaria (106 tests: configuración del LLM, streaming SSE, memoria de
criterio few-shot, presupuesto de ventana):

```bash
cd backend && python -m pytest -q
```

Verificación de tipos en el frontend:

```bash
cd frontend && npx tsc --noEmit
```

---

## Tecnologías

- **Frontend:** React, TypeScript, Vite, Tailwind CSS, Zustand, Recharts, Framer Motion, SheetJS, `@react-pdf/renderer`, `file-saver`.
- **Backend:** FastAPI, pandas, openpyxl, httpx, sse-starlette.
- **RAG (opcional):** ChromaDB, sentence-transformers (multilingüe, local y gratuito).
- **LLM (opcional):** Ollama + `qwen2.5:3b` (Q4_K_M, ≈1,9 GB). Un reranker cross-encoder (`ms-marco-MiniLM-L6-v2`) reordena los candidatos antes de armar el prompt.

---

## .gitignore

En la raíz ya está creado e ignora: `node_modules/`, `dist/`, `.vite/`, `.env*`,
`.venv/`, `__pycache__/`, bases de datos, logs y archivos de editor/sistema.
