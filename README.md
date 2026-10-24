# Comunidad Dojo – SOC Analyst Level 2 Front‑end

This repository contains a **modern React + Vite + TypeScript** front‑end and a **FastAPI** backend that together implement the requirements you described:

- **Upload any `.xlsx`** (the `SOC_L2_Logs_Ampliados_Multiples_Incidentes.xlsx` file) and parse it client‑side with **SheetJS**.
- **Call an AI classification/RAG service** (stubbed in the backend – replace with your real endpoint).
- **Show an interactive dashboard** – charts with **Recharts**, a live progress bar, and the animated **ServicePulse** line (Framer Motion).
- **Generate a PDF technical report** using **@react‑pdf/renderer** based on the markdown template from `proyecto_practica_analista_soc_nvl2`.
- **State management** via **Zustand** (easy, type‑safe, global store).
- **Secure rendering** – all strings from the backend are injected with `textContent` only (see `src/utils/dom.ts`).
- **Tailwind CSS** for design tokens and rapid styling.

## Project structure

```
proyecto_final_bootcamp_ComunidadDojo_SocAnalystNvl2/
├─ .gitignore            # ignores node_modules, .venv and OS/editor files
├─ backend/
│   ├─ main.py            # FastAPI service
│   └─ requirements.txt   # Python deps
├─ frontend/
│   ├─ package.json
│   ├─ vite.config.ts
│   ├─ tailwind.config.cjs
│   ├─ postcss.config.cjs
│   ├─ tsconfig.json
│   ├─ index.html
│   └─ src/
│       ├─ main.tsx
│       ├─ App.tsx
│       ├─ styles/
│       │   └─ index.css   # Tailwind directives
│       ├─ store/
│       │   └─ index.ts    # Zustand store
│       ├─ api/
│       │   └─ client.ts   # API helpers
│       ├─ components/
│       │   ├─ UploadExcel.tsx
│       │   ├─ ServicePulse.tsx
│       │   ├─ Dashboard.tsx
│       │   └─ ReportGenerator.tsx
│       └─ utils/
│           └─ dom.ts       # safe DOM helpers (optional)
└─ README.md
```

## How to run

```bash
# 1️⃣ Install backend deps (Python 3.12+)
cd backend
python -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8501   # runs on http://localhost:8501

# 2️⃣ Install frontend deps (Node 20+)
cd ../frontend
npm install                # installs React, Tailwind, etc.
npm run dev                # starts Vite on http://localhost:5300
```

The front‑end is proxied to the backend (see `frontend/vite.config.ts`). When you upload an Excel file, the backend processes it, streams progress events, and finally returns the analysis result that populates the dashboard and the PDF report.

---

### Next steps you’ll likely want to customise

- **Replace the dummy AI call** in `backend/main.py` (`call_ai_service`) with the real endpoint from `sistema‑delitos‑argentina` (add auth headers, etc.).
- **Adjust the branding** – edit Tailwind colours in `tailwind.config.cjs` or replace the `ServicePulse` animation.
- **Extend the dashboard** – add more Recharts components, filter by categories, etc.
- **Fine‑tune the PDF** – edit `ReportGenerator.tsx` to match your exact incident‑report format.

Happy coding!

## .gitignore

Asegúrate de que el proyecto incluye el archivo `.gitignore` en la raíz (ya creado). Ignora automáticamente:
- `node_modules/` y los artefactos de Vite (`dist/`, `.vite/`).
- El entorno virtual de Python `.venv/` y archivos de caché (`__pycache__/`).
- Archivos del sistema y del editor (`.DS_Store`, `Thumbs.db`, `.vscode/`, `.idea/`).
- Logs y otros temporales.

Con esto evitarás añadir dependencias de npm o el virtual env al repositorio.

