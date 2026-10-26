import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend")))

import io
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from main import app

# Columnas del dataset sintético, en el mismo orden que un Excel real.
COLUMNAS = [
    "Log_ID", "Timestamp", "Hostname", "Event_ID", "User", "Source_IP",
    "Destination", "Log_Source", "Event_Description", "Result", "Severity",
]

# Dataset sintético de 12 filas, fijado fila a fila: las aserciones de las
# tareas siguientes dependen de esta disposición exacta. Trampas ya resueltas:
# «Warning» se clasifica como crítico (SEVERITY_TO_LEVEL), así que el Log_ID 4
# es crítico; y ningún valor original contiene las cadenas critico/alto/medio/
# bajo, de modo que un filtro por nivel solo puede venir de la clasificación.
DATASET_12_FILAS = [
    (1,  "2026-01-10 08:00", "DC-01",    4624, "jsmith",     "10.10.2.15", "DC-01",  "Windows", "An account was successfully logged on to the system",     "Success", "Informational"),
    (2,  "2026-01-10 09:30", "WS-018",   4625, "jsmith",     "10.10.2.15", "WS-018", "Windows", "An account failed to log on with an invalid account",     "Failure", "High"),
    (3,  "2026-01-10 11:00", "WEB-01",   401,  "anon",       "10.10.2.40", "/login", "Nginx",   "An account failed to log on with an invalid account",     "Failure", "High"),
    (4,  "2026-01-10 16:00", "DC-01",    4740, "admin",      "10.10.2.20", "DC-01",  "Windows", "A user account was locked out after failed attempts",      "Failure", "Warning"),
    (5,  "2026-01-11 08:00", "FS-SRV01", 7045, "svc_backup", "10.10.2.30", "FS-SRV01", "Windows", "A service was installed successfully in the system",    "Success", "Medium"),
    (6,  "2026-01-11 09:15", "WS-018",   4625, "admin",      "10.10.2.15", "WS-018", "Windows", "An account failed to log on with an invalid account",     "Failure", "High"),
    (7,  "2026-01-11 12:00", "DC-01",    4624, "jsmith",     "10.10.2.15", "DC-01",  "Windows", "An account was successfully logged on to the system",     "Success", "Low"),
    (8,  "2026-01-11 17:30", "WEB-01",   500,  "anon",       "10.10.2.40", "/api",   "Nginx",   "Internal server error returned while processing request", "Failure", "Critical"),
    (9,  "2026-01-12 08:00", "DC-01",    4624, "jsmith",     "10.10.2.25", "DC-01",  "Windows", "An account was successfully logged on to the system",     "Success", "Informational"),
    (10, "2026-01-12 10:00", "FS-SRV01", 514,  "svc_backup", "10.10.2.30", "FS-SRV01", "Windows", "The print spooler service started successfully",        "Success", "Low"),
    (11, "2026-01-12 13:00", "WS-018",   4625, "anon",       "10.10.2.15", "WS-018", "Windows", "An account failed to log on with an invalid account",     "Failure", "Medium"),
    (12, "2026-01-12 18:00", "WEB-01",   200,  "admin",      "10.10.2.40", "/index", "Nginx",   "Request completed successfully by the web server",        "Success", "Informational"),
]


def xlsx_bytes() -> io.BytesIO:
    """Excel sintético de 12 filas en memoria.

    Los Timestamp van como texto a propósito: así el backend es quien debe
    convertirlos a datetime al ingerir (comportamiento que se quiere probar).
    """
    df = pd.DataFrame(DATASET_12_FILAS, columns=COLUMNAS)
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)
    return buffer


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def analysis_id(client: TestClient) -> str:
    """Sube el Excel sintético y devuelve el id cuando el análisis está «done».

    Si el análisis termina en «error», el fixture falla mostrando el mensaje
    del backend para diagnosticar desde el test.
    """
    res = client.post(
        "/api/analyze",
        files={
            "file": (
                "dataset_sintetico.xlsx",
                xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert res.status_code == 200, res.text
    analysis_id = res.json()["analysis_id"]
    # El endpoint responde «pending» y el procesamiento corre en background:
    # se pulea el estado con timeout para no colgar la suite si algo falla.
    deadline = time.monotonic() + 60
    state = None
    while time.monotonic() < deadline:
        state = client.get(f"/api/analysis/{analysis_id}").json()
        if state["status"] == "done":
            return analysis_id
        if state["status"] == "error":
            pytest.fail(f"El análisis terminó en error: {state.get('error')}")
        time.sleep(0.1)
    pytest.fail(f"El análisis no terminó en 60 s (estado final: {state and state['status']})")
