"""Asistente conversacional: memoria multi-turno + streaming SSE.

Cubre los endpoints nuevos de la Fase 2:

- POST /api/analysis/{id}/ask/stream (SSE: meta → token* → final | fallback)
- DELETE /api/analysis/{id}/chat (borra la memoria de la conversación)
- La memoria multi-turno que ahora comparte /ask y el stream.

Los tests aíslan `ai_config.CONFIG_PATH` en un tmp y APAGAN el LLM: así el
camino determinista (el 100 % del contrato SSE sin modelo) se prueba siempre
igual, sin depender de un proveedor real. El camino con modelo se
prueba con `chat_stream` falsificado: tokens reales de verdad y, sobre todo,
que UN Log_ID inventado invalida el texto transmitido entero.
"""

import asyncio
import json

import pytest
from sse_starlette.sse import AppStatus

from main import (
    CHAT_HISTORY_TURNS,
    QA_HISTORY_ANSWER_CHARS,
    _chat_history,
    _remember_turn,
    ai_config,
    analysis_store,
    app,
    stream_question,
)


@pytest.fixture(autouse=True)
def _reset_sse_exit_event():
    """sse-starlette guarda `AppStatus.should_exit_event` (un anyio.Event) a
    nivel de módulo y lo crea en el loop de la PRIMERA petición SSE. TestClient
    usa un loop nuevo por petición, así que la segunda llamada SSE fallaría con
    «bound to a different event loop». Resetearlo antes de cada test es el
    workaround oficial: en producción uvicorn reutiliza el loop, así que allí
    no hace falta (el endpoint /events existente lo demuestra)."""
    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None


@pytest.fixture
def ai(tmp_path, monkeypatch):
    """Configuración aislada en tmp con el LLM apagado (contrato determinista)."""
    monkeypatch.setattr(ai_config, "CONFIG_PATH", tmp_path / "ai_config.json")
    ai_config.update({"enabled": False})
    return ai_config


def _eventos_sse(texto: str):
    """Extrae [(evento, payload)] del cuerpo SSE devuelto por TestClient.

    sse-starlette separa los campos con CRLF (especificación SSE), así que se
    normaliza a LF antes de trocear por bloques en blanco.
    """
    eventos = []
    for bloque in texto.replace("\r\n", "\n").split("\n\n"):
        nombre = None
        datos = None
        for linea in bloque.splitlines():
            if linea.startswith("event: "):
                nombre = linea[len("event: "):].strip()
            elif linea.startswith("data: "):
                datos = json.loads(linea[len("data: "):])
        if nombre and datos is not None:
            eventos.append((nombre, datos))
    return eventos


# ---------------------------------------------------------------------------
# Memoria multi-turno (compartida por /ask y el stream)
# ---------------------------------------------------------------------------


def test_ask_guarda_el_turno_en_la_memoria(client, analysis_id, ai):
    res = client.post(
        f"/api/analysis/{analysis_id}/ask",
        json={"question": "¿Qué pasó en el host DC-01?"},
    )
    assert res.status_code == 200

    historia = _chat_history(analysis_store[analysis_id])
    assert len(historia) == 1
    assert historia[0]["question"] == "¿Qué pasó en el host DC-01?"
    # La respuesta guardada es la que se devolvió al analista, recortada.
    assert historia[0]["answer"] == res.json()["answer"][:QA_HISTORY_ANSWER_CHARS]


def test_ask_agregados_tambien_guardan_turno(client, analysis_id, ai):
    """Las preguntas de conteos también cuentan para el hilo de conversación."""
    client.delete(f"/api/analysis/{analysis_id}/chat")
    res = client.post(
        f"/api/analysis/{analysis_id}/ask",
        json={"question": "¿Cuántos eventos hay en total?"},
    )
    assert res.status_code == 200
    assert res.json()["mode"] == "aggregate"

    historia = _chat_history(analysis_store[analysis_id])
    assert [t["question"] for t in historia] == ["¿Cuántos eventos hay en total?"]


def test_memoria_acotada_a_los_ultimos_turnos(client, analysis_id, ai):
    """La memoria guarda solo CHAT_HISTORY_TURNS turnos: la ventana es finita."""
    entry = analysis_store[analysis_id]
    # Se siembran CHAT_HISTORY_TURNS + 2 = 8 turnos (índices 0..7).
    sembrados = CHAT_HISTORY_TURNS + 2
    entry["chat_history"] = [
        {"question": f"pregunta {i}", "answer": f"respuesta {i}"}
        for i in range(sembrados)
    ]
    client.post(
        f"/api/analysis/{analysis_id}/ask",
        json={"question": "¿Qué pasó en el host WS-018?"},
    )
    historia = _chat_history(entry)
    assert len(historia) == CHAT_HISTORY_TURNS
    # El turno nuevo es el último; los dos más antiguos cayeron por la cola.
    # Con 8 sembrados + 1 nuevo = 9, la ventana de 6 conserva «pregunta 3..7».
    assert historia[-1]["question"] == "¿Qué pasó en el host WS-018?"
    assert historia[0]["question"] == f"pregunta {sembrados - (CHAT_HISTORY_TURNS - 1)}"


def test_respuesta_larga_se_acorta_antes_de_guardarse():
    entry = {"chat_history": []}
    _remember_turn(entry, "pregunta", "x" * (QA_HISTORY_ANSWER_CHARS + 500))
    assert len(entry["chat_history"][0]["answer"]) == QA_HISTORY_ANSWER_CHARS


def test_respuesta_vacia_no_ocupa_memoria():
    """Una pregunta sin respuesta (error a mitad) no ensucia la conversación."""
    entry = {"chat_history": []}
    _remember_turn(entry, "pregunta", "")
    assert _chat_history(entry) == []


def test_delete_chat_borra_la_memoria(client, analysis_id, ai):
    client.post(
        f"/api/analysis/{analysis_id}/ask",
        json={"question": "¿Qué pasó en el host DC-01?"},
    )
    assert _chat_history(analysis_store[analysis_id])

    res = client.delete(f"/api/analysis/{analysis_id}/chat")
    assert res.status_code == 200
    assert _chat_history(analysis_store[analysis_id]) == []
    # Borrar dos veces es idempotente (el botón puede pulsarse dos veces).
    assert client.delete(f"/api/analysis/{analysis_id}/chat").status_code == 200


def test_delete_chat_analisis_desconocido_devuelve_404(client):
    assert client.delete("/api/analysis/no-existe/chat").status_code == 404


def test_ask_y_stream_comparten_la_memoria(client, analysis_id, ai):
    """Preguntar por /ask y luego por el stream ve el mismo hilo conversacional."""
    client.delete(f"/api/analysis/{analysis_id}/chat")
    client.post(
        f"/api/analysis/{analysis_id}/ask",
        json={"question": "¿Qué pasó en el host DC-01?"},
    )
    res = client.post(
        f"/api/analysis/{analysis_id}/ask/stream",
        json={"question": "¿Y esos mismos con IP externa?"},
    )
    assert res.status_code == 200

    historia = _chat_history(analysis_store[analysis_id])
    assert [t["question"] for t in historia] == [
        "¿Qué pasó en el host DC-01?",
        "¿Y esos mismos con IP externa?",
    ]


# ---------------------------------------------------------------------------
# Endpoint SSE /ask/stream (camino determinista, LLM apagado)
# ---------------------------------------------------------------------------


def test_stream_meta_antes_final_y_sin_tokens(client, analysis_id, ai):
    """Sin LLM el flujo es meta → final: evidencias ya, respuesta sin tokens."""
    client.delete(f"/api/analysis/{analysis_id}/chat")
    res = client.post(
        f"/api/analysis/{analysis_id}/ask/stream",
        json={"question": "¿Qué pasó en el host DC-01?"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    eventos = _eventos_sse(res.text)
    tipos = [nombre for nombre, _ in eventos]
    assert tipos == ["meta", "final"], f"flujo SSE inesperado: {tipos}"

    meta = eventos[0][1]
    assert meta["kind"] == "meta"
    assert meta["engine"] == "streaming"
    assert meta["analysis_id"] == analysis_id
    assert meta["question"] == "¿Qué pasó en el host DC-01?"
    assert isinstance(meta["evidence"], list) and meta["evidence"]
    assert isinstance(meta["retrieved"], int)
    # El envoltorio común llega con el meta: la UI pinta cabeceras ya.
    assert set(meta) >= {"filters", "rag", "kb", "summary_line", "confidence"}

    final = eventos[-1][1]
    assert final["kind"] == "final"
    assert final["answer"]
    assert final["engine"] == "deterministic"
    assert final["mode"] == "evidence"
    assert final["retrieved"] == meta["retrieved"]


def test_stream_agregado_emite_un_solo_final(client, analysis_id, ai):
    """Los conteos se contestan de una vez (sin streaming ni evidencia fila a fila)."""
    res = client.post(
        f"/api/analysis/{analysis_id}/ask/stream",
        json={"question": "¿Cómo se distribuyen los eventos por nivel?"},
    )
    assert res.status_code == 200
    eventos = _eventos_sse(res.text)
    assert [nombre for nombre, _ in eventos] == ["final"]
    final = eventos[0][1]
    assert final["mode"] == "aggregate"
    assert final["engine"] == "deterministic"
    assert "kind" in final and final["kind"] == "final"


def test_stream_pregunta_vacia_devuelve_400(client, analysis_id, ai):
    res = client.post(
        f"/api/analysis/{analysis_id}/ask/stream", json={"question": " x "}
    )
    assert res.status_code == 400


def test_stream_analisis_desconocido_devuelve_404(client, ai):
    res = client.post(
        "/api/analysis/no-existe/ask/stream", json={"question": "¿Qué pasó?"}
    )
    assert res.status_code == 404


def test_stream_final_guarda_el_turno(client, analysis_id, ai):
    client.delete(f"/api/analysis/{analysis_id}/chat")
    client.post(
        f"/api/analysis/{analysis_id}/ask/stream",
        json={"question": "¿Qué pasó en el host FS-SRV01?"},
    )
    historia = _chat_history(analysis_store[analysis_id])
    assert [t["question"] for t in historia] == ["¿Qué pasó en el host FS-SRV01?"]


def test_endpoints_stream_en_openapi(client):
    rutas = client.get("/openapi.json").json()["paths"]
    assert "/api/analysis/{analysis_id}/ask/stream" in rutas
    assert "post" in rutas["/api/analysis/{analysis_id}/ask/stream"]
    assert "/api/analysis/{analysis_id}/chat" in rutas
    assert "delete" in rutas["/api/analysis/{analysis_id}/chat"]


# ---------------------------------------------------------------------------
# Camino con LLM: chat_stream falsificado (sin red)
# ---------------------------------------------------------------------------


def _hit(log_id: str, hostname: str = "DC-01", event_id: str = "4625"):
    """Evidencia con Log_ID con prefijo (forma EVT-001) para el grounding."""
    return {
        "metadata": {
            "log_id": log_id,
            "timestamp": "2026-01-10 09:30",
            "hostname": hostname,
            "event_id": event_id,
            "user": "jsmith",
            "source_ip": "10.10.2.15",
            "destination": hostname,
            "severity": "High",
            "description": "An account failed to log on with an invalid account",
            "result": "Failure",
        },
        "level": "alto",
        "suspicious": True,
        "score": 0.91,
        "document": "evento de prueba",
    }


async def _colectar(generador):
    return [evento async for evento in generador]


def test_stream_llm_emite_tokens_y_final_validado(ai, monkeypatch):
    """Con el modelo activo: meta → tokens → final con el texto del LLM."""
    ai.update({"enabled": True, "api_url": "https://api.test/openai/v1/chat/completions"})

    async def chat_stream_falso(messages, cfg=None):
        yield "Se detectaron "
        yield "intentos fallidos en WS-018."

    monkeypatch.setattr(ai, "chat_stream", chat_stream_falso)

    eventos = asyncio.run(_colectar(
        stream_question("¿Hubo intentos de fuerza bruta?", [_hit("EVT-001")], {"rows": 12})
    ))
    tipos = [e["type"] for e in eventos]
    assert tipos == ["meta", "token", "token", "final"]
    assert "".join(e["text"] for e in eventos if e["type"] == "token") == (
        "Se detectaron intentos fallidos en WS-018."
    )
    final = eventos[-1]
    assert final["engine"] == "llm"
    assert final["answer"] == "Se detectaron intentos fallidos en WS-018."
    assert "warning" not in final


def test_stream_llm_con_log_id_inventado_usa_fallback(ai, monkeypatch):
    """Un solo Log_ID inventado invalida TODO el texto transmitido."""
    ai.update({"enabled": True, "api_url": "https://api.test/openai/v1/chat/completions"})

    async def chat_stream_falso(messages, cfg=None):
        yield "El evento "
        yield "EVT-9999 lo confirma."

    monkeypatch.setattr(ai, "chat_stream", chat_stream_falso)

    eventos = asyncio.run(_colectar(
        stream_question("¿Qué pasó en DC-01?", [_hit("EVT-001")], {"rows": 12})
    ))
    assert [e["type"] for e in eventos] == ["meta", "token", "token", "fallback"]
    fallback = eventos[-1]
    assert fallback["warning"] == "grounding_rechazado"
    # Se entrega la respuesta determinista completa, no el texto del modelo.
    assert fallback["engine"] == "deterministic"
    assert "EVT-9999" not in fallback["answer"]
    assert fallback["evidence"]


def test_stream_llm_caido_a_mitad_usa_fallback(ai, monkeypatch):
    """Si el stream del modelo revienta a la mitad, llega el fallback con aviso."""
    ai.update({"enabled": True, "api_url": "https://api.test/openai/v1/chat/completions"})

    async def chat_stream_falso(messages, cfg=None):
        yield "Texto parcial "
        raise RuntimeError("el proveedor se cayó")

    monkeypatch.setattr(ai, "chat_stream", chat_stream_falso)

    eventos = asyncio.run(_colectar(
        stream_question("¿Qué pasó en DC-01?", [_hit("EVT-001")], {"rows": 12})
    ))
    assert [e["type"] for e in eventos] == ["meta", "token", "fallback"]
    fallback = eventos[-1]
    assert fallback["warning"] == "stream_interrumpido"
    assert fallback["engine"] == "deterministic"
    assert fallback["answer"]


def test_stream_llm_respetar_historial_en_el_prompt(ai, monkeypatch):
    """Los turnos previos viajan al modelo como pares user/assistant."""
    ai.update({"enabled": True, "api_url": "https://api.test/openai/v1/chat/completions"})
    capturado = {}

    async def chat_stream_falso(messages, cfg=None):
        capturado["messages"] = messages
        yield "Sí, esos 33 eventos."

    monkeypatch.setattr(ai, "chat_stream", chat_stream_falso)

    historial = [
        {"question": "¿Cuántos intentos de login fallido hay?", "answer": "Hay 33 eventos."},
    ]
    asyncio.run(_colectar(
        stream_question(
            "¿Y esos mismos con IP externa?",
            [_hit("EVT-001")],
            {"rows": 12},
            history=historial,
        )
    ))
    mensajes = capturado["messages"]
    assert mensajes[0]["role"] == "system"
    assert mensajes[-1]["role"] == "user"
    assert "¿Y esos mismos con IP externa?" in mensajes[-1]["content"]
    # El turno previo aparece justo antes de la pregunta actual.
    assert [m["role"] for m in mensajes[1:-1]] == ["user", "assistant"]
    assert mensajes[1]["content"] == "¿Cuántos intentos de login fallido hay?"
    assert mensajes[2]["content"] == "Hay 33 eventos."


def test_app_es_importable():  # guarda contra imports rotos en main/ai_service
    assert app.title.startswith("Comunidad Dojo")
    assert callable(stream_question)
