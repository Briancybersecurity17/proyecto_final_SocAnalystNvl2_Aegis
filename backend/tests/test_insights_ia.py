"""Tarea: observaciones del análisis redactadas por IA (grounded).

Mismo contrato que el informe, aplicado a `ai_insights` del dashboard: el
modelo REDACTA y el sistema VALIDA cada observación contra el ctx (anclas +
atribución + redundancia); lo que trae una cifra sin ancla se descarta ENTERA.
Si el LLM está apagado, no responde, devuelve JSON inválido o lanza excepción,
se usan las locales deterministas: el dashboard nunca se queda sin
observaciones.

Cubre además el cableado de `main.py`:
- `process_analysis` genera las de IA ANTES de marcar «done» y las cachea;
- `GET /summary` sirve esa caché cuando los filtros no han movido las filas y
  recalcula en LOCAL cuando sí los han movido (una llamada al modelo por cada
  movimiento de filtro sería lenta y describiría datos de otra selección).
"""

import asyncio
import json

import pytest

import ai_service
from ai_service import (
    _filter_insights,
    _INSIGHTS_SYSTEM_PROMPT,
    MAX_INSIGHTS,
    build_context,
    generate_data_insights,
    generate_local_insights,
)
from tests.conftest import COLUMNAS, DATASET_12_FILAS

import pandas as pd

# Resumen REAL del dataset sintético de 12 filas (call_ai_service es pura y
# calcula _level él mismo): las cifras de abajo salen de ahí, no de una mano.
# Los tests de unidad lo reciben por el fixture `summary`.


@pytest.fixture(scope="module")
def summary() -> dict:
    from main import call_ai_service

    return call_ai_service(pd.DataFrame(DATASET_12_FILAS, columns=COLUMNAS))


# Observación de IA cuyas DOS cifras están ancladas al ctx sintético:
# 12 = filas del archivo y 4 = hosts distintos (DC-01, WS-018, WEB-01,
# FS-SRV01), ambos presentes en el summary calculado.
VALIDA = "Se analizaron 12 registros de seguridad en 4 hosts distintos."

# …y una con una cifra que no existe en ningún sitio del ctx: la regla A
# debe tirar la frase entera sin tocar la válida de al lado.
INVENTADA = "Además, 9999 hosts distintos presentan actividad inusual."


@pytest.fixture
def ia(tmp_path, monkeypatch):
    """Config aislada en tmp con el LLM encendido (para que intente el remoto)."""
    cfg = ai_service.ai_config
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "ai_config.json")
    cfg.update({"enabled": True,
                "api_url": "https://api.test/openai/v1/chat/completions"})
    return cfg


@pytest.fixture
def chat_falso(ia, monkeypatch):
    """Falsifica `ai_config.chat` con la respuesta que el test vaya a necesitar.

    Devuelve una lista `capturas` con el messages de cada llamada, para poder
    assertar qué se le envió al modelo.
    """
    capturas: list = []

    def instalar(respuesta):
        async def chat(messages, temperature=None, cfg=None):
            capturas.append(messages)
            if isinstance(respuesta, Exception):
                raise respuesta
            return respuesta

        monkeypatch.setattr(ia, "chat", chat)
        return capturas

    return instalar


def _con_insights(*texts: str) -> str:
    return json.dumps({"insights": list(texts)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# LLM apagado / caído → observaciones locales
# ---------------------------------------------------------------------------
def test_llm_apagado_usa_las_observaciones_locales(tmp_path, monkeypatch, summary):
    cfg = ai_service.ai_config
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "ai_config.json")
    cfg.update({"enabled": False})

    res = asyncio.run(generate_data_insights(summary))

    assert res == generate_local_insights(summary)
    assert res and res[0].startswith("Distribución de criticidad")


def test_respuesta_no_json_usa_las_locales(ia, chat_falso, summary):
    for respuesta in ("esto no es JSON", '{"notas": ["x"]}', None):
        chat_falso(respuesta)
        res = asyncio.run(generate_data_insights(summary))
        assert res == generate_local_insights(summary), f"respuesta: {respuesta!r}"


def test_excepcion_del_proveedor_usa_las_locales(ia, chat_falso, summary):
    """Un 500/timeout del proveedor no rompe el dashboard (fallback)."""
    chat_falso(RuntimeError("proveedor caído"))

    res = asyncio.run(generate_data_insights(summary))

    assert res == generate_local_insights(summary)


# ---------------------------------------------------------------------------
# Camino de IA: solo sobrevive lo anclado
# ---------------------------------------------------------------------------
def test_observaciones_de_ia_ancladas_sustituyen_a_las_locales(
    ia, chat_falso, summary
):
    capturas = chat_falso(_con_insights(VALIDA))

    res = asyncio.run(generate_data_insights(summary))

    assert res == [VALIDA]
    assert res != generate_local_insights(summary)   # NO es el fallback

    # Lo que viaja al modelo: system de grounding estricto + el ctx completo.
    mensajes = capturas[0]
    assert mensajes[0]["content"] == _INSIGHTS_SYSTEM_PROMPT
    user = mensajes[-1]["content"]
    assert '"insights"' in user                    # la forma de la respuesta
    assert '"level_counts"' in user                # el DATOS calculado
    assert "Todo número sale de DATOS" in user     # la regla dura del prompt


def test_cifra_inventada_tira_solo_su_observacion(ia, chat_falso, summary):
    chat_falso(_con_insights(VALIDA, INVENTADA))

    res = asyncio.run(generate_data_insights(summary))

    assert res == [VALIDA]
    assert "9999" not in " ".join(res)


def test_json_con_clave_equivocada_usa_las_locales(ia, chat_falso, summary):
    """JSON válido pero sin «insights»: no hay nada verificable que servir."""
    chat_falso(json.dumps({"observations": [VALIDA]}))

    res = asyncio.run(generate_data_insights(summary))

    assert res == generate_local_insights(summary)


def test_archivo_vacio_no_llama_al_modelo(ia, chat_falso):
    """rows=0 → respuesta local directa: ni prompt ni llamada al proveedor."""
    capturas = chat_falso(_con_insights(VALIDA))

    res = asyncio.run(generate_data_insights({"rows": 0}))

    assert res == ["El archivo no contiene registros para analizar."]
    assert capturas == []


# ---------------------------------------------------------------------------
# `_filter_insights`: las reglas puras, una a una
# ---------------------------------------------------------------------------
def test_filter_descarta_las_demasiado_cortas(summary):
    ctx = build_context(summary)
    larga = "Los registros concentrados en DC-01 justifican su revisión previa."

    assert _filter_insights([larga, "Muy corta."], ctx) == [larga]


def test_filter_no_acumula_duplicados(summary):
    ctx = build_context(summary)
    a = "Se analizaron 12 registros de seguridad en 4 hosts distintos."
    b = "SE ANALIZARON 12 REGISTROS DE SEGURIDAD EN 4 HOSTS DISTINTOS."

    assert _filter_insights([a, b], ctx) == [a]


def test_filter_respeta_el_tope_de_observaciones(summary):
    ctx = build_context(summary)
    candidatas = [
        f"Variante número {i} sobre la distribución de los 12 registros."
        for i in range(MAX_INSIGHTS + 2)
    ]

    res = _filter_insights(candidatas, ctx)

    assert len(res) == MAX_INSIGHTS
    # Las que sobreviven siguen siendo verificables (cifra anclada: 12)
    assert all("12" in texto for texto in res)


def test_filter_descarta_la_cifra_sin_ancla(summary):
    ctx = build_context(summary)
    assert _filter_insights([INVENTADA], ctx) == []


# ---------------------------------------------------------------------------
# Cableado en main.py: caché de IA vs. recálculo local con filtros
# ---------------------------------------------------------------------------
def test_summary_sirve_la_cache_de_ia_y_recalcula_con_filtros(
    client, analysis_id
):
    """Sin filtros: la caché de IA (generada una vez al terminar el análisis).
    Con filtros: recálculo LOCAL sobre la selección — si se sirviera la caché,
    las observaciones describirían las filas completas mientras la UI muestra
    otra cosa; y una llamada al modelo por movimiento de filtro sería lenta.
    """
    from main import analysis_store

    cached = analysis_store[analysis_id]["summary"].get("ai_insights")
    assert cached, "process_analysis debe generar ai_insights antes de «done»"

    guardado = list(cached)
    try:
        analysis_store[analysis_id]["summary"]["ai_insights"] = [
            "Observación de IA cacheada."
        ]

        sin_filtros = client.get(f"/api/analysis/{analysis_id}/summary").json()
        assert sin_filtros["ai_insights"] == ["Observación de IA cacheada."]

        con_filtro = client.get(
            f"/api/analysis/{analysis_id}/summary", params={"level": "critico"}
        ).json()
        assert con_filtro["rows"] < con_filtro["total"]
        assert con_filtro["ai_insights"] != ["Observación de IA cacheada."]
        assert con_filtro["ai_insights"], "con filtros nunca se queda vacío"
    finally:
        analysis_store[analysis_id]["summary"]["ai_insights"] = guardado


def test_dashboard_expone_observaciones_no_vacias(client, analysis_id):
    """Contrato del endpoint que consume el panel: siempre una lista con texto."""
    res = client.get(f"/api/analysis/{analysis_id}/summary").json()

    assert isinstance(res["ai_insights"], list)
    assert res["ai_insights"] and all(
        isinstance(item, str) and item.strip() for item in res["ai_insights"]
    )
