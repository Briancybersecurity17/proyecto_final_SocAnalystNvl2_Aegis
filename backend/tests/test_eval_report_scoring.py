"""Tests puros de las funciones de scoring del informe (categoría report).

Sin LLM, sin servidor, sin ChromaDB: solo `eval/scoring.py`, que no hace I/O.
Cada función nueva de la tarea 6 tiene test directo, positivo y negativo.
"""
from eval import scoring

# Las 6 claves del contrato del informe (ai_service._CAMPOS_INFORME): aquí se
# fijan a mano porque SON el contrato estructural, no una cifra de datos.
CAMPOS_INFORME = (
    "executive_summary",
    "methodology",
    "findings",
    "correlation",
    "conclusions",
    "action_plan",
)


# ---------------------------------------------------------------------------
# level_count_near
# ---------------------------------------------------------------------------
def test_level_count_near_con_la_cifra_antes_de_la_etiqueta():
    """«97 … nivel Crítico» prueba 97; la cifra equivocada no."""
    assert scoring.level_count_near("Los 97 registros quedaron en el nivel Crítico",
                                    "Crítico", 97)
    assert not scoring.level_count_near("Los 99 registros quedaron en el nivel Crítico",
                                        "Crítico", 97)


def test_level_count_near_acepta_la_etiqueta_antes_de_la_cifra():
    """Forma del fallback determinista: «Crítico: 97 (13,8%)» y
    «Nivel «Crítico»: 97 de 715 registros»."""
    assert scoring.level_count_near("Distribución: Crítico: 97 (13,8%)",
                                    "Crítico", 97)
    assert scoring.level_count_near("Nivel «Crítico»: 97 de 715 registros",
                                    "Crítico", 97)
    assert not scoring.level_count_near("Distribución: Crítico: 99 (13,8%)",
                                        "Crítico", 97)


def test_level_count_near_respeta_genero_y_voz():
    """«Alto»/«Alta» y «Medio»/«Media» casan; el número debe ser real."""
    assert scoring.level_count_near("8 eventos clasificados en nivel Alto",
                                    "Alto", 8)
    assert scoring.level_count_near("205 eventos clasificados en nivel Medio",
                                    "Medio", 205)
    assert not scoring.level_count_near("8 eventos clasificados en nivel Medio",
                                        "Medio", 205)


def test_level_count_near_no_confunde_sustrings_de_palabra():
    """«criticismo» ni «alternativa» son nombres de nivel."""
    assert not scoring.level_count_near("El criticismo no cuenta 5 aquí",
                                        "Crítico", 5)
    assert not scoring.level_count_near("Es una alternativa con 8 opciones",
                                        "Alto", 8)


def test_level_count_near_respeta_la_ventana():
    # «97» queda a 91 chars de «Crítico» (60 de relleno + «quedaron en el
    # nivel »): fuera de 40, dentro de 100.
    lejos = "97 registros" + " " * 60 + "quedaron en el nivel Crítico"
    assert not scoring.level_count_near(lejos, "Crítico", 97, window=40)
    assert scoring.level_count_near(lejos, "Crítico", 97, window=100)


def test_level_count_near_cuenta_la_cifra_de_cierre_de_frase():
    """El punto final («…nivel Bajo: 603.») no escribe el número fuera de
    la extracción — era el falso negativo que escondía la última cifra de
    la distribución."""
    assert scoring.level_count_near("…y el total del nivel Bajo es 603.",
                                    "Bajo", 603)
    assert scoring.number_in_answer("El total: 715.", 715)


# ---------------------------------------------------------------------------
# eventid_pairs_bad
# ---------------------------------------------------------------------------
def test_eventid_pareja_correcta_no_da_motivos():
    assert scoring.eventid_pairs_bad("Se observaron 102 registros de Event ID 4625",
                                     {"4625": 102}) == []


def test_eventid_pareja_mal_atribuida_da_motivo():
    """El 97→4625 histórico: la cifra pegada al Event ID no es su conteo."""
    motivos = scoring.eventid_pairs_bad("97 registros de Event ID 4625",
                                        {"4625": 102})
    assert len(motivos) == 1
    assert "97" in motivos[0] and "4625" in motivos[0]


def test_eventid_id_ausente_del_dataset_da_motivo():
    motivos = scoring.eventid_pairs_bad("5 registros de Event ID 9999",
                                        {"4625": 102})
    assert len(motivos) == 1 and "9999" in motivos[0]


def test_eventid_sin_cifra_adyacente_no_es_afirmacion():
    """«Event ID conocidos de Windows Security» no atribuye ningún conteo."""
    assert scoring.eventid_pairs_bad(
        "Se revisan Event ID conocidos del sistema operativo Windows.",
        {"4625": 102}) == []


def test_eventid_listado_de_ids_con_conteo_entre_parentesis_no_atriba():
    """Forma del informe correcto: «4625 (102), 4624 (111)» enumera, no
    declara «N … Event ID X»."""
    texto = "Event IDs dominantes en este nivel: 4625 (102), 4624 (111)."
    assert scoring.eventid_pairs_bad(texto, {"4625": 102, "4624": 111}) == []


def test_eventid_el_id_de_otra_pareja_no_cuenta_como_cifra():
    """Sin excluir los spans-etiqueta, «… Event ID 4625 y 111 de Event ID
    4624» atribuiría el 4625 como si fuera la cifra del 4624."""
    texto = "102 registros de Event ID 4625 y 111 de Event ID 4624"
    assert scoring.eventid_pairs_bad(texto, {"4625": 102, "4624": 111}) == []
    assert scoring.eventid_pairs_bad(texto, {"4625": 102, "4624": 999})


# ---------------------------------------------------------------------------
# ip_cited
# ---------------------------------------------------------------------------
def test_ip_cited_detecta_ip_del_dataset():
    assert scoring.ip_cited("La IP 10.10.2.15 concentró el tráfico",
                            ["10.10.2.15", "203.0.113.74"])


def test_ip_cited_sin_ip_en_el_texto():
    assert not scoring.ip_cited("Las correlaciones no arrojan patrones",
                                ["10.10.2.15"])


# ---------------------------------------------------------------------------
# required_tokens
# ---------------------------------------------------------------------------
def test_required_tokens_devuelve_solo_los_faltantes():
    assert scoring.required_tokens("Riesgo global: Crítico", ["Crítico", "Medio"]) == ["Medio"]
    assert scoring.required_tokens("Riesgo global: Crítico y Medio",
                                   ["Crítico", "Medio"]) == []


def test_required_tokens_ignora_mayusculas_y_tildes():
    assert scoring.required_tokens("el nivel critico domina", ["Crítico"]) == []


# ---------------------------------------------------------------------------
# sentences_repeated
# ---------------------------------------------------------------------------
def test_sentences_repeated_detecta_frase_larga_repetida():
    frase = "El servidor presentó fallos intermitentes durante la noche entera."
    assert scoring.sentences_repeated(f"{frase} {frase}")


def test_sentences_repeated_texto_sin_repeticiones():
    assert not scoring.sentences_repeated(
        "Uno dos tres cuatro cinco. Seis siete ocho nueve diez.")


def test_sentences_repeated_ignora_frases_cortas():
    """«Nivel crítico. Nivel crítico.» es una enumeración, no redundancia."""
    assert not scoring.sentences_repeated("Nivel crítico. Nivel crítico.")


# ---------------------------------------------------------------------------
# field_sources_ok
# ---------------------------------------------------------------------------
def test_field_sources_ok_con_las_6_claves():
    assert scoring.field_sources_ok(
        {"field_sources": {c: "local" for c in CAMPOS_INFORME}})
    mixto = {c: "local" for c in CAMPOS_INFORME}
    mixto["executive_summary"] = "llm"
    assert scoring.field_sources_ok({"field_sources": mixto})


def test_field_sources_ok_rechaza_clave_faltante_o_valor_raro():
    incompleto = {c: "local" for c in CAMPOS_INFORME}
    del incompleto["conclusions"]
    assert not scoring.field_sources_ok({"field_sources": incompleto})
    assert not scoring.field_sources_ok(
        {"field_sources": {c: "mente" for c in CAMPOS_INFORME}})
    assert not scoring.field_sources_ok({})
