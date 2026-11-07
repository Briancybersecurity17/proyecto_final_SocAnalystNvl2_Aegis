"""Prueba rápida del backend (sin levantar el servidor).

Uso:
    python test_backend.py                       # usa el archivo por defecto
    python test_backend.py <ruta/al/archivo.xlsx>
    SOC_SAMPLE_FILE=<ruta> python test_backend.py

Comprueba el flujo completo: /api/analyze → resumen → /rows → /report →
/rag/status → /ask (recuperación de evidencias y validación de entrada).
"""

import os
import sys

sys.path.append(os.path.abspath("backend"))
from main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Muestra versionada en el repo: funciona en cualquier máquina sin rutas personales.
DEFAULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "backend", "eval", "fixtures", "sample.xlsx")
FILE_PATH = sys.argv[1] if len(sys.argv) > 1 else os.getenv("SOC_SAMPLE_FILE", DEFAULT_FILE)

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

if not os.path.exists(FILE_PATH):
    sys.exit(f"No existe el archivo de prueba: {FILE_PATH}")

client = TestClient(app)

with open(FILE_PATH, "rb") as f:
    response = client.post("/api/analyze", files={"file": ("SOC.xlsx", f, MIME)})

print("analyze status:", response.status_code)
analysis_id = response.json()["analysis_id"]

status = "pending"
for _ in range(20):
    status = client.get(f"/api/analysis/{analysis_id}").json()["status"]
    if status in ("done", "error"):
        break
print("analysis status:", status)

result = client.get(f"/api/analysis/{analysis_id}").json()
if status != "done":
    sys.exit(f"El análisis falló: {result.get('error')}")

summary = result["summary"]
print("rows:", summary["rows"])
print("risk:", summary["risk"], "| levels:", summary["level_counts"])
print("suspicious:", summary["suspicious_events"])

rows = client.get(
    f"/api/analysis/{analysis_id}/rows",
    params={"page": 1, "page_size": 3, "suspicious": True},
).json()
print("rows endpoint:", rows["total"], "coincidencias |", len(rows["rows"]), "devueltas")

report = client.post(f"/api/analysis/{analysis_id}/report").json()
print("report source:", report["source"])
print("report sections:", len(report["findings"]), "hallazgos |",
      len(report["action_plan"]), "acciones")

# --- Asistente IA (recuperación híbrida: semántica + léxica) ---
rag = client.get("/api/rag/status", params={"analysis_id": analysis_id}).json()
print(
    "rag:", "disponible" if rag.get("available") else "no disponible",
    "| motor:", rag.get("model"),
    "| eventos indexados:", rag.get("events", rag.get("total", 0)),
)

ask = client.post(
    f"/api/analysis/{analysis_id}/ask",
    json={"question": "failed authentication events", "n_results": 5},
).json()
print(
    "ask:", ask.get("retrieved"), "evidencias | confianza:", ask.get("confidence"),
    "| motor:", ask.get("engine"),
)
if not ask.get("retrieved"):
    sys.exit("El asistente no recuperó evidencias")
if not ask.get("answer"):
    sys.exit("El asistente no devolvió respuesta")
if not ask.get("evidence"):
    sys.exit("El asistente no devolvió evidencias citables")

# Cada evidencia debe traer sus campos citables del archivo importado
for item in ask["evidence"]:
    if item.get("log_id") and item["log_id"] != "—":
        print("   evidencia:", item["log_id"], "|", item["host"], "| nivel", item["level"])

# Consulta de AGREGACIÓN → cifras exactas del resumen, no filas recuperadas
agg = client.post(
    f"/api/analysis/{analysis_id}/ask",
    json={"question": "¿Qué hosts concentran los eventos críticos?"},
).json()
print(
    "ask agregado:", agg.get("mode"), "| confianza:", agg.get("confidence"),
    "| recuperados:", agg.get("retrieved"),
)
if agg.get("mode") != "aggregate":
    sys.exit("La consulta de agregación no se resolvió con cifras exactas")
if agg.get("confidence") != "alta":
    sys.exit("La consulta de agregación debería tener confianza alta")
if agg.get("retrieved"):
    sys.exit("Una consulta de agregación no debe devolver evidencia fila a fila")
if "Distribución" not in agg.get("answer", ""):
    sys.exit("La respuesta de agregación no incluye la distribución por criticidad")

# Consulta demasiado corta → 400 (validación)
short = client.post(f"/api/analysis/{analysis_id}/ask", json={"question": "ab"})
print("ask corta:", short.status_code, "(esperado 400)")
if short.status_code != 400:
    sys.exit("Una consulta de 2 caracteres debería devolver 400")

print("OK")
