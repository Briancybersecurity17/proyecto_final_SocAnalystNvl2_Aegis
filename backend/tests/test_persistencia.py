"""Persistencia de análisis en disco + informe generado una sola vez.

Spec arquitectónico A: el backend deja de perder todo al reiniciar. Cada
análisis vive en `backend/data/analyses/{id}/` (redirigido a tmp por el
fixture `analyses_aislado` de conftest), y el informe técnico se genera UNA
vez (`report.json`): GET lo sirve cacheado y POST solo regenera con
`?regenerate=true`.
"""

import os
import time

import pytest

from main import analysis_store, ai_config
import main
import analysis_persist


@pytest.fixture
def ai(tmp_path, monkeypatch):
    """Configuración aislada en tmp: los tests no tocan el archivo real."""
    monkeypatch.setattr(ai_config, "CONFIG_PATH", tmp_path / "ai_config.json")
    return ai_config


# ---------------------------------------------------------------------------
# Helpers: subida propia de análisis (los fixtures de sesión se usan solo en
# lectura; los tests que mutan un análisis necesitan el suyo).
# ---------------------------------------------------------------------------
def _subir(client, filename="dataset_persistencia.xlsx") -> str:
    from tests.conftest import xlsx_bytes

    res = client.post(
        "/api/analyze",
        files={
            "file": (
                filename,
                xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert res.status_code == 200, res.text
    aid = res.json()["analysis_id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = client.get(f"/api/analysis/{aid}").json()
        if state["status"] == "done":
            return aid
        if state["status"] == "error":
            pytest.fail(f"El análisis terminó en error: {state.get('error')}")
        time.sleep(0.1)
    pytest.fail("El análisis no terminó en 60 s")


@pytest.fixture(scope="module")
def aid(client) -> str:
    """Análisis propio del módulo: lo comparten los tests de solo lectura."""
    return _subir(client)


# ---------------------------------------------------------------------------
# 1. La ingesta escribe en disco
# ---------------------------------------------------------------------------

def test_subida_persiste_excel_y_summary_en_disco(aid):
    directorio = analysis_persist.dir_analisis(aid)
    excel = directorio / "archivo.xlsx"
    summary = directorio / "summary.json"
    assert excel.is_file() and excel.stat().st_size > 0
    assert summary.is_file()

    import json

    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["status"] == "done"
    assert data["summary"]["rows"] == 12
    assert data["summary"]["file_name"] == "dataset_persistencia.xlsx"
    assert data["chat_history"] == []


def test_informe_generado_se_persiste_en_disco(client, aid):
    res = client.post(f"/api/analysis/{aid}/report")
    assert res.status_code == 200, res.text
    report = analysis_persist.cargar_informe(aid)
    assert report is not None
    assert report["generated_at"] == res.json()["generated_at"]


# ---------------------------------------------------------------------------
# 2. El informe se genera una vez y se reutiliza
# ---------------------------------------------------------------------------

def test_get_report_sirve_el_cacheado_sin_regenerar(client, aid, monkeypatch):
    """GET devuelve el informe guardado y NO vuelve a llamar al generador."""
    llamadas = []
    original = main.generate_report_narrative

    async def contando(summary):
        llamadas.append(1)
        return await original(summary)

    monkeypatch.setattr(main, "generate_report_narrative", contando)
    res = client.get(f"/api/analysis/{aid}/report")
    assert res.status_code == 200
    assert res.json()["generated_at"] == analysis_persist.cargar_informe(aid)["generated_at"]
    assert llamadas == [], "GET no debe regenerar el informe"


def test_post_report_cacheado_no_regenera(client, aid, monkeypatch):
    """POST sin regenerate devuelve el cacheado: cero llamadas a la IA."""
    llamadas = []
    original = main.generate_report_narrative

    async def contando(summary):
        llamadas.append(1)
        return await original(summary)

    monkeypatch.setattr(main, "generate_report_narrative", contando)
    res = client.post(f"/api/analysis/{aid}/report")
    assert res.status_code == 200
    assert llamadas == [], "POST sin regenerate no debe volver a generar"


def test_post_report_regenerate_fuerza_nueva_version(client, aid, monkeypatch):
    """`?regenerate=true` sí invoca al generador y sobreescribe el cache."""
    llamadas = []
    original = main.generate_report_narrative

    async def contando(summary):
        llamadas.append(1)
        return await original(summary)

    monkeypatch.setattr(main, "generate_report_narrative", contando)
    res = client.post(f"/api/analysis/{aid}/report", params={"regenerate": "true"})
    assert res.status_code == 200
    assert llamadas == [1], "regenerate debe generar exactamente una vez más"


def test_get_report_sin_informe_devuelve_404(client):
    """Un análisis sin informe no tiene nada cacheado: 404 (no 405)."""
    aid_nuevo = _subir(client, "sin_informe.xlsx")
    try:
        res = client.get(f"/api/analysis/{aid_nuevo}/report")
        assert res.status_code == 404
    finally:
        main.analysis_store.pop(aid_nuevo, None)
        analysis_persist.borrar(aid_nuevo)


# ---------------------------------------------------------------------------
# 3. Rehidratación tras un reinicio simulado
# ---------------------------------------------------------------------------

def test_rehidrata_desde_disco_con_dataframe_informe_y_chat(client, aid):
    """Vaciar el store («reinicio») y rehidratar restaura todo lo persistido."""
    main.analysis_store[aid]["chat_history"] = [
        {"question": "¿cuántos fallos hay?", "answer": "12 fallos (4625)."}
    ]
    analysis_persist.guardar_estado(aid, main.analysis_store[aid])

    backup = dict(analysis_store)
    analysis_store.clear()
    try:
        main.rehidratar_desde_disco()

        entry = analysis_store[aid]
        assert entry["status"] == "done"
        assert entry["summary"]["rows"] == 12
        assert entry["dataframe"] is not None and len(entry["dataframe"]) == 12
        # Columnas derivadas reconstruidas al reler el Excel (enriquecimiento
        # único de ingesta): sin ellas /rows y /summary no responden igual.
        assert "_level" in entry["dataframe"].columns
        assert "_suspicious" in entry["dataframe"].columns
        assert entry["report"] is not None
        assert entry["report"]["generated_at"]
        assert entry["chat_history"][0]["question"] == "¿cuántos fallos hay?"
        # Y la API responde con el análisis restaurado, no 404.
        res = client.get(f"/api/analysis/{aid}")
        assert res.status_code == 200
        assert res.json()["status"] == "done"
    finally:
        analysis_store.clear()
        analysis_store.update(backup)


def test_rehidratacion_con_excel_corrupto_marca_error_y_no_tumba_el_resto(aid):
    """Un Excel ilegible al arrancar marca error en ESE análisis; el resto carga."""
    import json

    directorio = analysis_persist.dir_analisis(aid)
    (directorio / "archivo.xlsx").write_bytes(b"no soy un excel")
    # summary.json con fecha futura para que la retención no lo borre antes.
    (directorio / "summary.json").write_text(
        json.dumps(
            {
                "status": "done",
                "error": None,
                "summary": {"rows": 12, "file_name": "x.xlsx"},
                "chat_history": [],
                "rag": None,
            }
        ),
        encoding="utf-8",
    )

    backup = dict(analysis_store)
    analysis_store.clear()
    try:
        main.rehidratar_desde_disco()
        entry = analysis_store[aid]
        assert entry["status"] == "error"
        assert entry["error"]
        # El informe cacheado sigue siendo servible aunque el df no cargue.
        assert entry.get("report") is not None
    finally:
        analysis_store.clear()
        analysis_store.update(backup)
        # Reparar el archivo para el resto de la suite.
        from tests.conftest import xlsx_bytes

        buffer = xlsx_bytes()
        (directorio / "archivo.xlsx").write_bytes(buffer.getvalue())


# ---------------------------------------------------------------------------
# 4. Borrado manual desde el frontend
# ---------------------------------------------------------------------------

def test_delete_analysis_quita_store_disco_e_indice(client):
    aid_borrar = _subir(client, "para_borrar.xlsx")
    client.post(f"/api/analysis/{aid_borrar}/report")
    assert analysis_persist.dir_analisis(aid_borrar).exists()

    res = client.delete(f"/api/analyses/{aid_borrar}")
    assert res.status_code == 200, res.text

    assert aid_borrar not in analysis_store
    assert not analysis_persist.dir_analisis(aid_borrar).exists()
    assert client.get(f"/api/analysis/{aid_borrar}").status_code == 404
    ids = [item["analysis_id"] for item in client.get("/api/analyses").json()]
    assert aid_borrar not in ids


def test_delete_analysis_desconocido_devuelve_404(client):
    res = client.delete("/api/analyses/no-existe-123")
    assert res.status_code == 404


def test_borrado_durante_indexacion_no_revienta(rag_aislado, ai_config_aislado, monkeypatch):
    """Borrar un análisis mientras process_analysis aún lo procesa es seguro.

    El botón BORRAR se puede pulsar en plena indexación RAG: el DELETE saca la
    entrada del store y borra la carpeta, y la tarea de fondo sigue escribiendo
    después. Sin más, eso estallaba con `KeyError` en la rama RAG (observado
    en vivo) y, si no, resucitaba la carpeta al volver a persistir.
    """
    import asyncio

    from tests.conftest import xlsx_bytes

    aid = "id-borrado-en-caliente"
    contenido = xlsx_bytes().getvalue()
    analysis_persist.guardar_excel(aid, contenido)
    analysis_store[aid] = {
        "analysis_id": aid,
        "status": "pending",
        "summary": None,
        "error": None,
    }

    def borrando_mientras_indexa(_aid, _records):
        # El analista pulsa BORRAR en el panel durante la indexación.
        analysis_store.pop(aid, None)
        analysis_persist.borrar(aid)
        return {"available": True, "indexed": 12, "total": 12, "reason": ""}

    monkeypatch.setattr(main.rag_store, "index_events", borrando_mientras_indexa)

    try:
        asyncio.run(main.process_analysis(aid, contenido, "borrado_en_caliente.xlsx"))
    finally:
        analysis_store.pop(aid, None)

    assert aid not in analysis_store
    assert not analysis_persist.dir_analisis(aid).exists(), (
        "no debe resucitar en disco la carpeta que el DELETE acaba de borrar"
    )


# ---------------------------------------------------------------------------
# 5. Retención configurable (Opciones IA, default 30 días)
# ---------------------------------------------------------------------------

def test_ai_config_retention_days_default_30(ai):
    assert ai.get()["retention_days"] == 30


def test_ai_config_retention_days_valida_entero_no_negativo(ai):
    ai.update({"retention_days": 7})
    assert ai.get()["retention_days"] == 7
    with pytest.raises(ValueError):
        ai.update({"retention_days": -1})
    with pytest.raises(ValueError):
        ai.update({"retention_days": "muchos"})


def test_aplicar_retencion_borra_solo_directorios_viejos():
    """Dirs con mtime de hace 60 días se borran; los recientes quedan."""
    raiz = analysis_persist.ANALYSES_DIR
    viejo = raiz / "id-viejo-para-retencion"
    reciente = analysis_persist.dir_analisis("id-reciente-para-retencion")
    for directorio in (viejo, reciente):
        directorio.mkdir(parents=True, exist_ok=True)
        (directorio / "archivo.xlsx").write_bytes(b"x")
    viejo_mtimes = time.time() - 60 * 24 * 3600
    os.utime(viejo, (viejo_mtimes, viejo_mtimes))
    try:
        borrados = analysis_persist.aplicar_retencion(30)
        assert "id-viejo-para-retencion" in borrados
        assert not viejo.exists()
        assert reciente.exists()
        assert "id-reciente-para-retencion" not in borrados
    finally:
        for directorio in (viejo, reciente):
            if directorio.exists():
                for archivo in directorio.iterdir():
                    archivo.unlink()
                directorio.rmdir()


def test_aplicar_retencion_cero_nunca_borra():
    """`retention_days = 0` = conservar para siempre."""
    raiz = analysis_persist.ANALYSES_DIR
    viejo = raiz / "id-viejo-para-siempre"
    viejo.mkdir(parents=True, exist_ok=True)
    (viejo / "archivo.xlsx").write_bytes(b"x")
    viejo_mtimes = time.time() - 400 * 24 * 3600
    os.utime(viejo, (viejo_mtimes, viejo_mtimes))
    try:
        borrados = analysis_persist.aplicar_retencion(0)
        assert viejo.exists()
        assert "id-viejo-para-siempre" not in borrados
    finally:
        for archivo in viejo.iterdir():
            archivo.unlink()
        viejo.rmdir()


# ---------------------------------------------------------------------------
# 6. Historial de chat persistente
# ---------------------------------------------------------------------------

def test_remember_turn_persiste_el_historial_en_disco(aid):
    entry = main.analysis_store[aid]
    entry["chat_history"] = []
    main._remember_turn(entry, "¿qué hosts concentran eventos?", "DC-01 y FS-SRV01.")
    data = analysis_persist.cargar_estado(aid)
    assert data is not None
    assert data["chat_history"][-1]["question"] == "¿qué hosts concentran eventos?"


def test_borrar_chat_persiste_el_vaciado_en_disco(client, aid):
    main.analysis_store[aid]["chat_history"] = [
        {"question": "x", "answer": "y"}
    ]
    res = client.delete(f"/api/analysis/{aid}/chat")
    assert res.status_code == 200, res.text
    data = analysis_persist.cargar_estado(aid)
    assert data["chat_history"] == []
