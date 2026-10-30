"""Tarea 1: event_id_counts en el summary + justificación acotada al nivel.

Dos defectos del resumen ejecutivo, en un solo archivo:
- /summary no expone el conteo de Event_IDs, así que el informe no puede
  citar los IDs dominantes del archivo completo.
- risk_justification presenta los conteos de hosts y Event IDs (que se
  calculan DENTRO del nivel más alto) como si fueran globales: DC-01
  aparece con sus 4 registros aunque solo 1 sea crítico (bug DC-01 33 vs 91).
"""

import pandas as pd

from main import analysis_store
from tests.conftest import COLUMNAS, DATASET_12_FILAS


def test_summary_expone_event_id_counts(client, analysis_id):
    """GET /summary trae event_id_counts: top-20 de Event_ID, claves como texto.

    El conteo esperado se recalcula con pandas en el propio test sobre el
    dataset sintético, no se escribe a mano.
    """
    res = client.get(f"/api/analysis/{analysis_id}/summary").json()
    event_ids = pd.DataFrame(DATASET_12_FILAS, columns=COLUMNAS)["Event_ID"]
    esperado = {
        str(k): int(v) for k, v in event_ids.value_counts().head(20).items()
    }
    assert res["event_id_counts"] == esperado
    assert sum(res["event_id_counts"].values()) == 12
    # 4624 aparece en las filas 1, 7 y 9 del dataset sintético (el «4» del
    # brief venía del dataset original de 91 filas del informe DC-01).
    assert res["event_id_counts"]["4624"] == 3


def test_summary_sin_columna_event_id_devuelve_vacio(client, analysis_id_minimo):
    """Un archivo sin Event_ID no inventa conteos: event_id_counts es {}."""
    res = client.get(f"/api/analysis/{analysis_id_minimo}/summary").json()
    assert res["event_id_counts"] == {}


def test_risk_justification_acota_los_conteos_al_nivel(client, analysis_id):
    """La justificación del riesgo declara que hosts y Event IDs son del nivel.

    DC-01 tiene registros en varios niveles (bajo, crítico): la cifra citada
    debe ser la del nivel más alto, no el total global (4).
    """
    res = client.get(f"/api/analysis/{analysis_id}/summary").json()
    texto = res["risk_justification"]
    assert "en este nivel" in texto
    assert "Hosts con más registros: " not in texto

    df = analysis_store[analysis_id]["dataframe"]
    esperado = int(
        df[df["_level"] == "critico"]["Hostname"].value_counts()["DC-01"]
    )
    assert esperado == 1                      # el total global de DC-01 es 4
    assert f"DC-01 ({esperado})" in texto
