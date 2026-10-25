"""Prueba rápida del backend (sin levantar el servidor).

Uso:
    python test_backend.py                       # usa el archivo por defecto
    python test_backend.py <ruta/al/archivo.xlsx>
    SOC_SAMPLE_FILE=<ruta> python test_backend.py

Comprueba el flujo completo: /api/analyze → resumen → /rows → /report.
"""

import os
import sys

sys.path.append(os.path.abspath("backend"))
from main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

DEFAULT_FILE = r"C:/Users/Brian/Downloads/SOC_L2_Logs_Ampliados_Multiples_Incidentes.xlsx"
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
print("OK")
