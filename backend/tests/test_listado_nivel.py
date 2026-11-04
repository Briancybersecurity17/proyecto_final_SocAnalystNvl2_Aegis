"""Ruta de LISTADO por nivel de criticidad en el asistente (pieza 1).

Por qué existe: el RAG generaliza desde 8 filas recuperadas y respondió
«no existen eventos críticos» cuando el dataset tenía 97. Quien pide
«dame los eventos de nivel crítico» quiere los Log_ID reales de ESE nivel,
contados sobre TODO el dataframe, nunca una estimación de similitud.

Contrato cubierto aquí:

1. Activación: mención de un nivel concreto (critico/alto/medio/bajo) +
   intención de listado («dame», «muestrame», «lista», «cuáles son»…).
2. Respuesta determinista: conteo EXACTO del dataframe + top-20 Log_IDs
   (MAX_LOG_IDS de attack_cards) + nota de truncado, `retrieved = 0`.
3. «No hay eventos de nivel X» SOLO si el conteo real es 0.
4. Si la consulta nombra otras entidades filtrables (host, usuario, IP,
   Event ID del dataset) la ruta NO se activa: ignorar ese filtro sería
   mentir. Tampoco sin dataframe (llamadas directas legadas).
5. Prioridad: DESPUÉS de los agregados («¿qué hosts concentran los
   eventos críticos?» sigue en `mode = aggregate`) y ANTES de la puerta
   de tema; los endpoints cortan antes de la recuperación (retrieved = 0).
"""

import asyncio
import json
import re

import pandas as pd
import pytest
from sse_starlette.sse import AppStatus

from ai_service import answer_question, routes_list_level
from main import ai_config


# ---------------------------------------------------------------------------
# Fixtures (mismos patrones que test_ruta_ataques)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_sse_exit_event():
    """AppStatus de sse-starlette a nivel de módulo: TestClient usa un loop
    por petición, así que sin reset la segunda petición SSE revienta."""
    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None


@pytest.fixture
def ai(tmp_path, monkeypatch):
    """Config de IA aislada en tmp con el LLM apagado por defecto."""
    monkeypatch.setattr(ai_config, "CONFIG_PATH", tmp_path / "ai_config.json")
    ai_config.update({"enabled": False})
    return ai_config


def _eventos_sse(texto: str):
    """[(evento, payload)] de un cuerpo SSE (mismo helper que test_chat_stream)."""
    eventos = []
    for bloque in texto.replace("\r\n", "\n").split("\n\n"):
        nombre, datos = None, None
        for linea in bloque.splitlines():
            if linea.startswith("event: "):
                nombre = linea[len("event: "):].strip()
            elif linea.startswith("data: "):
                datos = json.loads(linea[len("data: "):])
        if nombre and datos is not None:
            eventos.append((nombre, datos))
    return eventos


# ---------------------------------------------------------------------------
# Datasets de prueba (con las columnas derivadas que el backend crea al
# ingerir: `_level` y `_suspicious`, igual que `_enrichir_df`)
# ---------------------------------------------------------------------------
def _df_niveles(filas):
    """filas: [(Log_ID, Hostname, _level)] → dataframe listo para la ruta.

    Lleva además las columnas de entidad reales (User/Source_IP/Event_ID)
    para que la guarda de «¿ignoraría un filtro escrito?» pueda evaluarlas.
    """
    return pd.DataFrame(
        [
            {"Log_ID": lid, "Hostname": host, "User": "jsmith",
             "Source_IP": "10.10.2.15", "Event_ID": 4624,
             "_level": lvl, "_suspicious": False}
            for lid, host, lvl in filas
        ]
    )


def _resumen(df):
    """Resumen mínimo como el que arma el backend (`_ask_prep`) para los
    agregados: `dataset_rows` + `level_counts` calculados del MISMO dataframe."""
    counts = df["_level"].value_counts()
    return {
        "rows": len(df),
        "level_counts": {lvl: int(n) for lvl, n in counts.items()},
    }


def _df_criticos(total=6, criticos=2):
    """`total` filas: las primeras `criticos` de nivel crítico (L001, L002…)."""
    filas = [
        (f"L{i + 1:03d}", "DC-01", "critico") for i in range(criticos)
    ] + [
        (f"B{i + 1:03d}", "WS-01", "bajo") for i in range(total - criticos)
    ]
    return _df_niveles(filas)


# ---------------------------------------------------------------------------
# 1. Activación y respuesta exacta
# ---------------------------------------------------------------------------
def test_dame_eventos_criticos_listado_exacto(ai):
    """Conteo exacto del dataframe + sus Log_ID, en modo nuevo y sin RAG."""
    res = asyncio.run(
        answer_question("Dame los eventos de nivel crítico", [], None,
                        df=_df_criticos())
    )
    assert res["mode"] == "level_list"
    assert res["retrieved"] == 0
    assert res["evidence"] == []
    assert res["warnings"] == []
    assert res["reference"]["available"] is False
    assert res["engine"] == "deterministic"
    # Las cifras salen del dataframe: 2 críticos de 6, con sus Log_ID.
    assert "2" in res["answer"] and "Crítico" in res["answer"]
    assert "L001" in res["answer"] and "L002" in res["answer"]
    assert "B001" not in res["answer"]
    assert "log_id" in (res.get("summary_line") or "").lower() or "listado" in (
        res.get("summary_line") or ""
    ).lower()


def test_dame_eventos_criticos_cero_reales(ai):
    """«No hay» SOLO cuando el conteo real del dataframe es 0."""
    df = _df_niveles([("B001", "WS-01", "bajo"), ("B002", "WS-01", "bajo")])
    res = asyncio.run(answer_question("Dame los eventos críticos", [], None, df=df))
    assert res["mode"] == "level_list"
    assert res["retrieved"] == 0
    assert "no hay" in res["answer"].lower()
    assert "crítico" in res["answer"].lower()
    assert "2" in res["answer"]  # las 2 filas reales del archivo


def test_listado_menciona_el_nivel_pedido(ai):
    """Todos los niveles citados se cuentan («críticos y altos»)."""
    df = _df_niveles([
        ("L001", "DC-01", "critico"),
        ("A001", "DC-01", "alto"),
        ("B001", "WS-01", "bajo"),
    ])
    res = asyncio.run(
        answer_question("Dame los eventos críticos y altos", [], None, df=df)
    )
    assert res["mode"] == "level_list"
    assert "L001" in res["answer"] and "A001" in res["answer"]
    assert "B001" not in res["answer"]


# ---------------------------------------------------------------------------
# 2. Truncado a top-20 Log_IDs (MAX_LOG_IDS de attack_cards)
# ---------------------------------------------------------------------------
def test_top_20_log_ids_con_nota_de_truncado(ai):
    df = _df_niveles(
        [(f"L{i + 1:03d}", "DC-01", "critico") for i in range(25)]
    )
    res = asyncio.run(answer_question("Dame los eventos críticos", [], None, df=df))
    assert res["mode"] == "level_list"
    ids = re.findall(r"L\d{3}", res["answer"])
    assert len(ids) == 20, f"deben viajar 20 Log_ID como máximo: {ids}"
    assert "L021" not in res["answer"]
    assert "20 de 25" in res["answer"]  # nota de truncado visible


# ---------------------------------------------------------------------------
# 3. Guardas: no se activa cuando ignoraría un filtro o no hay dataframe
# ---------------------------------------------------------------------------
def test_conserva_el_filtro_de_entidad_escrito(ai):
    """«…del host DC-01»: el listado por nivel ignoraría ese host → no activa."""
    df = _df_niveles([
        ("L001", "DC-01", "critico"),
        ("L002", "HOST-2", "critico"),
        ("B001", "HOST-2", "bajo"),
    ])
    assert not routes_list_level("Dame los eventos críticos del host DC-01", df)
    res = asyncio.run(
        answer_question("Dame los eventos críticos del host DC-01", [], None, df=df)
    )
    assert res["mode"] != "level_list"


def test_conserva_el_filtro_por_ip_o_usuario(ai):
    """Una IP o un usuario en la consulta también corta la ruta."""
    df = _df_criticos()
    assert not routes_list_level("Dame los eventos críticos de 10.10.2.15", df)
    assert not routes_list_level("Dame los eventos críticos de jsmith", df)


def test_sin_dataframe_no_activa():
    """Llamadas directas legadas (df=None): el flujo normal sigue intacto."""
    assert not routes_list_level("Dame los eventos de nivel crítico", None)
    res = asyncio.run(answer_question("Dame los eventos de nivel crítico", [], None))
    assert res["mode"] != "level_list"


def test_pregunta_de_distribucion_sigue_en_aggregate(ai):
    """«¿qué hosts concentran…?» es un REPARTO, no un listado de IDs."""
    df = _df_criticos()
    assert not routes_list_level("¿Qué hosts concentran los eventos críticos?", df)
    res = asyncio.run(
        answer_question("¿Qué hosts concentran los eventos críticos?", [],
                        _resumen(df), df=df)
    )
    assert res["mode"] == "aggregate"


def test_conteo_sigue_en_aggregate(ai):
    """«¿cuántos…?» pide un número, no una lista de Log_ID."""
    df = _df_criticos()
    assert not routes_list_level("¿Cuántos eventos críticos hay?", df)
    res = asyncio.run(
        answer_question("¿Cuántos eventos críticos hay?", [], _resumen(df), df=df)
    )
    assert res["mode"] == "aggregate"


# ---------------------------------------------------------------------------
# 4. Endpoints: corte ANTES de la recuperación (retrieved = 0)
# ---------------------------------------------------------------------------
def test_endpoint_ask_listado_sin_recuperacion(client, analysis_id, ai):
    """El dataset sintético tiene 2 críticos (Warning→crítico + Critical)."""
    res = client.post(
        f"/api/analysis/{analysis_id}/ask",
        json={"question": "Dame los eventos de nivel crítico"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "level_list"
    assert body["retrieved"] == 0
    assert body["warnings"] == []
    assert body["reference"]["available"] is False
    assert "crítico" in body["answer"].lower()
    assert "2" in body["answer"]


def test_endpoint_ask_stream_listado_sin_recuperacion(client, analysis_id, ai):
    res = client.post(
        f"/api/analysis/{analysis_id}/ask/stream",
        json={"question": "Dame los eventos de nivel crítico"},
    )
    assert res.status_code == 200, res.text
    eventos = _eventos_sse(res.text)
    finales = [datos for nombre, datos in eventos if nombre == "final"]
    assert finales, f"sin evento final: {eventos}"
    body = finales[-1]
    assert body["mode"] == "level_list"
    assert body["retrieved"] == 0
    assert body["reference"]["available"] is False
    # Sin recuperación no hay fase «meta» con tarjetas: la ruta corta antes.
    assert not any(nombre == "meta" for nombre, _ in eventos)
