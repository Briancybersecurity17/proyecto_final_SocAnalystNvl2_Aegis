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


def test_facets_criticidad_ignora_su_propio_filtro_de_nivel(client, analysis_id):
    # spec §4.4: la faceta cuenta ignorando SU filtro (aquí el canónico
    # `level`), para que el panel permita cambiar de valor. Sin ese salto,
    # con ?level=critico la faceta solo ofrecería «critico».
    res = client.get(f"/api/analysis/{analysis_id}/facets",
                     params={"level": "critico"}).json()
    campo = next(f for f in res["fields"] if f["name"] == "_level")
    conteos = {v["value"]: v["count"] for v in campo["values"]}
    # Los conteos son los del dataset completo (como si no hubiera filtro);
    # se comparan como dict porque «critico» y «medio» empatan a 2 y el
    # orden de value_counts con empate no está garantizado.
    assert campo["label"] == "Criticidad"
    assert campo["distinct"] == 4
    assert conteos == {"critico": 2, "alto": 3, "medio": 2, "bajo": 5}
    # ...pero el total filtrado sí refleja el filtro completo.
    assert res["filtered_total"] == 2


def test_facets_hostname_ignora_su_propio_filtro(client, analysis_id):
    # Misma regla para una faceta normal: con ?Hostname=DC-01 activo, la
    # faceta Hostname sigue ofreciendo todos los hosts con sus conteos.
    res = client.get(f"/api/analysis/{analysis_id}/facets",
                     params={"Hostname": "DC-01"}).json()
    campo = next(f for f in res["fields"] if f["name"] == "Hostname")
    conteos = {v["value"]: v["count"] for v in campo["values"]}
    # WS-018 y WEB-01 empatan a 3: se compara como dict, no como lista.
    assert campo["distinct"] == 4
    assert conteos == {"DC-01": 4, "WS-018": 3, "WEB-01": 3, "FS-SRV01": 2}
    assert res["filtered_total"] == 4


def test_facets_sospechosos_ignora_su_propio_filtro(client, analysis_id):
    # El canónico `suspicious` es el «propio» filtro de la faceta derivada
    # _suspicious: sin el salto, con ?suspicious=true solo ofrecería «True».
    res = client.get(f"/api/analysis/{analysis_id}/facets",
                     params={"suspicious": "true"}).json()
    campo = next(f for f in res["fields"] if f["name"] == "_suspicious")
    conteos = {v["value"]: v["count"] for v in campo["values"]}
    assert res["filtered_total"] == 6
    assert campo["distinct"] == 2
    assert conteos == {"True": 6, "False": 6}
