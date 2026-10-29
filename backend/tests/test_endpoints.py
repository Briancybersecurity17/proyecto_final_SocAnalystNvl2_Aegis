import io

import pandas as pd
import pytest

from main import analysis_store
from tests.conftest import COLUMNAS, DATASET_12_FILAS, _subir_y_esperar


def test_ingesta_enriquece_el_dataframe(client, analysis_id):
    df = analysis_store[analysis_id]["dataframe"]
    assert {"_level", "_reason", "_suspicious"} <= set(df.columns)
    assert pd.api.types.is_datetime64_any_dtype(df["Timestamp"])


def test_columnas_derivadas_no_filtran_en_rows(client, analysis_id):
    res = client.get(f"/api/analysis/{analysis_id}/rows", params={"page_size": 50}).json()
    assert all(not c.startswith("_") for c in res["columns"])
    assert {"risk_level", "risk_reason", "suspicious"} <= set(res["rows"][0])


# Red de regresión del refactor de /rows (Tarea 6): protege que los 4 filtros
# de siempre casen exactamente igual con los mismos datos, tanto antes como
# después de delegar en el motor compartido `filters`. Debe pasar SIEMPRE.
def test_rows_refactor_es_identico(client, analysis_id):
    base = f"/api/analysis/{analysis_id}/rows"
    todos = client.get(base, params={"page_size": 200}).json()
    assert todos["total"] == 12 and todos["total_rows"] == 12

    por_nivel = client.get(base, params={"page_size": 200, "level": "critico"}).json()
    assert por_nivel["total"] == 2

    sospechosos = client.get(base, params={"page_size": 200, "suspicious": "true"}).json()
    assert sospechosos["total"] >= 1

    por_result = client.get(base, params={"page_size": 200, "result": "Failure"}).json()
    assert por_result["total"] == 6

    por_q = client.get(base, params={"page_size": 200, "q": "jsmith"}).json()
    assert por_q["total"] == 4

def test_q_no_busca_en_columnas_derivadas(client, analysis_id):
    # La búsqueda libre no debe colar la clasificación interna: «critico» solo
    # existe en _level (derivada), no en los datos del archivo que ve el analista.
    res = client.get(f"/api/analysis/{analysis_id}/rows",
                     params={"page_size": 200, "q": "critico"}).json()
    assert res["total"] == 0      # "critico" solo aparece en _level, no en los datos


def test_suspicious_on_se_comporta_como_true(client, analysis_id):
    # R18: el toggle «solo sospechosos» puede llegar como on/yes/1 (el antiguo
    # bool de FastAPI los aceptaba todos). «on» debe filtrar igual que «true»;
    # enseñar TODAS las filas creyendo el filtro puesto es un engaño peor que
    # un error.
    on = client.get(f"/api/analysis/{analysis_id}/summary",
                    params={"suspicious": "on"}).json()
    true = client.get(f"/api/analysis/{analysis_id}/summary",
                      params={"suspicious": "true"}).json()
    assert on["filtered_total"] == true["filtered_total"] == 6
    # y en la tabla, donde el engaño sería visible para el analista
    filas = client.get(f"/api/analysis/{analysis_id}/rows",
                       params={"page_size": 200, "suspicious": "on"}).json()
    assert filas["total"] == 6
    # la forma canónica falsa («off») no restringe nada: sigue viéndose todo
    off = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"suspicious": "off"}).json()
    assert off["filtered_total"] == 12


def test_suspicious_basura_devuelve_400(client, analysis_id):
    # R18: un booleano ilegible es error del cliente (antes el motor de Query
    # respondía 422; ahora el motor de filtros responde 400 con el motivo) —
    # nunca un 200 silencioso que ignora el filtro.
    res = client.get(f"/api/analysis/{analysis_id}/rows",
                     params={"page_size": 200, "suspicious": "quizas"})
    assert res.status_code == 400
    resumen = client.get(f"/api/analysis/{analysis_id}/summary",
                         params={"suspicious": "quizas"})
    assert resumen.status_code == 400


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


def test_summary_con_filtros_mantiene_metadatos_del_analisis(client, analysis_id):
    # spec §4.3 «mismo AnalysisSummary» + R14: file_name, sheet_names,
    # missing_columns y generated_at son datos del ARCHIVO, no de la
    # selección: deben seguir presentes e iguales con filtros activos.
    # ai_insights se recalcula sobre el resumen acotado pero también va.
    res = client.get(f"/api/analysis/{analysis_id}/summary",
                     params={"level": "critico"}).json()
    assert res["rows"] == 2                       # el filtro está activo de verdad
    assert res["file_name"] == "dataset_sintetico.xlsx"
    assert isinstance(res["generated_at"], str) and res["generated_at"]
    assert isinstance(res["sheet_names"], list) and res["sheet_names"]
    assert res["missing_columns"] == []           # el dataset sintético es completo
    assert isinstance(res["ai_insights"], list)


def test_facets_result_ignora_su_propio_filtro(client, analysis_id):
    # Mismo hueco que las facetas derivadas, para una faceta original: con
    # ?result=Failure activo, la faceta Result debe seguir ofreciendo
    # Success y Failure (spec §4.4) con los conteos del dataset completo.
    res = client.get(f"/api/analysis/{analysis_id}/facets",
                     params={"result": "Failure"}).json()
    campo = next(f for f in res["fields"] if f["name"] == "Result")
    conteos = {v["value"]: v["count"] for v in campo["values"]}
    assert res["filtered_total"] == 6
    assert campo["distinct"] == 2
    assert conteos == {"Success": 6, "Failure": 6}


def test_facets_criticidad_conserva_otros_filtros_activos(client, analysis_id):
    # Invariante del salto: saltar el filtro PROPIO no apaga los demás.
    # level=critico&Hostname=DC-01 → la faceta Criticidad se cuenta solo
    # sobre las filas DC-01 (bajo 3, critico 1) porque el filtro de
    # Hostname sigue aplicando, mientras el de nivel se salta.
    res = client.get(f"/api/analysis/{analysis_id}/facets",
                     params={"level": "critico", "Hostname": "DC-01"}).json()
    campo = next(f for f in res["fields"] if f["name"] == "_level")
    conteos = {v["value"]: v["count"] for v in campo["values"]}
    # DC-01 = Log_IDs 1, 4, 7, 9 → niveles bajo, critico, bajo, bajo.
    assert res["filtered_total"] == 1             # DC-01 ∩ critico = Log_ID 4
    assert campo["distinct"] == 2
    assert conteos == {"bajo": 3, "critico": 1}


# --- Historial de subidas (spec sub-3 §3.5) --------------------------------

def test_analyses_lista_solo_done_con_campos(client, analysis_id, analysis_id_minimo):
    """Los análisis completados aparecen con los 5 campos del contrato."""
    r = client.get("/api/analyses")
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list) and items
    ids = [item["analysis_id"] for item in items]
    assert analysis_id in ids
    assert analysis_id_minimo in ids
    for item in items:
        assert set(item) == {"analysis_id", "file_name", "generated_at", "rows", "risk"}
        assert item["file_name"]
        assert item["generated_at"]
        assert item["rows"] > 0


def test_analyses_orden_inverso_y_limite_8(client):
    """Lo más reciente primero y nunca más de 8 entradas."""
    from main import analysis_store

    sinteticos = {
        f"synth-{n}": {
            "status": "done",
            "summary": {
                "file_name": f"f{n}.xlsx",
                "generated_at": "2026-10-27T00:00:00",
                "rows": n + 1,
                "risk": "bajo",
            },
            "error": None,
        }
        for n in range(10)
    }
    analysis_store.update(sinteticos)
    try:
        items = client.get("/api/analyses").json()
        assert len(items) == 8
        assert items[0]["analysis_id"] == "synth-9"
        assert items[-1]["analysis_id"] == "synth-2"
        # limit fuera de rango es error del cliente: 0 dispararía el
        # gotcha de done[-0:] == done[0:] (devolvería TODO) y 9 excedería
        # el contrato de «últimos 8».
        assert client.get("/api/analyses", params={"limit": 0}).status_code == 422
        assert client.get("/api/analyses", params={"limit": 9}).status_code == 422
    finally:
        for key in sinteticos:
            analysis_store.pop(key, None)


def test_analyses_store_vacio_devuelve_lista_vacia(client):
    """Reinicio del backend: lista vacía, nunca un 500."""
    from main import analysis_store

    backup = dict(analysis_store)
    analysis_store.clear()
    try:
        r = client.get("/api/analyses")
        assert r.status_code == 200
        assert r.json() == []
    finally:
        analysis_store.update(backup)


# ---------------------------------------------------------------------------
# Auditoria del RAG en no-stream: grounding_rechazado y rag_examples_used
# (Task 4 del harness de evaluacion: el runner lee estos campos para medir
# cuantas respuestas caen al determinista por rechazo del guard y cuantos
# ejemplos few-shot viajan de verdad.)
# ---------------------------------------------------------------------------
_Q_DC01 = "¿Qué pasó en el host DC-01?"


@pytest.fixture(scope="module")
def analysis_evt(client):
    """Análisis con Log_ID con forma «EVT-00001» (el dataset sintético usa
    IDs numéricos: con ellos el guard no encuentra familia «EVT» que validar
    y no hay nada que rechazar)."""
    df = pd.DataFrame(
        [(f"EVT-{n:05d}",) + tuple(fila[1:]) for n, fila in enumerate(DATASET_12_FILAS, 1)],
        columns=COLUMNAS,
    )
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)
    return _subir_y_esperar(client, "evt-ids.xlsx", buffer)


@pytest.fixture(autouse=True)
def _aislamiento_ask(tmp_path, monkeypatch):
    """La memoria del analista y la config de IA jamás se tocan en tests.

    - feedback_store: JSON temporal + caché invalidada (mismo patron que
      test_feedback_store).
    - ai_config: CONFIG_PATH en tmp (patron del fixture `ai` de
      test_chat_stream) para no escribir backend/data/ai_config.json.
    """
    import feedback_store
    from main import ai_config

    monkeypatch.setattr(feedback_store, "FEEDBACK_PATH", tmp_path / "fb.json")
    monkeypatch.setattr(feedback_store, "_cache", None)
    monkeypatch.setattr(ai_config, "CONFIG_PATH", tmp_path / "ai_config.json")
    yield
    monkeypatch.setattr(feedback_store, "_cache", None)


def _llm_activo(monkeypatch, respuesta):
    """Activa el LLM (config aislada) con `chat` falso que devuelve `respuesta`."""
    from main import ai_config

    ai_config.update({"enabled": True})

    async def fake_chat(messages, cfg=None):
        return respuesta

    monkeypatch.setattr(ai_config, "chat", fake_chat)


def test_ask_no_stream_avisa_cuando_el_guard_rechaza(client, analysis_evt, monkeypatch):
    """Respuesta con un Log_ID inventado → se descarta y se entrega el
    determinista, con `grounding_rechazado` en warnings (antes invisible)."""
    client.delete(f"/api/analysis/{analysis_evt}/chat")
    _llm_activo(monkeypatch, "El host fue comprometido; revise EVT-99999 inmediatamente.")

    res = client.post(f"/api/analysis/{analysis_evt}/ask", json={"question": _Q_DC01})
    body = res.json()

    assert res.status_code == 200
    assert "grounding_rechazado" in body["warnings"]
    assert body["engine"] == "deterministic"
    # El guard entregó la respuesta determinista: el ID inventado NO aparece.
    assert "EVT-99999" not in body["answer"]


def test_ask_no_stream_expone_rag_examples_used(client, analysis_id, monkeypatch):
    """Con criterio previo del analista, el bloque few-shot se inyecta y el
    número de ejemplos queda expuesto en la respuesta (auditoría)."""
    import feedback_store

    feedback_store.add_correction(_Q_DC01, "Cita los EVT- recuperados y su host.")
    _llm_activo(monkeypatch, "Los eventos recuperados de DC-01 muestran intentos de sesión.")

    body = client.post(f"/api/analysis/{analysis_id}/ask", json={"question": _Q_DC01}).json()

    assert body["rag_examples_used"] >= 1


def test_ask_con_llm_ok_no_marca_rechazo(client, analysis_id, monkeypatch):
    """Camino sano: engine llm, sin rechazo y sin ejemplos (memoria vacía)."""
    _llm_activo(monkeypatch, "Los eventos recuperados muestran intentos fallidos de cuenta.")

    body = client.post(f"/api/analysis/{analysis_id}/ask", json={"question": _Q_DC01}).json()

    assert body["engine"] == "llm"
    assert "grounding_rechazado" not in body["warnings"]
    assert body["rag_examples_used"] == 0


def test_ask_llm_apagado_con_memoria_no_informa_ejemplos(client, analysis_id, monkeypatch):
    """El LLM apagado con criterio guardado NO informa ejemplos: el bloque
    few-shot no se construye ni viaja en el prompt, así que un conteo > 0
    sería «ejemplos inyectados» en un prompt que jamás se envió. Contaminaría
    la cobertura few-shot de la pasada determinista del harness."""
    import feedback_store

    feedback_store.add_correction(_Q_DC01, "Cita los EVT- recuperados y su host.")
    from main import ai_config

    ai_config.update({"enabled": False})       # LLM apagado (config aislada)

    body = client.post(f"/api/analysis/{analysis_id}/ask", json={"question": _Q_DC01}).json()

    assert body["engine"] == "deterministic"
    assert body["rag_examples_used"] == 0


def test_ask_agregado_expone_rag_examples_cero(client, analysis_id):
    """La rama aggregate no pasa por el modelo: el conteo es 0, explícito."""
    body = client.post(f"/api/analysis/{analysis_id}/ask",
                       json={"question": "¿Cuántos eventos hay en total?"}).json()
    assert body["mode"] == "aggregate"
    assert body["rag_examples_used"] == 0
