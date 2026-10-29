"""Tests puros del scoring del harness de evaluación.

Sin LLM, sin servidor, sin ChromaDB: solo scoring.py (que no hace I/O).
"""
import pytest

from eval import scoring


def test_cited_ids_detecta_log_ids():
    assert scoring.cited_ids("Falló en EVT-00002 y EVT-00042") == ["EVT-00002", "EVT-00042"]


def test_invented_ids_marca_lo_que_no_esta_en_el_dataset():
    dataset = {"EVT-00001", "EVT-00002"}
    assert scoring.invented_ids(["EVT-00002", "EVT-99999"], dataset) == ["EVT-99999"]


def test_numero_por_frontera_de_token_no_subcadena():
    assert scoring.number_in_answer("Hay 97 eventos críticos", 97, "eventos")
    assert not scoring.number_in_answer("Hay 1977 eventos", 97, "eventos")


def test_numero_formatos_es_en():
    for texto in ("1.234 registros", "1,234 registros", "1234 registros"):
        assert scoring.number_in_answer(texto, 1234, "registros")


def test_numero_ignora_ip_y_elige_el_mas_cercano_a_la_unidad():
    texto = "La IP 10.10.97.1 originó 68 eventos desde el puerto 18"
    assert scoring.number_in_answer(texto, 68, "eventos")
    assert not scoring.number_in_answer(texto, 97, "eventos")
    assert not scoring.number_in_answer(texto, 18, "eventos")


def test_numero_sin_unidades_acepta_cualquier_coincidencia():
    assert scoring.number_in_answer("Total: 102", 102)


def test_precision_conjunto_calculado_a_mano():
    # esperados {A,B,C,D}; citados A,B,X → 2/3
    assert scoring.precision_at_k(["EVT-A", "EVT-B", "EVT-X"],
                                  {"EVT-A", "EVT-B", "EVT-C", "EVT-D"}) == 0.67


def test_precision_sin_citados_es_cero():
    assert scoring.precision_at_k([], {"EVT-A"}) == 0.0


def test_hit_requiere_al_menos_uno():
    assert scoring.hit_at_k(["EVT-X", "EVT-B"], {"EVT-A", "EVT-B"})
    assert not scoring.hit_at_k(["EVT-X"], {"EVT-A", "EVT-B"})


def test_is_wide_deriva_de_la_regla_2k():
    ancho = {f"EVT-{i:05d}" for i in range(91)}   # DC-01: 91 eventos
    estrecho = {f"EVT-{i:05d}" for i in range(6)}
    assert scoring.is_wide(ancho)      # 91 > 16 → precisión@k
    assert not scoring.is_wide(estrecho)


def test_denial_acepta_solo_si_niega_y_cero_ids():
    assert scoring.denial_ok("No encontré registros de terremotos entre tus eventos.", [])
    assert not scoring.denial_ok("No encontré nada; revise EVT-00007.", ["EVT-00007"])
    assert not scoring.denial_ok("El terremoto afectó al host DC-01.", [])


def test_macro_promedia_categorias_sin_pesar_por_n():
    assert scoring.macro_average({"a": 1.0, "b": 0.0, "c": 0.5}) == 0.5


def test_regex_scoring_coincide_con_ai_service():
    from ai_service import _cited_log_ids
    muestra = "IDs: EVT-00001, INC_2024_55, EVT-99999. Nada más."
    assert scoring.cited_ids(muestra) == _cited_log_ids(muestra)
