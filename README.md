# AEGIS – Plataforma de análisis de seguridad

**AEGIS** es una aplicación web de análisis de seguridad: importa un archivo Excel
con registros, analiza y correlaciona eventos sospechosos, clasifica el riesgo en
cuatro niveles con justificación, y genera un **informe técnico en PDF** con
hallazgos, evidencias y plan de acción. Incluye asistente IA con recuperación
(RAG) sobre tus propios registros y mapeo MITRE ATT&CK.

Fue creada como **proyecto final del bootcamp de Analista SOC Nivel 2**. Todo el
análisis se realiza sobre los registros que tú importas: la IA solo redacta a
partir de cifras ya calculadas, nunca inventa hallazgos.

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
aegis/
├─ .gitignore                 # node_modules, .venv, .env, logs, editores, cachés RAG/KB/IA
├─ README.md
├─ test_backend.py            # prueba end-to-end del backend
├─ test_rag.py                # prueba del índice semántico (RAG)
├─ test_kb.py                 # prueba de la base de conocimiento (ATT&CK + Event IDs)
├─ backend/
│  ├─ main.py                 # FastAPI: 25 endpoints — analyze, rows, report, ask/SSE, fichas, IA (8500)
│  ├─ ai_service.py           # IA de redacción con grounding estricto + fallback local
│  ├─ ai_config.py            # config del LLM en caliente (endpoint, modelo, temperature…)
│  ├─ analysis_persist.py     # análisis, informe e índice sobreviven a reinicios (data/)
│  ├─ filters.py              # motor de facetas/filtros compartido por dashboard y tabla
│  ├─ severity.py             # mapeo del campo Severity original a la escala de 4 niveles
│  ├─ behavior_correlations.py # patrones conductuales deterministas (IP ↔ evento ↔ host)
│  ├─ attack_cards.py         # catálogo de fichas de ataque: reglas + match_card/evaluate
│  ├─ attack_candidates.py    # taller de fichas nuevas con aprobación humana (data/)
│  ├─ feedback_store.py       # criterio aprendido del analista (RAG few-shot)
│  ├─ rerank.py               # reranker cross-encoder sobre los candidatos del RAG
│  ├─ rag_store.py            # índice semántico de los eventos (ChromaDB, colección soc_events)
│  ├─ kb_store.py             # base de conocimiento: ATT&CK + glosario (colección soc_kb)
│  ├─ knowledge/
│  │  ├─ mitre_attack.json    # 706 técnicas (generada por scripts/build_kb.py)
│  │  ├─ event_ids.json       # 44 Event IDs de Windows (curado a mano)
│  │  └─ attack_cards.json    # 15 fichas de ataque con sus reglas evaluables
│  ├─ eval/                   # harness de evaluación: fixtures + 24 casos + scoring
│  ├─ scripts/                # build_kb.py, eval_rag.py y benches de latencia
│  ├─ tests/                  # pytest: 552 tests en 28 archivos
│  └─ requirements.txt
└─ frontend/                  # React + Vite + TypeScript (puerto 5300)
   ├─ index.html
   ├─ vite.config.ts          # proxy /api → 127.0.0.1:8500
   └─ src/
      ├─ App.tsx              # layout + cabecera por sección + estado del backend
      ├─ charts.ts            # sistema compartido de estilos de gráficos (Recharts)
      ├─ motion.ts / persist.ts / restore.ts   # variantes, guardado y restauración
      ├─ store/               # Zustand: análisis, navegación y motor de filtros
      ├─ api/client.ts        # axios + SSE
      └─ components/          # 21 componentes
         ├─ Sidebar.tsx
         ├─ BackendStatus.tsx / EmptyState.tsx   # cabecera y estados vacíos con atajo
         ├─ UploadSection.tsx / UploadExcel.tsx / UploadHistory.tsx
         ├─ DataTable.tsx / ColumnFilter.tsx     # tabla con autofilter por cabecera
         ├─ Dashboard.tsx / DashboardSection.tsx / SummaryCard.tsx
         ├─ ChartTools.tsx          # SelectionCells + ChartInsight (brillo/contorno y tarjeta de detalle)
         ├─ TimelineBrush.tsx       # tira temporal con arrastre para acotar el rango
         ├─ SecurityAnalysis.tsx
         ├─ RiskClassification.tsx
         ├─ ReportGenerator.tsx
         ├─ AssistantChat.tsx / ReferenceBlock + FeedbackControl (corrección del analista)
         ├─ AttackCorrelations.tsx  # técnicas ATT&CK ancladas a Event_ID observados
         ├─ AttackCandidates.tsx    # fichas candidatas: aprobar / rechazar
         ├─ AiOptions.tsx           # panel del LLM + memoria de criterio aprendido
         └─ TypingTitle.tsx
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
pip install -r ../requirements-rag.txt    # requirements-rag.txt está en la raíz

cd ..                            # volvé a la raíz del proyecto
uvicorn backend.main:app --host 127.0.0.1 --port 8500

# 2) Frontend (Node 20+) — http://localhost:5300
cd frontend
npm install
npm run dev
```

Abrí **http://localhost:5300** → *Carga de archivos* → seleccioná tu archivo
`.xlsx` (para probar ya, el repo incluye una muestra de 715 filas en
`backend/eval/fixtures/sample.xlsx`) → navegá por Dashboard, Análisis de
seguridad y Clasificación de riesgos → *Informe técnico* → **Descargar informe PDF**.

No se usan puertos adicionales: backend **8500**, frontend **5300**.

---

## Auto-chequeo: todo se comprueba solo

No hay pasos manuales de configuración del RAG ni de Chroma: al usar la app
verifica sola que tenga todo para trabajar.

- **Al arrancar el backend** se comprueban las dependencias y se precarga en
  segundo plano lo que haga falta: índice semántico (RAG), base de
  conocimiento ATT&CK, reranker y análisis anteriores. La cabecera muestra el
  estado real (**punto verde = backend conectado**).
- **Primera ejecución:** si el modelo de embeddings no está en tu PC, se
  descarga solo desde Hugging Face (~470 MB) y queda en caché. Si no hay red,
  se reintenta en la próxima consulta.
- **Si falta `chromadb` o `sentence-transformers`** (son opcionales), el
  backend lo detecta y avisa **«RAG desactivado»**: el asistente sigue
  funcionando con búsqueda léxica. **La app nunca falla por el RAG.**
- **Al subir un Excel:** los eventos se indexan automáticamente en un hilo
  aparte; el banner del asistente avisa «Indexando…» hasta «Recuperación
  híbrida activa». No hay que tocar nada.
- **Si el índice se pierde** (p. ej. borraste la caché), el backend lo detecta
  y **relanza la indexación solo**, sin bloquear la consulta: mientras
  responde con léxica y lo avisa en el propio texto.
- **La IA remota es opcional:** sin API key configurada, el informe y el
  asistente usan el motor local. Podés verificar el proveedor cuando quieras
  con **Opciones IA → Probar conexión**.

En resumen: instalás las dependencias, arrancás los dos servidores y la app
queda lista; todo lo demás (índice, cachés, estados) se resuelve en el uso.

---

## Guía de uso de la app

Recorrido completo, de la carga del Excel al PDF. La navegación es por el
**sidebar izquierdo**; el estado del backend se ve arriba a la derecha
(punto verde = conectado).

### 1. Carga de archivos

1. Entrá a **Carga de archivos** (también desde el estado vacío inicial).
2. Seleccioná o arrastrá un `.xlsx`. Se procesan **todas las hojas**.
3. Esperá a que el estado pase de `pending` a `done`:
   - la tabla muestra todas las columnas del archivo con paginación y búsqueda;
   - en segundo plano se indexa el índice semántico (RAG) — el banner del
     asistente avisa «Indexando…» hasta que queda «Recuperación híbrida activa».
4. **Filtros rápidos** sobre la tabla: criticidad, `sospechoso`, valor de
   `Result` (Success/Failure/Allowed y códigos HTTP) y búsqueda de texto.
   Cada columna tiene autofilter en su cabecera.
5. **Historial**: los análisis anteriores aparecen abajo; con el botón
   **Restaurar** se recupera cualquiera (sobreviven a reiniciar el backend).

> El Excel original queda guardado en `backend/data/analyses/<id>/`: la fuente
> de verdad siempre es el archivo, no el navegador.

### 2. Dashboard

- **KPIs** arriba: total de registros, sospechosos, hosts, distribución por
  nivel y riesgo global.
- **Gráficos interactivos**: distribución por criticidad, eventos por día,
  top hosts, reparto de `Result`/errores HTTP.
- **Interacción**: clic en un gráfico o arrastre en la barra temporal
  (TimelineBrush) **acota el rango**; todos los KPIs y gráficos se recalculan
  al instante sobre la selección. El panel *Detalle* muestra las cifras
  exactas de lo que se seleccionó.

### 3. Análisis de seguridad

- Lista de **eventos sospechosos** con su justificación (severidad + resultado
  + palabras clave + Event ID).
- **Correlaciones** IP ↔ host ↔ tiempo: hosts afectados, IPs de origen y
  patrones conductuales (fuerza bruta, barrido, IPs multi-host…).

### 4. Correlaciones ATT&CK

- **Técnicas observadas**: las técnicas MITRE ATT&CK que corresponden a los
  Event ID reales del archivo (con táctica, descripción y enlace oficial).
- **Tácticas cubiertos** y **Event IDs del dataset** con su glosario.
- **Fichas de ataque**: 15 fichas evaluables; si el asistente encuentra un
  ataque fuera del catálogo propone una **ficha candidata** que vos
  aprobás o rechazás (nunca entra sola).

### 5. Asistente IA (RAG)

1. Entrá a **Asistente IA (RAG)** (requiere un análisis cargado).
2. Escribí la pregunta en lenguaje natural, p. ej.:
   - «¿cuántos intentos de login fallido hay?» → cifra exacta del resumen;
   - «¿qué hosts concentran los eventos críticos?» → agregación;
   - «¿hubo fuerza bruta?» → evaluación de la ficha con umbrales reales;
   - «¿qué es el evento 1102?» → referencia técnica de la KB.
3. Cada respuesta trae **tarjetas de evidencia** con los `Log_ID` citados
   (verificables) y, si aplica, el bloque **Referencia técnica** (Event IDs y
   técnicas ATT&CK con enlace).
4. **Filtros dentro del chat**: acotar a un nivel de criticidad o solo
   sospechosos antes de preguntar.
5. **Corregir**: el botón «Corregir» de cualquier respuesta guarda tu
   criterio en la memoria few-shot (se ve en *Opciones IA* → Memoria de
   criterio). Las respuestas que repetís el mismo tema se guardan como
   positivas automáticamente.

### 6. Opciones IA

- **Motor de IA**: endpoint compatible con OpenAI (Groq por defecto), modelo,
  **API key**, temperature, timeout y on/off — aplica al instante, sin
  reiniciar. Botón **Probar conexión** para verificar latencia real.
  *¿De dónde saco la key?* Ver **Configurar la IA: obtener tu API key**.
- **Parámetros de generación** y **retención de datos**.
- **Memoria de criterio**: estado de los ejemplos few-shot, con opción de
  vaciarla.

### 7. Clasificación de riesgos

- Los registros agrupados en **Crítico / Alto / Medio / Bajo**, cada nivel con
  su justificación técnica y ejemplos reales.
- Se ve el **mapeo de severidad**: `Warning → Crítico`, `High → Alto`,
  `Medium → Medio`, `Informational/Low → Bajo` (el valor original se conserva
  sin alterar).

### 8. Informe técnico → PDF

1. Entrá a **Informe técnico** y pulsá **Generar informe**.
2. El documento tiene **7 secciones**: resumen ejecutivo, alcance y
   metodología, distribución por criticidad, eventos sospechosos, correlaciones,
   clasificación de riesgos, conclusiones y plan de acción.
3. Revisalo en pantalla (vista previa A4) y descargalo con **Descargar informe
   PDF** — portada con marca AEGIS, numeración de páginas y las cifras
   ancladas a tus datos.

> **Flujo mínimo para un entregable:** *Carga de archivos* → (esperar `done`)
> → *Informe técnico* → *Generar* → *Descargar PDF*. El resto de secciones
> sirve para sustentar y verificar cada hallazgo del informe.

---

## Manejo de datos

**Todo vive en tu equipo.** No hay servidores propios, telemetría ni cuentas:

| Qué | Dónde queda | ¿Sale de tu equipo? |
|---|---|---|
| El Excel que importás y todos los análisis | `backend/data/analyses/<id>/` (disco local, en `.gitignore`) | **No** |
| Índice RAG (ChromaDB) y base de conocimiento | `backend/data/` local | **No** — el modelo de embeddings corre en tu PC |
| API key y ajustes de la IA | `backend/data/ai_config.json` (local, en `.gitignore`) | Solo viaja a tu proveedor de IA como `Authorization: Bearer` |
| Preferencias de la interfaz (sección activa, chat) | `localStorage` / `sessionStorage` del navegador (`aegis.*`) | **No** |
| Configuración para el LLM (informe y asistente) | — | **Sí, al proveedor que vos configures** (Groq por defecto): estadísticas ya calculadas, evidencias de los eventos recuperados y tu pregunta. **Nunca el archivo Excel completo ni nada más** |

- La IA solo **redacta sobre cifras ya calculadas** por el backend: no recibe
  registros crudos en bloque ni puede inventar hallazgos (cada Log_ID citado se
  valida contra tus datos antes de mostrarse).
- **Sin configurar la IA remota, nada sale de tu equipo**: informe, asistente
  y clasificación funcionan igual con el motor local.
- Únicas conexiones externas de la app: tu proveedor de IA (si lo activás) y
  Hugging Face **la primera vez** que se descarga el modelo de embeddings
  (~470 MB, queda en caché local). Podés borrar los análisis desde *Carga de
  archivos* (botón **BORRAR**) y acotar cuánto tiempo se conservan en
  *Opciones IA* → retención.

---

## Configurar la IA: obtener tu API key

La app funciona **sin API key** (motor determinista), pero para que la IA remota
redacte informes y respuestas necesitás una. La más sencilla y gratis es **Groq**:

1. Entrá a **https://console.groq.com** y creá una cuenta (con Google o email).
2. Menú **API Keys** → **Create API Key** → copiá la clave (empieza con
   `gsk_…`). Se muestra **una sola vez**: guardala.
3. En la app: **Opciones IA** → campo **API key del proveedor** → pegala →
   **Guardar cambios**. Aplica al instante, sin reiniciar.
4. Verificá con **Probar conexión**: muestra latencia real, modelo y proveedor.

**Plan gratuito (Developer):** es de pago cero y alcanza con holgura para usar
la app. Según la [documentación oficial de límites de Groq](https://console.groq.com/docs/rate-limits),
para el modelo por defecto `openai/gpt-oss-120b` el plan libre da **~30
peticiones/minuto, ~1.000/día y ~200.000 tokens/día**: un informe completo
consume unos pocos miles de tokens, así que da para cientos de preguntas e
informes al día. Los límites exactos de tu cuenta están en
`console.groq.com/settings/limits`. Si llegás al tope, Groq responde `429` y
la app **no se rompe**: reintenta sola y, si persiste, el informe cae al motor
local hasta que la cuota se renueva.

Cualquier proveedor compatible con OpenAI funciona cambiando **API URL** y
**Modelo** en el mismo panel. El campo es **texto libre** (acepta cualquier ID
de modelo) y debajo lo seleccionás con **chips**: la fila **Recomendados
(gratis)** trae los gratuitos de Groq — `gpt-oss-120b` —mejor calidad, por
defecto—, `gpt-oss-20b` —más rápido—, `qwen3.8-27b` —buen español— y
`gpt-oss-safeguard-20b` —con filtro de seguridad y cuota baja—, y si tu
cuenta trae más modelos aparecen como **Otros modelos del proveedor**. No hay
modelos especializados en ciberseguridad en Groq: la calidad analítica la
aportan los prompts y la base de conocimiento de AEGIS.
También podés configurarlo por variables de entorno (ver *Configuración por
entorno* más abajo).

---

## IA integrada (proveedor cloud + panel «Opciones IA»)

`backend/ai_service.py` redacta el informe y las respuestas del asistente a
partir de un contexto que contiene **únicamente datos calculados** de los
registros (estadísticas + evidencias).

- **Modo determinista (siempre disponible):** generador con plantillas
  basadas en los mismos números → nunca falta informe ni respuesta.
- **Modo LLM (por defecto):** proveedor cloud compatible con OpenAI — por
  defecto **Groq** con `openai/gpt-oss-120b` (≈3 s por respuesta, sin
  hardware local). Se configura desde la sección **Opciones IA** del sidebar
  (endpoint, API key, modelo, `temperature`, `timeout`, on/off) y **aplica al
  instante, sin reiniciar**: el backend relee `backend/data/ai_config.json`
  en la próxima llamada. El botón **Probar conexión** mide latencia real
  contra el mismo endpoint que usaría la app.
- **Configuración por entorno (opcional):** las variables siguen aportando
  los valores por defecto si preferís configurar por entorno:

```bash
export AI_API_URL="https://api.groq.com/openai/v1/chat/completions"
export AI_API_KEY="gsk_..."          # obligatoria para el proveedor
export AI_MODEL="openai/gpt-oss-120b"  # opcional
export AI_ENABLED=0                  # opcional: 0 desactiva el LLM
export AI_TIMEOUT=60                 # opcional (segundos)
```

Si el LLM está apagado, no responde o la respuesta no es JSON válido, el
servicio **cae automáticamente al modo determinista**: el informe nunca queda
vacío. La interfaz muestra en todo momento la fuente utilizada (LLM / motor
determinista).

---

## Asistente IA con RAG («preguntar a los datos»)

La sección **Asistente IA (RAG)** del sidebar permite preguntar en lenguaje
natural y obtener una respuesta con las evidencias recuperadas.

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
attempts`) — solo la consulta, nunca los datos. Sin esta ampliación, una
consulta en español casi no puntuaba sobre los eventos en inglés y devolvía
resultados irrelevantes.

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

**Auto-reparación:** si el índice se pierde o un análisis quedó sin indexar,
el backend lo detecta y lo reconstruye solo en segundo plano (ver
*Auto-chequeo*). Las consultas de agregación no lo necesitan: responden al
instante.

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
| **MITRE ATT&CK** | `backend/knowledge/mitre_attack.json` | **706 técnicas** generadas por `backend/scripts/build_kb.py` desde el bundle STIX oficial (`github.com/mitre/cti` → `enterprise-attack.json`). Cada entrada guarda la descripción **oficial en inglés** + la URL en `attack.mitre.org` (verificable) y, en español, qué es y **cómo se detecta**. |
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
bundle oficial y conserva los IDs antiguos como `aliases`, así una búsqueda de
una técnica revocada sigue resolviendo a la vigente.

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
| Inyección | `backend/ai_service.py` → `_feedback_note()` | inserta los ejemplos afines en el prompt, presupuestados contra la ventana del modelo |
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

Los ejemplos se ajustan al espacio libre de la ventana del modelo: si el prompt
ya la desborda, se inyectan **cero ejemplos** antes que sacrificar las reglas
de no inventar `Log_ID`. Además, una respuesta no se autovalida a sí misma ni
se repite un ejemplo ya guardado.

```bash
GET  /api/feedback            # estado: total, positivos, correcciones, índice
POST /api/analysis/{id}/feedback   # {question, corrected_answer, bad_answer?}
DELETE /api/feedback          # vacía la memoria (no toca el modelo ni los datos)
```

---

## Fichas de ataque y aprendizaje con aprobación humana

El asistente no solo recupera registros: también decide **qué ficha de ataque**
encaja con la pregunta y, cuando el catálogo no la cubre, propone una nueva —
pero nunca la escribe solo.

| Pieza | Archivo | Qué hace |
|---|---|---|
| Catálogo vigente | `backend/knowledge/attack_cards.json` | **15 fichas** con sinónimos, `que_es`, `como_se_ve`, umbrales, ejemplo y una **regla evaluable** (columnas y Event IDs reales) |
| Motor | `backend/attack_cards.py` | `match_card()` elige la ficha por sinónimo; `evaluate()` la contrasta con el dataframe (`applicable` + motivo + detecciones) |
| Taller de candidatas | `backend/attack_candidates.py` | valida el borrador con el **mismo criterio** que el catálogo y lo guarda en `data/attack_cards_candidates.json` con estado `pendiente` |
| Señal | `backend/ai_service.py` | si la consulta nombra un ataque fuera del catálogo (o una ficha que no aplica) y el LLM está encendido, propone el borrador con las columnas y Event IDs del archivo |
| Endpoints | `backend/main.py` | `GET /api/attack-candidates` · `POST …/approve` · `POST …/reject` |
| UI | `AttackCandidates.tsx` (sección *Correlaciones ATT&CK*) | lista las candidatas con su evaluación y los botones **Aprobar** / **Rechazar** |

**Nunca automático:** `approve` es la única vía para que una ficha entre al
catálogo, y al hacerlo invalida la caché para que la próxima pregunta ya la
matchee **sin reiniciar** el backend. `reject` queda grabada **para siempre**:
una pregunta nueva con el mismo borrador no la revive. Hay dos tipos de
candidata — `nueva`, que agrega una ficha, y `mejora`, que reemplaza la regla
de una ficha vigente con `applicable:false` conservando sus sinónimos
originales (así no se pierde el alcance que hoy matchea).

```bash
GET  /api/attack-candidates                 # ?status=pendiente|aprobada|rechazada
POST /api/attack-candidates/{id}/approve    # escribe al catálogo + invalida la caché
POST /api/attack-candidates/{id}/reject     # marca la decisión para siempre
```

---

## Persistencia (sobrevive a reinicios y a reciclar la pestaña)

`backend/analysis_persist.py` espeja cada análisis en
`backend/data/analyses/<id>/`: el Excel original (`archivo.xlsx`), el estado
calculado (`summary.json`: resumen, chat, estado del índice RAG) y el informe
(`report.json`, que se genera **una sola vez**). Al arrancar, el backend los
restaura (`Análisis restaurados desde disco: N`).

En el frontend, `restore.ts` ofrece el botón **Restaurar**, `persist.ts`
guarda la sección activa y `AssistantChat.tsx` mantiene la conversación en
`sessionStorage` **por análisis**, así un F5 no pierde ni el chat ni dónde
estabas.

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
semántica («fuerza bruta» → T1110, «ransomware» → T1486) → alias de técnicas
revocadas → referencia anclada a la evidencia (4625 → T1110) → IDs
citados en la pregunta → bloque «Referencia técnica» en la respuesta →
degradación sin Chroma.

Suite unitaria (552 tests en 28 archivos: configuración del LLM, streaming
SSE, memoria de criterio few-shot, presupuesto de ventana, fichas de ataque,
scoring/casos/runner del eval):

```bash
cd backend && python -m pytest -q
```

Verificación de tipos en el frontend:

```bash
cd frontend && npx tsc --noEmit
```

---

## Evaluación del asistente (RAG+LLM)

El proyecto incluye un harness reproducible (`backend/eval/`) que puntúa el
asistente contra una muestra fija versionada (715 filas) con **verdad
calculable por pandas** — sin juez LLM ni respuestas escritas a mano: los 24
casos (agregados, negativas, recuperación y QA sobre evidencia) se calculan
contra el propio Excel. Compara el motor determinista contra el LLM y guarda
cada corrida en `backend/eval/runs/` (ignorado por git).

```bash
# desde la raíz del proyecto
backend\.venv\Scripts\python.exe backend\scripts\eval_rag.py --motor determinista   # línea base (sin LLM)
backend\.venv\Scripts\python.exe backend\scripts\eval_rag.py --motor ambos          # determinista + LLM
```

---

## Tecnologías

- **Frontend:** React, TypeScript, Vite, Tailwind CSS, Zustand, Recharts, Framer Motion, SheetJS, `@react-pdf/renderer`, `file-saver`.
- **Backend:** FastAPI, pandas, openpyxl, httpx, sse-starlette.
- **RAG (opcional):** ChromaDB, sentence-transformers (multilingüe, local y gratuito).
- **LLM (opcional):** proveedor cloud compatible con OpenAI (Groq por defecto: `openai/gpt-oss-120b`). Un reranker cross-encoder (`ms-marco-MiniLM-L6-v2`, local) reordena los candidatos antes de armar el prompt.
