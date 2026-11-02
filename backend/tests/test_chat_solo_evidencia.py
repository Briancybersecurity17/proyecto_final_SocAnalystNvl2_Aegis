"""El asistente responde SOLO con la evidencia del dataset cargado.

Contrato del alcance «solo evidencia»: el chat ya no inyecta la base de
conocimiento (glosario de Event IDs Windows + técnicas MITRE ATT&CK), que es
conocimiento ajeno al archivo cargado. Cubre:

- /ask y /ask/stream devuelven `reference.available = False` y el texto no
  contiene «Referencia técnica» ni menciones a MITRE/ATT&CK.
- El chat ya NO consulta la KB (`kb_store.build_reference` no se llama).
- El system prompt prohíbe explícitamente explicar significados ajenos.
- Una pregunta sobre un Event ID ausente del dataset se responde sin glosario.

El informe y las correlaciones ATT&CK del dashboard NO cambian: la KB sigue
viva para ellos (kb_store.correlate / status), solo se desconectó del chat.
"""

import json

import pytest
from sse_starlette.sse import AppStatus

import main
from main import ai_config
from ai_service import QA_SYSTEM_PROMPT

# Cadenas que solo aparecen cuando el chat mete conocimiento del marco.
TERMINOS_GLOSARIO = ("Referencia técnica", "MITRE", "ATT&CK", "attack.mitre.org")


@pytest.fixture(autouse=True)
def _llm_apagado(tmp_path, monkeypatch):
    """Camino determinista: la respuesta la construye el fallback local."""
    monkeypatch.setattr(ai_config, "CONFIG_PATH", tmp_path / "ai_config.json")
    ai_config.update({"enabled": False})
    yield
    ai_config.update({"enabled": False})


@pytest.fixture(autouse=True)
def _chat_vacio(client, analysis_id):
    """La memoria multi-turno es de sesión: se vacía para no ensayar los tests
    vecinos (que asertan el nº de turnos del hilo conversacional)."""
    client.delete(f"/api/analysis/{analysis_id}/chat")
    yield
    client.delete(f"/api/analysis/{analysis_id}/chat")


@pytest.fixture
def sin_kb(monkeypatch):
    """Falla el test si el chat vuelve a consultar la base de conocimiento."""

    def _explota(*args, **kwargs):  # pragma: no cover - solo corre si hay regresión
        raise AssertionError("el chat consultó kb_store.build_reference")

    monkeypatch.setattr(main.kb_store, "build_reference", _explota)
    return main


def _sin_glosario(texto: str) -> None:
    for termino in TERMINOS_GLOSARIO:
        assert termino not in texto, f"la respuesta metió glosario: «{termino}»"


def _eventos_sse(texto: str):
    """[(evento, payload_dict)] del cuerpo SSE (misma trocea que el chat)."""
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


def test_ask_no_consulta_la_kb_y_responde_sin_glosario(client, analysis_id, sin_kb):
    res = client.post(
        f"/api/analysis/{analysis_id}/ask",
        json={"question": "¿Qué hosts concentran los eventos críticos?"},
    )
    assert res.status_code == 200
    body = res.json()
    # La referencia del marco llega apagada (el contrato de la API se mantiene).
    assert body["reference"]["available"] is False
    _sin_glosario(body["answer"])


def test_ask_event_id_ausente_no_responde_con_glosario(client, analysis_id, sin_kb):
    """El 1102 no está en el dataset sintético: se responde con los datos o
    diciendo que no aparece, nunca con el significado general del evento."""
    res = client.post(
        f"/api/analysis/{analysis_id}/ask",
        json={"question": "¿Qué significa el Event ID 1102?"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reference"]["available"] is False
    _sin_glosario(body["answer"])
    # Nada de conocimiento propio de la limpieza del registro de seguridad.
    assert "limpieza" not in body["answer"].lower()
    assert "borrado de eventos" not in body["answer"].lower()


def test_ask_stream_emite_final_sin_glosario(client, analysis_id, sin_kb):
    AppStatus.should_exit_event = None
    try:
        res = client.post(
            f"/api/analysis/{analysis_id}/ask/stream",
            json={"question": "¿Qué pasó en DC-01?"},
        )
    finally:
        AppStatus.should_exit_event = None
    assert res.status_code == 200

    eventos = _eventos_sse(res.text)
    assert [nombre for nombre, _ in eventos] == ["meta", "final"], eventos
    meta, final = eventos[0][1], eventos[-1][1]
    assert meta["reference"]["available"] is False
    assert final["reference"]["available"] is False
    _sin_glosario(final["answer"])


def test_system_prompt_prohibe_conocimiento_ajeno_al_dataset():
    """Contrato del prompt: única fuente los registros de ESTE archivo."""
    assert "MITRE ATT&CK" in QA_SYSTEM_PROMPT
    assert "NO expliques qué significa un Event ID" in QA_SYSTEM_PROMPT
    assert "glosarios" in QA_SYSTEM_PROMPT
    assert "tu única fuente son los registros de ESTE archivo" in QA_SYSTEM_PROMPT
