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


def test_invented_ids_ignora_tokens_de_otra_familia():
    """La regex casa «WS-018» (host) igual que «EVT-018» (Log_ID): solo
    cuentan los tokens de la MISMA familia que los IDs reales del dataset,
    igual que filtra el guard _grounding_ok de producción."""
    dataset = {"EVT-00001", "EVT-00002"}
    assert scoring.invented_ids(["WS-018", "EVT-00002"], dataset) == []
    assert scoring.invented_ids(["WS-018"], dataset) == []


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


def test_precision_cuenta_cada_id_una_sola_vez():
    """Un ID repetido NO es más precisión: «EVT-1, EVT-1, EVT-1» es una sola
    evidencia correcta, no tres. La deduplicación preserva el orden para que
    hit_at_k y la impresión del informe no cambien."""
    esperados = {"EVT-A", "EVT-B"}
    assert scoring.precision_at_k(["EVT-A", "EVT-A", "EVT-A"], esperados) == 1.0
    assert scoring.precision_at_k(["EVT-A", "EVT-X", "EVT-X"], esperados) == 0.5


def test_fecha_no_cuenta_como_numero():
    """El lookaround de [\d.] descarta IPs pero NO fechas («2026-01-10» tiene
    guiones): esperar 2026 no debe aceptar la fecha. Solo la parte de la fecha
    pegada al resto del número la descarta, por eso el test es contra el
    número AÑO completo, que es el que el modelo citaría como conteo."""
    assert not scoring.number_in_answer("Ocurrió el 2026-01-10", 2026)
    assert scoring.number_in_answer("Ocurrieron 2026 eventos", 2026)


def test_fecha_con_barra_no_cuenta_como_numero():
    """Mismo caso con el separador «/» (formato dd/mm/aaaa): sin el / en el
    lookaround, «31/12/2025» casaba el 31 como número suelto y un agregado
    esperando 31 acertaba por una fecha (minor 9)."""
    assert not scoring.number_in_answer("Ocurrió el 31/12/2025", 31)
    assert not scoring.number_in_answer("Rango: 12/07/2025 al 31/12/2025", 12)
    assert scoring.number_in_answer("Ocurrieron 31 eventos", 31)


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
