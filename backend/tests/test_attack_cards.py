"""Fichas de ataque: clasificación de preguntas y evaluador de reglas.

TDD (rojo→verde) de la pieza 1 del diseño aprobado:

- Catálogo declarativo único en `knowledge/attack_cards.json` (~15 fichas).
- Evaluador con tipos de regla CERRADOS (sin eval ni código dinámico):
  `event_presence`, `event_burst`, `group_threshold` y `row_filter`.
- Degradación honesta: si falta una columna, si el archivo no contiene los
  Event IDs de la ficha o si la ficha es solo explicativa (regla null),
  `evaluate` devuelve `applicable=False` con el motivo — nunca lanza.
- `behavior_correlations.py` queda intacto: las fichas conviven con él.
"""

import pandas as pd

import attack_cards


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
COLUMNAS = [
    "Log_ID", "Timestamp", "Hostname", "Event_ID", "User", "Source_IP", "Result",
]


def _df(filas, columnas=None):
    """DataFrame de dicts con las columnas estándar (las que falten → NaN)."""
    return pd.DataFrame(filas, columns=columnas or COLUMNAS)


def _evento(i, ip="10.10.2.15", host="DC-01", result="Failure", event=4625,
            user="jsmith", ts="2026-01-10 08:00"):
    return {
        "Log_ID": f"EVT-{i:03d}", "Timestamp": ts, "Hostname": host,
        "Event_ID": event, "User": user, "Source_IP": ip, "Result": result,
    }


def _catalogo(card_id):
    for card in attack_cards.load_cards():
        if card["id"] == card_id:
            return card
    raise AssertionError(f"ficha «{card_id}» no está en el catálogo")


# ---------------------------------------------------------------------------
# match_card: la pregunta nombra un ataque
# ---------------------------------------------------------------------------
def test_match_card_detecta_el_sinonimo_en_espanol():
    card = attack_cards.match_card("¿Hubo fuerza bruta contra DC-01?")
    assert card is not None
    assert card["id"] == "fuerza_bruta"


def test_match_card_detecta_el_sinonimo_en_ingles():
    card = attack_cards.match_card("¿Any brute force attempts?")
    assert card is not None
    assert card["id"] == "fuerza_bruta"


def test_match_card_sin_ataque_en_la_pregunta():
    assert attack_cards.match_card("¿cuántos registros hay en total?") is None
    assert attack_cards.match_card("¿qué pasó en el host DC-01?") is None


def test_match_card_prioriza_el_sinonimo_mas_largo():
    # «fuerza bruta distribuida» (spraying) y «fuerza bruta rdp» contienen
    # «fuerza bruta»: gana el sinonimo más largo, no el primero del archivo.
    assert attack_cards.match_card("¿hubo fuerza bruta distribuida?")["id"] == "spraying"
    assert attack_cards.match_card("¿hubo fuerza bruta rdp?")["id"] == "rdp_fuerza_bruta"


def test_match_card_respeta_los_limites_de_palabra():
    # Subcadena dentro de otra palabra: no es el ataque nombrado.
    assert attack_cards.match_card("¿hubo fuerzabrutal?") is None


def test_match_card_admite_plurales():
    assert attack_cards.match_card("¿hubo barridos?")["id"] == "barrido"


def test_mentions_generic_attack_solo_con_vocabulario_de_ataque():
    assert attack_cards.mentions_generic_attack("¿hubo un ciberataque?")
    assert attack_cards.mentions_generic_attack("¿algún malware en los registros?")
    assert not attack_cards.mentions_generic_attack("¿cuántos eventos hay?")
    assert not attack_cards.mentions_generic_attack("¿qué pasó en DC-01?")
    assert not attack_cards.mentions_generic_attack("")


def test_es_consulta_paraguas_cuando_no_queda_nada_especifico():
    # Tras quitar andamiaje (hubo, algún, en, los, registros…) y vocabulario
    # paraguas (ciberataque, malware, incidentes…) la pregunta queda vacía.
    assert attack_cards.es_consulta_paraguas("¿Hubo un ciberataque?")
    assert attack_cards.es_consulta_paraguas("¿Algún malware en los registros?")
    assert attack_cards.es_consulta_paraguas("¿Hubo incidentes?")


def test_es_consulta_paraguas_con_patron_concreto_es_falso():
    # Queda un patrón específico después de limpiar: NO es paraguas.
    assert not attack_cards.es_consulta_paraguas("¿Hubo un ataque de día cero?")
    # Sin vocabulario de ataque tampoco (nunca llega a la ruta, pero el
    # helper tiene que ser honesto por su cuenta).
    assert not attack_cards.es_consulta_paraguas("¿Cuántos registros hay en total?")
    assert not attack_cards.es_consulta_paraguas("")


# ---------------------------------------------------------------------------
# event_presence
# ---------------------------------------------------------------------------
def _card_presence(minimo=1, event_ids=("1102",)):
    return {
        "id": "t_presence", "nombre": "Presencia", "event_ids": list(event_ids),
        "regla": {"tipo": "event_presence", "min": minimo},
    }


def test_event_presence_detecta_por_encima_del_umbral():
    df = _df([
        _evento(1, event=4624, result="Success"),
        _evento(2, event=1102, result="Success"),
    ])
    res = attack_cards.evaluate(_card_presence(), df)
    assert res["applicable"] is True
    assert res["motivo"] == "ok"
    assert res["total"] == 1
    det = res["detections"][0]
    assert det["evento"] == "1102"
    assert det["cantidad"] == 1
    assert "EVT-002" in det["log_ids"]


def test_event_presence_bajo_umbral_no_detecta():
    df = _df([_evento(1, event=1102, result="Success")])
    res = attack_cards.evaluate(_card_presence(minimo=2), df)
    assert res["applicable"] is True   # el archivo sí contiene el evento
    assert res["total"] == 0
    assert res["detections"] == []


def test_event_presence_no_aplica_si_el_archivo_no_tiene_esos_eventos():
    df = _df([_evento(1, event=4624, result="Success")])
    res = attack_cards.evaluate(_card_presence(), df)
    assert res["applicable"] is False
    assert res["motivo"] == "sin_event_ids"
    assert "1102" in res["missing"]


def test_event_presence_no_aplica_sin_la_columna_event_id():
    df = _df([_evento(1)], columnas=["Log_ID", "Source_IP", "Result"])
    res = attack_cards.evaluate(_card_presence(), df)
    assert res["applicable"] is False
    assert res["motivo"] == "sin_columna"
    assert "Event_ID" in res["missing"]


# ---------------------------------------------------------------------------
# group_threshold (con las fichas REALES del catálogo)
# ---------------------------------------------------------------------------
def test_fuerza_bruta_con_las_cifras_exactas_del_grupo():
    filas = []
    for i in range(5):   # 5 fallos de la misma IP
        filas.append(_evento(100 + i, ip="203.0.113.74", result="Failure"))
    filas.append(_evento(106, ip="203.0.113.74", event=4624, result="Success"))
    # 3 fallos SIN éxito: no llega al umbral (igual que behavior_correlations)
    for i in range(3):
        filas.append(_evento(200 + i, ip="198.51.100.77", result="Failure"))
    # 1 fallo + 1 éxito: por debajo de «≥3 fallos»
    filas.append(_evento(300, ip="198.51.100.88", result="Failure"))
    filas.append(_evento(301, ip="198.51.100.88", event=4624, result="Success"))

    res = attack_cards.evaluate(_catalogo("fuerza_bruta"), _df(filas))
    assert res["applicable"] is True
    assert res["total"] == 1
    det = res["detections"][0]
    assert det["grupo"] == "203.0.113.74"
    assert det["metricas"] == {"fallos": 5, "exitos": 1}
    assert det["log_ids"], "la detección debe traer Log_ID de ejemplo"
    assert det["log_ids"][0] == "EVT-100"
    assert det["log_ids"][-1] == "EVT-106"


def test_barrido_cuenta_hosts_distintos_por_ip():
    filas = []
    for i, host in enumerate(["DC-01", "WS-01", "WS-02", "FS-01"], start=1):
        filas.append(_evento(i, ip="203.0.113.9", host=host))
    for i, host in enumerate(["DC-01", "WS-01"], start=50):
        filas.append(_evento(i, ip="198.51.100.5", host=host))

    res = attack_cards.evaluate(_catalogo("barrido"), _df(filas))
    assert res["applicable"] is True
    assert res["total"] == 1
    det = res["detections"][0]
    assert det["grupo"] == "203.0.113.9"
    assert det["metricas"] == {"hosts": 4}


def test_acciones_repetidas_detecta_la_combinacion_repetida():
    filas = []
    for i in range(5):   # misma IP + host + evento, 5 veces → umbral
        filas.append(_evento(10 + i, ip="203.0.113.74", host="DC-01", event=4625))
    for i in range(4):   # 4 veces: por debajo del umbral
        filas.append(_evento(60 + i, ip="198.51.100.77", host="WS-01", event=4688))

    res = attack_cards.evaluate(_catalogo("acciones_repetidas"), _df(filas))
    assert res["applicable"] is True
    assert res["total"] == 1
    det = res["detections"][0]
    assert det["valores"] == {
        "Source_IP": "203.0.113.74", "Hostname": "DC-01", "Event_ID": "4625",
    }
    assert det["cantidad"] == 5


def test_fuerza_bruta_no_aplica_si_falta_la_columna_result():
    df = _df([_evento(1)], columnas=["Log_ID", "Hostname", "Event_ID", "Source_IP"])
    res = attack_cards.evaluate(_catalogo("fuerza_bruta"), df)
    assert res["applicable"] is False
    assert res["motivo"] == "sin_columna"
    assert "Result" in res["missing"]


def test_fuerza_bruta_no_aplica_sin_sus_event_ids_en_el_archivo():
    # Archivo con Result pero solo eventos 4740: no trae los datos de la regla.
    df = _df([_evento(1, event=4740)])
    res = attack_cards.evaluate(_catalogo("fuerza_bruta"), df)
    assert res["applicable"] is False
    assert res["motivo"] == "sin_event_ids"
    assert "4625" in res["missing"]


# ---------------------------------------------------------------------------
# row_filter (ficha powershell_codificado) y fichas explicativas
# ---------------------------------------------------------------------------
def test_powershell_codificado_requiere_la_columna_command_line():
    df = _df([_evento(1)])
    res = attack_cards.evaluate(_catalogo("powershell_codificado"), df)
    assert res["applicable"] is False
    assert res["motivo"] == "sin_columna"
    assert "Command_Line" in res["missing"]


def test_powershell_codificado_detecta_el_comando_ofuscado():
    df = pd.DataFrame([
        {"Log_ID": "EVT-001", "Command_Line": "powershell.exe -NoP -Enc SQBFAFgA"},
        {"Log_ID": "EVT-002", "Command_Line": "powershell.exe -File check.ps1"},
    ])
    res = attack_cards.evaluate(_catalogo("powershell_codificado"), df)
    assert res["applicable"] is True
    assert res["total"] == 1
    assert res["detections"][0]["cantidad"] == 1
    assert "EVT-001" in res["detections"][0]["log_ids"]


def test_ficha_explicativa_sin_regla_no_detecta():
    # ransomware es explicativa en v1: regla null → nunca detecta, nunca lanza.
    res = attack_cards.evaluate(_catalogo("ransomware"), _df([_evento(1)]))
    assert res["applicable"] is False
    assert res["motivo"] == "sin_regla"
    assert res["detections"] == []


def test_evaluador_no_lanza_con_un_dataframe_vacio():
    res = attack_cards.evaluate(_catalogo("fuerza_bruta"), pd.DataFrame(columns=COLUMNAS))
    assert res["applicable"] is False
    assert res["motivo"] == "sin_datos"
    res2 = attack_cards.evaluate(_catalogo("fuerza_bruta"), None)
    assert res2["applicable"] is False


# ---------------------------------------------------------------------------
# Contrato del catálogo (diseño aprobado)
# ---------------------------------------------------------------------------
def test_catalogo_cubre_el_diseño_aprobado():
    cards = attack_cards.load_cards()
    ids = {c["id"] for c in cards}
    esperados = {
        "fuerza_bruta", "spraying", "rdp_fuerza_bruta", "movimiento_lateral",
        "escalamiento", "persistencia", "limpieza_logs", "powershell_codificado",
        "barrido", "exfiltracion", "credential_dumping", "ransomware",
        "cuentas_nuevas", "ip_multi_evento", "acciones_repetidas",
    }
    assert esperados <= ids, f"faltan fichas: {esperados - ids}"
    assert 12 <= len(cards) <= 16

    for card in cards:
        for campo in ("id", "nombre", "que_es", "como_se_ve", "event_ids",
                      "mitre", "sinonimos", "umbrales", "ejemplo"):
            assert card.get(campo) is not None, (
                f"ficha «{card.get('id')}» sin el campo «{campo}»"
            )
        assert card["sinonimos"], f"ficha «{card['id']}» sin sinónimos"
        assert "regla" in card, f"ficha «{card['id']}» sin la clave «regla»"
        if card["regla"] is not None:
            assert card["regla"].get("tipo") in (
                "event_presence", "event_burst", "group_threshold", "row_filter",
            ), f"ficha «{card['id']}» con tipo de regla desconocido"
