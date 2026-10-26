import pandas as pd
from main import analysis_store


def test_ingesta_enriquece_el_dataframe(client, analysis_id):
    df = analysis_store[analysis_id]["dataframe"]
    assert {"_level", "_reason", "_suspicious"} <= set(df.columns)
    assert pd.api.types.is_datetime64_any_dtype(df["Timestamp"])


def test_columnas_derivadas_no_filtran_en_rows(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/rows", params={"page_size": 50}).json()
    assert all(not c.startswith("_") for c in res["columns"])
    assert {"risk_level", "risk_reason", "suspicious"} <= set(res["rows"][0])


def test_summary_sin_filtros_es_identico_al_global(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary").json()
    assert res["total"] == 12 and res["filtered_total"] == 12 and res["rows"] == 12
    assert res["level_counts"] == {"critico": 2, "alto": 3, "medio": 2, "bajo": 5}

def test_summary_ventana_temporal_acota(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"from": "2026-01-11T00:00:00", "to": "2026-01-12T23:59:59"}).json()
    assert res["total"] == 12 and res["rows"] == 8
    assert res["level_counts"]["critico"] == 1

def test_summary_facetas_de_campos_distintos_son_and(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params=[("Hostname", "DC-01"), ("User", "admin")]).json()
    assert res["rows"] == 1

def test_summary_misma_faceta_repetida_es_or(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params=[("Hostname", "DC-01"), ("Hostname", "WEB-01")]).json()
    assert res["rows"] == 7

def test_summary_level_mas_ventana(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"level": "critico", "from": "2026-01-11T00:00:00"}).json()
    assert res["rows"] == 1

def test_summary_sin_coincidencias_devuelve_cero(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"Hostname": "NOEXISTE"}).json()
    assert res["rows"] == 0 and res["total"] == 12

def test_summary_from_mayor_que_to_devuelve_400(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"from": "2026-01-12", "to": "2026-01-10"})
    assert res.status_code == 400

def test_summary_level_invalido_devuelve_400(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary", params={"level": "nivelfalso"})
    assert res.status_code == 400

def test_summary_faceta_inexistente_se_ignora(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"ColumnaFantasma": "x"}).json()
    assert res["rows"] == 12

def test_summary_desconocido_devuelve_404(client):
    assert client.get("/api/analysis/no-existe/summary").status_code == 404

def test_na_sin_filtro_temporal_entra_y_con_filtro_sale(client, analysis_id_na):
    sin_filtro = client.get(f"/api/analysis/{analysis_id_na}/summary").json()
    con_filtro = client.get(f"/api/analysis/{analysis_id_na}/summary",
                            params={"from": "2026-01-01"}).json()
    assert sin_filtro["rows"] == 2 and con_filtro["rows"] == 1

def test_summary_archivo_minimo_sin_timestamp_ni_severity(client, analysis_id_minimo):
    res = client.get(f"/api/analysis/{analysis_id_minimo}/summary",
                     params={"from": "2026-01-01"}).json()
    assert res["rows"] == 2 and res["level_counts"]["bajo"] == 2


def test_facets_detecta_campos_del_archivo(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/facets").json()
    nombres = [f["name"] for f in res["fields"]]
    assert {"Hostname", "User", "Result"} <= set(nombres)
    assert "Timestamp" not in nombres and "Log_ID" not in nombres

def test_facets_conteos_acotados_por_el_filtro(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/facets", params={"User": "jsmith"}).json()
    campo = next(f for f in res["fields"] if f["name"] == "Hostname")
    assert res["filtered_total"] == 4
    assert [v["value"] for v in campo["values"]] == ["DC-01", "WS-018"]

def test_facets_no_ofrece_event_description(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/facets").json()
    assert "Event_Description" not in [f["name"] for f in res["fields"]]
