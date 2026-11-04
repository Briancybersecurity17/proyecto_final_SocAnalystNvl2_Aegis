"""Ruta híbrida de fichas de ataque en el asistente (diseño aprobado, fase 1+2).

Cubre el contrato completo de la ruta:

1. `routes_pattern` clasifica la pregunta: sinónimo de ficha O vocabulario
   genérico de ataque («ciberataque») → nunca se cae en silencio al RAG.
2. Respuesta determinista con las cifras EXACTAS de la ficha evaluada sobre
   el dataframe. El bloque «Material del marco» etiquetado viaja SOLO en
   preguntas conceptuales («¿qué es…?», «¿cómo se ve…?»): en detecciones,
   negativas y fichas explicativas era ruido (pieza 2), así que ahí mandan
   las cifras + el chip `ficha <id> · <motivo>`.
3. Degradación honesta: ficha sin regla (explicativa), archivo sin las
   columnas/Event IDs necesarios, o ataque fuera del catálogo.
4. Híbrido con anclaje: el LLM redacta SOLO si todas sus cifras ya están en
   el borrador; una cifra inventada cae al borrador determinista.
5. Prioridad sobre agregados y sin recuperación (retrieved == 0 en /ask).
6. Sin dataframe (llamadas directas legadas) la ruta NO se activa: el flujo
   normal de hoy sigue intacto.
"""

import asyncio
import json

import pandas as pd
import pytest
from sse_starlette.sse import AppStatus

from ai_service import answer_question, routes_pattern, stream_question
from main import ai_config


# ---------------------------------------------------------------------------
# Fixtures (mismos patrones que test_chat_stream / test_puerta_tema)
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
# Datasets de prueba
# ---------------------------------------------------------------------------
def _logon(i, ip, host, result, event=4625, ts="2026-01-10 08:00"):
    return {
        "Log_ID": f"EVT-{i:03d}", "Timestamp": ts, "Hostname": host,
        "Event_ID": event, "User": "jsmith", "Source_IP": ip, "Result": result,
    }


def _df_fuerza_bruta():
    """IP .74 con 5 fallos + 1 éxito (pasa el umbral); .88 solo 1 fallo."""
    filas = [_logon(100 + i, "203.0.113.74", "DC-01", "Failure") for i in range(5)]
    filas.append(_logon(106, "203.0.113.74", "DC-01", "Success", event=4624))
    filas.append(_logon(200, "198.51.100.88", "WS-01", "Failure"))
    return pd.DataFrame(filas)


def _df_sin_fuerza_bruta():
    """Solo éxitos: la regla no encuentra ningún grupo."""
    filas = [_logon(100 + i, "203.0.113.74", "DC-01", "Success", event=4624)
             for i in range(3)]
    return pd.DataFrame(filas)


def _df_minimo():
    """Solo Log_ID + descripción: faltan todas las columnas de la regla."""
    return pd.DataFrame([
        {"Log_ID": "EVT-001", "Event_Description": "Registro normal"},
        {"Log_ID": "EVT-002", "Event_Description": "Intento de acceso"},
    ])


async def _colectar(generador):
    return [evento async for evento in generador]


# ---------------------------------------------------------------------------
# Clasificador
# ---------------------------------------------------------------------------
def test_routes_pattern_reconoce_ataques():
    assert routes_pattern("¿Hubo fuerza bruta en mis registros?")
    assert routes_pattern("¿hubo un ciberataque?")
    # Preguntas normales del asistente: fuera de la ruta.
    assert not routes_pattern("¿qué pasó en el host DC-01?")
    assert not routes_pattern("¿cuántos registros hay en total?")


def test_sin_dataframe_la_ruta_no_se_activa():
    """Llamadas directas legadas (sin df) mantienen el flujo normal."""
    res = asyncio.run(answer_question("¿Hubo fuerza bruta?", [], {"rows": 12}))
    assert res.get("mode") != "attack_card"


def test_preguntas_normales_no_entran_en_la_ruta():
    res = asyncio.run(
        answer_question("¿qué pasó en DC-01?", [], {"rows": 12}, df=_df_fuerza_bruta())
    )
    assert res.get("mode") != "attack_card"


# ---------------------------------------------------------------------------
# Respuesta determinista (fase 1 + 2)
# ---------------------------------------------------------------------------
def test_respuesta_determinista_con_cifras_de_la_ficha(ai):
    res = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=_df_fuerza_bruta())
    )
    assert res["mode"] == "attack_card"
    assert res["engine"] == "deterministic"
    assert res["attack_card"]["id"] == "fuerza_bruta"
    assert res["attack_card"]["motivo"] == "ok"
    assert res["attack_card"]["total"] == 1
    assert res["evidence"] == []

    answer = res["answer"]
    assert "203.0.113.74" in answer
    assert "fallos: 5" in answer
    assert "éxitos: 1" in answer
    # Pieza 2: la detección NO arrastra material del marco (ruido bajo las
    # cifras): solo cifras exactas + el chip de la ficha en la línea de resumen.
    assert "material del marco" not in answer.lower()
    assert "NO es un hallazgo" not in answer
    assert res["summary_line"] == "ficha fuerza_bruta · ok"


def test_sin_detecciones_respuesta_negativa_con_el_umbral():
    res = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=_df_sin_fuerza_bruta())
    )
    assert res["mode"] == "attack_card"
    assert res["attack_card"]["detections"] == []
    answer = res["answer"]
    assert "No detecté" in answer
    assert "ningún grupo supera el umbral" in answer
    # El umbral de la ficha viaja siempre: la negativa es verificable.
    assert "≥3 fallos" in answer
    # Pieza 2: sin detección tampoco hay bloque de material (es conceptual).
    assert "material del marco" not in answer.lower()


def test_no_aplica_por_falta_de_columnas():
    res = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=_df_minimo())
    )
    assert res["mode"] == "attack_card"
    assert res["attack_card"]["applicable"] is False
    assert res["attack_card"]["motivo"] == "sin_columna"
    assert "ficha_no_aplicable" in res["warnings"]
    answer = res["answer"]
    assert "no tiene los datos" in answer
    assert "Result" in answer
    # Pieza 2: el material del marco es ruido en una respuesta de «no aplica».
    assert "material del marco" not in answer.lower()


def test_no_aplica_por_event_ids_ausentes():
    df = pd.DataFrame([
        {"Log_ID": "EVT-001", "Timestamp": "2026-01-10 08:00", "Hostname": "DC-01",
         "Event_ID": 4740, "User": "admin", "Source_IP": "10.10.2.20",
         "Result": "Failure"},
    ])
    res = asyncio.run(answer_question("¿Hubo fuerza bruta?", [], None, df=df))
    assert res["attack_card"]["motivo"] == "sin_event_ids"
    answer = res["answer"]
    assert "no contiene" in answer
    assert "4625" in answer


def test_ficha_explicativa_no_detecta():
    res = asyncio.run(
        answer_question("¿Hubo ransomware?", [], None, df=_df_fuerza_bruta())
    )
    assert res["mode"] == "attack_card"
    assert res["attack_card"]["motivo"] == "sin_regla"
    assert "ficha_explicativa" in res["warnings"]
    assert "explicativa" in res["answer"]
    # Pieza 2: el bloque de material SOLO en preguntas conceptuales — también
    # acá se quita (decisión del analista) y la respuesta redirige al «¿qué es?».
    assert "material del marco" not in res["answer"].lower()


def test_ataque_fuera_del_catalogo_respuesta_honesta():
    res = asyncio.run(
        answer_question("¿Hubo un ciberataque?", [], None, df=_df_fuerza_bruta())
    )
    assert res["mode"] == "attack_card"
    assert "sin_ficha" in res["warnings"]
    assert res["confidence"] == "ninguna"
    answer = res["answer"]
    assert "catálogo" in answer
    assert "Fuerza bruta" in answer   # el listado de cobertura
    # Sin ficha no hay material de qué hablar: solo el catálogo disponible.
    assert "material del marco" not in answer.lower()


def test_pregunta_conceptual_encabeza_el_material_del_marco(ai):
    res = asyncio.run(
        answer_question("¿Qué es la fuerza bruta?", [], None, df=_df_fuerza_bruta())
    )
    answer = res["answer"]
    # Es la ÚNICA variante conceptual: el bloque viaja completo y encabezado
    # (antes del hallazgo), con sus cinco rótulos.
    assert "Material del marco" in answer
    assert answer.index("Material del marco") < answer.index("detectada en tus registros")
    for rotulo in ("Qué es:", "Cómo se ve en logs:", "Umbral:",
                   "MITRE ATT&CK:", "Ejemplo:"):
        assert rotulo in answer, f"falta el rótulo «{rotulo}» en la respuesta"
    assert res["summary_line"] == "ficha fuerza_bruta · ok"


def test_otra_variant_conceptual_tambien_lleva_material(ai):
    """«¿Cómo se ve…?» es conceptual igual que «¿qué es…?»."""
    res = asyncio.run(
        answer_question("¿Cómo se ve la fuerza bruta en mis logs?", [], None,
                        df=_df_fuerza_bruta())
    )
    assert res["mode"] == "attack_card"
    assert "Material del marco" in res["answer"]


def test_los_ataques_tienen_prioridad_sobre_los_agregados():
    # «cuantos» la marcaría como agregación; la ficha da cifras exactas del
    # ataque, que es lo que el analista pide.
    res = asyncio.run(
        answer_question("¿Cuántos intentos de fuerza bruta hubo?", [],
                        {"rows": 12}, df=_df_fuerza_bruta())
    )
    assert res["mode"] == "attack_card"


# ---------------------------------------------------------------------------
# Streaming: una sola respuesta final (mismo contrato que los agregados)
# ---------------------------------------------------------------------------
def test_stream_emite_un_solo_final_con_la_ficha():
    eventos = asyncio.run(_colectar(
        stream_question("¿Hubo fuerza bruta?", [], None, df=_df_fuerza_bruta())
    ))
    assert [e["type"] for e in eventos] == ["final"]
    final = eventos[0]
    assert final["mode"] == "attack_card"
    assert "203.0.113.74" in final["answer"]
    assert final["attack_card"]["id"] == "fuerza_bruta"


# ---------------------------------------------------------------------------
# Híbrido: el LLM redacta encima SOLO con cifras ancladas al borrador
# ---------------------------------------------------------------------------
def test_llm_redacta_manteniendo_las_cifras_del_borrador(ai, monkeypatch):
    ai.update({"enabled": True,
               "api_url": "https://api.test/openai/v1/chat/completions"})

    async def chat_falso(messages, cfg=None):
        return ("La IP 203.0.113.74 acumula fallos: 5 y éxitos: 1 contra "
                "DC-01: revisar las credenciales de esa cuenta.")

    monkeypatch.setattr(ai, "chat", chat_falso)
    res = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=_df_fuerza_bruta())
    )
    assert res["engine"] == "llm"
    assert res["answer"].startswith("La IP 203.0.113.74 acumula fallos: 5")
    assert res["attack_card"]["id"] == "fuerza_bruta"
    # Pieza 2: ni el borrador ni la redacción del LLM traen el bloque de
    # material del marco en una pregunta de detección.
    assert "material del marco" not in res["answer"].lower()


def test_llm_con_cifra_inventada_usa_el_borrador(ai, monkeypatch):
    ai.update({"enabled": True,
               "api_url": "https://api.test/openai/v1/chat/completions"})

    async def chat_falso(messages, cfg=None):
        return "La IP 203.0.113.74 acumula fallos: 5 y éxitos: 99."

    monkeypatch.setattr(ai, "chat", chat_falso)
    res = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=_df_fuerza_bruta())
    )
    # 99 no está en el borrador → redacción descartada, manda lo determinista.
    assert res["engine"] == "deterministic"
    assert "fallos: 5" in res["answer"]
    assert "grounding_rechazado" in res["warnings"]


def test_llm_con_tipografia_no_es_cifra_inventada(ai, monkeypatch):
    """El modelo reescribe los Log_ID con guion largo (U+2011) y espacios
    angostos (U+202F): es tipografía, no una cifra inventada. La redacción
    válida debe aceptarse y el texto normalizado a guion ASCII (el analista
    copia los IDs a otras herramientas)."""
    ai.update({"enabled": True,
               "api_url": "https://api.test/openai/v1/chat/completions"})

    async def chat_falso(messages, cfg=None):
        return ("La IP 203.0.113.74 acumuló\u202f5 fallos y\u202f1 éxito "
                "(EVT\u2011100, EVT\u2011101); revisá las credenciales.")

    monkeypatch.setattr(ai, "chat", chat_falso)
    res = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=_df_fuerza_bruta())
    )
    assert res["engine"] == "llm"
    assert "EVT-100" in res["answer"]
    assert "\u2011" not in res["answer"]
    assert "\u202f" not in res["answer"]
    assert "grounding_rechazado" not in res["warnings"]


def test_llm_id_inventado_con_guion_largo_sigue_cayendo(ai, monkeypatch):
    """La normalización no puede abrir la puerta: «EVT‑00005» (U+2011) cita
    un ID inexistente del dataset y sus dígitos (5) coinciden con una cifra
    del borrador. Sin normalizar, `_numbers` lee «00005» como 5 y
    `_cited_log_ids` no reconoce el ID: la redacción pasaría inventada."""
    ai.update({"enabled": True,
               "api_url": "https://api.test/openai/v1/chat/completions"})

    async def chat_falso(messages, cfg=None):
        return ("La IP 203.0.113.74 acumuló 5 fallos y 1 éxito "
                "(EVT\u201100005): revisá las credenciales.")

    monkeypatch.setattr(ai, "chat", chat_falso)
    res = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=_df_fuerza_bruta())
    )
    assert res["engine"] == "deterministic"
    assert "grounding_rechazado" in res["warnings"]


def test_llm_no_se_invoca_cuando_no_hay_deteccion(ai, monkeypatch):
    ai.update({"enabled": True,
               "api_url": "https://api.test/openai/v1/chat/completions"})

    async def chat_prohibido(messages, cfg=None):
        raise AssertionError("sin detección no hay redacción que pedir")

    monkeypatch.setattr(ai, "chat", chat_prohibido)
    res = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=_df_sin_fuerza_bruta())
    )
    assert res["engine"] == "deterministic"
    assert "No detecté" in res["answer"]


# ---------------------------------------------------------------------------
# Endpoints: corte ANTES de la recuperación
# ---------------------------------------------------------------------------
def test_ask_de_ataque_no_recupera_y_usa_la_ficha(client, analysis_id):
    client.delete(f"/api/analysis/{analysis_id}/chat")
    res = client.post(
        f"/api/analysis/{analysis_id}/ask",
        json={"question": "¿Hubo intentos de fuerza bruta?"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["retrieved"] == 0
    assert data["evidence"] == []
    assert data["mode"] == "attack_card"
    assert data["attack_card"]["id"] == "fuerza_bruta"
    # Dataset sintético de conftest: 10.10.2.15 → 3 fallos + 2 éxitos.
    assert "10.10.2.15" in data["answer"]
    assert "fallos: 3" in data["answer"]
    assert "éxitos: 2" in data["answer"]
    client.delete(f"/api/analysis/{analysis_id}/chat")


def test_ask_stream_de_ataque_emite_un_solo_final(client, analysis_id):
    client.delete(f"/api/analysis/{analysis_id}/chat")
    res = client.post(
        f"/api/analysis/{analysis_id}/ask/stream",
        json={"question": "¿Hubo intentos de fuerza bruta?"},
    )
    assert res.status_code == 200, res.text
    eventos = _eventos_sse(res.text)
    assert [nombre for nombre, _ in eventos] == ["final"]
    payload = eventos[0][1]
    assert payload["retrieved"] == 0
    assert payload["mode"] == "attack_card"
    assert "10.10.2.15" in payload["answer"]
    client.delete(f"/api/analysis/{analysis_id}/chat")
