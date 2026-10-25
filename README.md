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
| 01 | **Carga de archivos**: importa `.xlsx` (todas las hojas) **y visualiza sus filas** con paginación, búsqueda y filtros | `UploadSection` + `DataTable` |
| 02 | **Análisis de seguridad**: identifica eventos sospechosos y **correlaciona IP ↔ hosts ↔ tiempo** | `SecurityAnalysis` + `backend/main.py` |
| 03 | **Dashboard**: KPIs y **gráficos interactivos por criticidad** (distribución, eventos/día, top hosts) | `Dashboard` |
| 04 | **Clasificación de riesgos**: Crítico / Alto / Medio / Bajo **con justificación y ejemplos** | `RiskClassification` |
| 05 | **Informe técnico**: documento estructurado completo (7 secciones) | `ReportGenerator` |
| 06 | **Exportación**: descarga del informe en **PDF** | `ReportGenerator` (`@react-pdf/renderer` + `file-saver`) |

**Requisitos adicionales**

- Interfaz moderna e intuitiva en **español**, con navegación lateral y Dashboard como pantalla principal.
- **IA integrada** que apoya el análisis de datos **y** la redacción del informe (`backend/ai_service.py`).
- **Rigor analítico**: cada hallazgo cita sus evidencias (`Log_ID`, `Timestamp`, `Host`, `Event_ID`, `Source_IP`);
  la IA solo recibe datos ya calculados y nunca agrega cifras propias.
- Renderizado seguro: los textos del backend se insertan con `textContent` (`frontend/src/utils/dom.ts`).

---

## Estructura del proyecto

```
proyecto_final_bootcamp_ComunidadDojo_SocAnalystNvl2/
├─ .gitignore                 # node_modules, .venv, .env, logs, editores
├─ README.md
├─ test_backend.py            # prueba end-to-end del backend
├─ backend/
│  ├─ main.py                 # FastAPI: /analyze, /rows, /report, SSE (puerto 8500)
│  ├─ ai_service.py           # IA de redacción con grounding estricto + fallback local
│  └─ requirements.txt
└─ frontend/                  # React + Vite + TypeScript (puerto 5300)
   ├─ index.html
   ├─ vite.config.ts          # proxy /api → 127.0.0.1:8500
   └─ src/
      ├─ App.tsx              # layout + cabecera por sección
      ├─ store/index.ts       # Zustand (tipado)
      ├─ api/client.ts        # axios + SSE
      └─ components/
         ├─ Sidebar.tsx
         ├─ UploadSection.tsx / UploadExcel.tsx / DataTable.tsx
         ├─ Dashboard.tsx
         ├─ SecurityAnalysis.tsx
         ├─ RiskClassification.tsx
         ├─ ReportGenerator.tsx
         └─ ServicePulse.tsx / SummaryCard.tsx / DashboardSection.tsx
```

---

## Cómo ejecutarlo

```bash
# 1) Backend (Python 3.11+) — http://127.0.0.1:8500
cd backend
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
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

## IA integrada

`backend/ai_service.py` redacta el informe a partir de un contexto que contiene
**únicamente datos calculados** de los registros (estadísticas + evidencias).

- **Modo local (por defecto):** generador determinista con plantillas basadas en los
  mismos números → siempre entrega un informe completo y verificable.
- **Modo remoto (opcional):** definí estas variables de entorno para usar un modelo
  compatible con la API de OpenAI (`/v1/chat/completions`):

```bash
export AI_API_URL="https://<tu-servidor>/v1/chat/completions"   # requerido para modo remoto
export AI_API_KEY="sk-..."          # opcional
export AI_MODEL="gpt-4o-mini"       # opcional
export AI_TIMEOUT=60                # opcional (segundos)
```

Si la llamada falla o la respuesta no es JSON válido, el servicio **cae
automáticamente al modo local**: el informe nunca queda vacío.
La interfaz muestra en todo momento la fuente utilizada (IA remota / IA local grounded).

---

## Pruebas

```bash
# desde la raíz del proyecto
python test_backend.py                                  # archivo por defecto
python test_backend.py ruta/archivo.xlsx                # archivo propio
set SOC_SAMPLE_FILE=ruta\archivo.xlsx && python test_backend.py
```

Comprueba: subida → análisis (`done`) → resumen y niveles → endpoint `/rows`
con filtros → generación del informe.

Verificación de tipos en el frontend:

```bash
cd frontend && npx tsc --noEmit
```

---

## Tecnologías

- **Frontend:** React, TypeScript, Vite, Tailwind CSS, Zustand, Recharts, Framer Motion, SheetJS, `@react-pdf/renderer`, `file-saver`.
- **Backend:** FastAPI, pandas, openpyxl, httpx, sse-starlette.

---

## .gitignore

En la raíz ya está creado e ignora: `node_modules/`, `dist/`, `.vite/`, `.env*`,
`.venv/`, `__pycache__/`, bases de datos, logs y archivos de editor/sistema.
