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
import attack_cards
import attack_candidates


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


def test_ataque_paraguas_con_detecciones_reencuadra_la_respuesta(ai):
    """Paraguas («¿Hubo un ciberataque?») con detecciones: la respuesta
    empieza diciendo SÍ con el barrido, no con «no tengo una ficha»."""
    res = asyncio.run(
        answer_question("¿Hubo un ciberataque?", [], None, df=_df_fuerza_bruta())
    )
    assert res["mode"] == "attack_card"
    assert res["warnings"] == ["sin_ficha"]
    # El chip manda el barrido completo, no «fuera del catálogo».
    assert res["confidence"] == "alta"
    assert res["summary_line"] == "Barrido del catálogo: 2 de 15 detectan actividad"
    answer = res["answer"]
    total = len(attack_cards.load_cards())
    # Encuadre nuevo: «Sí» + barrido (pieza 5: conteos exactos de evaluate;
    # calibrado con este df: 15 fichas = 2 detectan + 3 sin coincidencias +
    # 10 no evaluables).
    assert answer.startswith(f"Sí: revisé las {total} fichas del catálogo")
    assert "2 detectan actividad" in answer
    assert "No tengo una ficha" not in answer
    assert "Fuerza bruta (1 detección)" in answer
    assert "Acciones repetidas" in answer
    assert "3 no superan sus umbrales" in answer
    assert "10 no se pudieron evaluar" in answer
    # Sin ficha no hay material de qué hablar.
    assert "material del marco" not in answer.lower()


def test_pregunta_concreta_fuera_de_catalogo_no_barrida(ai):
    """Patrón concreto sin ficha («día cero»): texto honesto de siempre,
    SIN barrido del catálogo ni reencuadre de paraguas."""
    res = asyncio.run(
        answer_question("¿Hubo un ataque de día cero?", [], None, df=_df_fuerza_bruta())
    )
    assert res["mode"] == "attack_card"
    assert res["warnings"] == ["sin_ficha"]
    assert res["confidence"] == "ninguna"
    assert res["summary_line"] == "Ataque fuera del catálogo de fichas"
    answer = res["answer"]
    assert "No tengo una ficha para ese patrón" in answer
    # El barrido es solo para consultas paraguas: acá no aparece.
    assert f"{len(attack_cards.load_cards())} fichas" not in answer
    assert "detectan actividad" not in answer


def test_sin_ficha_barrido_sin_detecciones(ai):
    """Ninguna ficha supera su umbral: el barrido lo dice con conteos."""
    res = asyncio.run(
        answer_question("¿Hubo un ciberataque?", [], None, df=_df_sin_fuerza_bruta())
    )

    assert res["warnings"] == ["sin_ficha"]
    # Encuadre nuevo: la respuesta empieza por la negativa directa.
    assert res["confidence"] == "baja"
    assert res["summary_line"] == "Ninguna de las 15 fichas detecta actividad"
    answer = res["answer"]
    total = len(attack_cards.load_cards())
    assert answer.startswith(
        f"No encontré actividad de ataque: revisé las {total} fichas"
    )
    assert "ninguna detecta actividad" in answer
    # Calibrado con este df: 0 detectan + 4 sin coincidencias + 11 no eval.
    assert "4 no superan sus umbrales" in answer
    assert "11 no se pudieron evaluar" in answer
    assert "2 detectan actividad" not in answer


def test_sin_ficha_df_vacio_no_inventa_conteos(ai):
    """Sin filas no hay qué barrer: se dice en vez de inventar un «0 de 15»."""
    vacio = _df_fuerza_bruta().iloc[0:0]
    res = asyncio.run(
        answer_question("¿Hubo un ciberataque?", [], None, df=vacio)
    )

    assert res["warnings"] == ["sin_ficha"]
    assert res["summary_line"] == "Archivo sin registros para evaluar"
    assert res["confidence"] == "ninguna"
    assert "vacío" in res["answer"]
    assert "Catálogo disponible" not in res["answer"]


def test_barrido_con_una_detecta_en_singular(ai):
    """Una sola ficha detecta: «1 detecta actividad», nunca «1 detectan».

    Columnas mínimas para que UNA ficha sea evaluable y detecte:
    Event_ID trae un 4720 (cuentas_nuevas) y solo rdp_fuerza_bruta es
    applicable además (row_filter sobre Event_Description, sin coincidencias).
    Calibrado: 1 detecta + 1 sin umbral + 13 no evaluables = 15.
    """
    df = pd.DataFrame([
        {"Log_ID": "EVT-001", "Event_ID": 4720,
         "Event_Description": "Nuevo usuario creado en el dominio"},
        {"Log_ID": "EVT-002", "Event_ID": 4624,
         "Event_Description": "Registro de actividad normal"},
    ])
    res = asyncio.run(
        answer_question("¿Hubo un ciberataque?", [], None, df=df)
    )

    answer = res["answer"]
    assert "1 detecta actividad" in answer
    assert "1 detectan actividad" not in answer
    assert "Cuentas nuevas fuera de ventana (1 detección)" in answer
    assert "1 no supera sus umbrales" in answer
    assert "1 no superan sus umbrales" not in answer
    assert "13 no se pudieron evaluar" in answer


def test_barrido_con_dos_grupos_lista_detecciones_en_plural(ai):
    """Dos grupos que pasan el umbral: «Fuerza bruta (2 detecciones)».

    Dos IPs con 3 fallos + 1 éxito cada una: la ficha elige 2 grupos y
    el rótulo del barrido tiene que pluralizar «detecciones» (sin tilde
    en la e), no «detecciónes».
    """
    filas = []
    for i, ip in enumerate(("203.0.113.74", "198.51.100.88")):
        for j in range(3):
            filas.append(_logon(100 + i * 10 + j, ip, "DC-01", "Failure"))
        filas.append(_logon(106 + i * 10, ip, "DC-01", "Success", event=4624))
    res = asyncio.run(
        answer_question("¿Hubo un ciberataque?", [], None, df=pd.DataFrame(filas))
    )

    answer = res["answer"]
    assert "1 detecta actividad" in answer
    assert "Fuerza bruta (2 detecciones)" in answer
    assert "detecciónes" not in answer
    # Calibrado: 1 detecta + 4 sin umbral + 10 no evaluables = 15.
    assert "4 no superan sus umbrales" in answer
    assert "10 no se pudieron evaluar" in answer


def test_barrido_sin_detecciones_con_una_sin_umbral_en_singular(ai):
    """Solo una ficha es evaluable y no detecta: «1 no supera sus umbrales».

    `_df_minimo` (solo Log_ID + descripción): rdp es la única applicable
    y no alcanza su umbral; las otras 14 ni se pueden evaluar.
    """
    res = asyncio.run(
        answer_question("¿Hubo un ciberataque?", [], None, df=_df_minimo())
    )

    answer = res["answer"]
    assert "ninguna detecta actividad" in answer
    assert "1 no supera sus umbrales" in answer
    assert "1 no superan sus umbrales" not in answer
    assert "14 no se pudieron evaluar" in answer


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


# ---------------------------------------------------------------------------
# Fase 3 · pieza 2: la señal de aprendizaje (candidata PENDIENTE)
# ---------------------------------------------------------------------------
# Regla del diseño: la respuesta NO cambia de texto; solo se agrega el
# warning `candidata_creada` cuando el borrador pasó la validación
# determinista. Sin LLM, con JSON ilegible o con validación negativa, la
# respuesta queda EXACTAMENTE igual que hoy y no se escribe nada.
_BORRADOR_NUEVO = {
    "id": "credential_stuffing",
    "nombre": "Credential stuffing",
    "que_es": "Reutilización de credenciales filtradas contra muchos usuarios.",
    "como_se_ve": "Muchos 4625 desde una misma IP con usuarios distintos.",
    "event_ids": ["4625"],
    "mitre": ["T1110.004"],
    "sinonimos": ["credential stuffing", "reutilizacion de credenciales"],
    "regla": {"tipo": "group_threshold", "columna": "Source_IP", "min": 3,
              "conteo": {"Event_ID": "4625"}},
    "umbrales": "≥3 eventos 4625 desde la misma IP.",
    "ejemplo": "Cinco 4625 de 203.0.113.74 en DC-01.",
}

_BORRADOR_MEJORA = {
    "id": "fuerza_bruta",
    "nombre": "Fuerza bruta",
    "que_es": "Intentos repetidos de adivinar contraseñas.",
    "como_se_ve": "Muchos 4625 desde una misma IP.",
    "event_ids": ["4625", "4624"],
    "mitre": ["T1110"],
    "sinonimos": ["fuerza bruta", "brute force", "password guessing",
                  "ataque de diccionario"],
    "regla": {"tipo": "group_threshold", "columna": "Source_IP", "min": 3,
              "conteo": {"Event_ID": "4625"}},
    "umbrales": "≥3 eventos 4625 desde la misma IP.",
    "ejemplo": "Cinco 4625 de 203.0.113.74 sin depender de Result.",
    "mejora": True,
}


def _df_sin_result():
    """Fuerza bruta sin la columna `Result`: la ficha vigente no aplica."""
    return _df_fuerza_bruta().drop(columns=["Result"])


def _encender_llm(ai, monkeypatch, respuesta, llamadas=None):
    """LLM encendido con `chat` falso; registra cada llamada en `llamadas`."""
    ai.update({"enabled": True,
               "api_url": "https://api.test/openai/v1/chat/completions"})

    async def chat_falso(messages, cfg=None):
        if llamadas is not None:
            llamadas.append(messages)
        return respuesta

    monkeypatch.setattr(ai, "chat", chat_falso)


def test_sin_ficha_con_llm_crea_candidata_pendiente(ai, monkeypatch):
    # Patrón CONCRETO fuera del catálogo: es cuando toca proponer candidata.
    pregunta = "¿Hubo un ataque de día cero?"
    # Respuesta de referencia SIN LLM: el texto no puede cambiar después.
    sin_llm = asyncio.run(
        answer_question(pregunta, [], None, df=_df_fuerza_bruta())
    )
    _encender_llm(ai, monkeypatch, json.dumps(_BORRADOR_NUEVO, ensure_ascii=False))
    con_llm = asyncio.run(
        answer_question(pregunta, [], None, df=_df_fuerza_bruta())
    )

    # Texto INTACTO (misma honestidad) + el aviso.
    assert con_llm["answer"] == sin_llm["answer"]
    assert "sin_ficha" in con_llm["warnings"]
    assert "candidata_creada" in con_llm["warnings"]

    # La candidata quedó PENDIENTE en el JSON temporal (nunca en el real).
    candidatas = attack_candidates.list_candidates()
    assert [c["id"] for c in candidatas] == ["credential_stuffing"]
    assert candidatas[0]["status"] == "pendiente"
    assert candidatas[0]["tipo"] == "nueva"
    assert candidatas[0]["question"] == pregunta
    assert attack_candidates.CANDIDATES_PATH.exists()

    # Y el catálogo NO cambió: match_card sigue sin conocer la ficha.
    ids = [c["id"] for c in attack_cards.load_cards()]
    assert "credential_stuffing" not in ids


def test_paraguas_con_llm_no_propone_candidata(ai, monkeypatch):
    """«¿Hubo un ciberataque?» no nombra ningún patrón: no hay nada que
    proponer, así que el LLM ni se invoca y no se escribe candidata."""
    llamadas = []
    _encender_llm(ai, monkeypatch,
                  json.dumps(_BORRADOR_NUEVO, ensure_ascii=False), llamadas)
    res = asyncio.run(
        answer_question("¿Hubo un ciberataque?", [], None, df=_df_fuerza_bruta())
    )

    assert res["warnings"] == ["sin_ficha"]
    # El LLM NO se invoca para una consulta paraguas (nada que proponer).
    assert llamadas == []
    assert attack_candidates.list_candidates() == []


def test_sin_ficha_sin_llm_no_crea_candidata(ai):
    res = asyncio.run(
        answer_question("¿Hubo un ciberataque?", [], None, df=_df_fuerza_bruta())
    )

    assert res["warnings"] == ["sin_ficha"]
    assert attack_candidates.list_candidates() == []
    assert not attack_candidates.CANDIDATES_PATH.exists()


def test_el_prompt_de_candidata_trae_datos_reales_del_archivo(ai, monkeypatch):
    llamadas = []
    _encender_llm(ai, monkeypatch,
                  json.dumps(_BORRADOR_NUEVO, ensure_ascii=False), llamadas)
    asyncio.run(
        answer_question("¿Hubo un ataque de día cero?", [], None, df=_df_fuerza_bruta())
    )

    assert llamadas, "el LLM no llegó a invocarse para proponer la candidata"
    prompt = json.dumps(llamadas[0], ensure_ascii=False)
    # Columnas y Event IDs REALES del dataframe (el borrador se ancla a ellos).
    assert "Source_IP" in prompt
    assert "4625" in prompt
    # Tipos de regla cerrados + ids/sinónimos existentes a evitar (colisiones).
    assert "group_threshold" in prompt
    assert "fuerza_bruta" in prompt
    assert "que_es" in prompt


def test_llm_basura_no_rompe_la_respuesta_de_sin_ficha(ai, monkeypatch):
    _encender_llm(ai, monkeypatch, "lo siento, no puedo redactar eso")
    res = asyncio.run(
        answer_question("¿Hubo un ataque de día cero?", [], None, df=_df_fuerza_bruta())
    )

    assert res["warnings"] == ["sin_ficha"]
    # Sin candidata válida la respuesta determinista viaja completa.
    assert "No tengo una ficha para ese patrón" in res["answer"]
    assert attack_candidates.list_candidates() == []


def test_ficha_no_aplicable_propone_mejora(ai, monkeypatch):
    df = _df_sin_result()
    # Respuesta de referencia SIN LLM sobre el mismo archivo sin `Result`.
    sin_llm = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=df)
    )
    assert "ficha_no_aplicable" in sin_llm["warnings"]
    _encender_llm(ai, monkeypatch, json.dumps(_BORRADOR_MEJORA, ensure_ascii=False))
    con_llm = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=df)
    )

    # Texto INTACTO + aviso de candidata creada.
    assert con_llm["answer"] == sin_llm["answer"]
    assert "ficha_no_aplicable" in con_llm["warnings"]
    assert "candidata_creada" in con_llm["warnings"]

    candidatas = attack_candidates.list_candidates()
    assert [c["id"] for c in candidatas] == ["fuerza_bruta"]
    assert candidatas[0]["tipo"] == "mejora"
    # La regla VIGENTE viaja junto: el revisor compara propuesta vs. actual.
    assert "Result" in json.dumps(candidatas[0]["regla_anterior"])
    # El catálogo sigue intacto: la mejora NO se aplicó sola.
    card = next(c for c in attack_cards.load_cards() if c["id"] == "fuerza_bruta")
    assert "Result" in json.dumps(card["regla"])


def test_no_aplicable_sin_llm_no_cambia_nada(ai):
    res = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=_df_sin_result())
    )

    assert res["warnings"] == ["ficha_no_aplicable"]
    assert attack_candidates.list_candidates() == []


def test_deteccion_normal_no_dispara_senal(ai, monkeypatch):
    llamadas = []
    _encender_llm(ai, monkeypatch,
                  "La IP 203.0.113.74 acumula 5 fallos y 1 éxito en DC-01.",
                  llamadas)
    res = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=_df_fuerza_bruta())
    )

    assert res["attack_card"]["total"] > 0
    assert llamadas, "la redacción de la detección sí debe invocar al LLM"
    assert "candidata_creada" not in res["warnings"]
    assert attack_candidates.list_candidates() == []


def test_df_vacio_no_invoca_al_llm_para_candidata(ai, monkeypatch):
    llamadas = []
    _encender_llm(ai, monkeypatch, json.dumps(_BORRADOR_MEJORA), llamadas)
    vacio = _df_fuerza_bruta().iloc[0:0]
    res = asyncio.run(
        answer_question("¿Hubo fuerza bruta?", [], None, df=vacio)
    )

    assert res["warnings"] == ["ficha_no_aplicable"]
    assert llamadas == []   # sin filas no hay con qué anclar el borrador
    assert attack_candidates.list_candidates() == []
