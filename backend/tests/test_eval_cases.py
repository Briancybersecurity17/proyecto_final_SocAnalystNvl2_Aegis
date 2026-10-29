"""Verdad calculable del harness: los 24 casos contra el Excel real.

Cada `expected` es un cálculo pandas sobre `fixtures/sample.xlsx` (los valores
97/102/91/68/9 se verificaron contra el archivo real de 715 filas): si alguien
cambia un caso, estos tests fallan antes de que una corrida mienta.
"""
import pandas as pd
import pytest

from ai_service import is_aggregate_question
from eval.cases import CASES, load_fixture
from main import normalize_level


@pytest.fixture(scope="module")
def df():
    return load_fixture()


def test_fixture_tiene_715_filas_y_columnas_reales(df):
    assert len(df) == 715
    for col in ("Log_ID", "Hostname", "Severity", "Result", "Event_ID", "Source_IP"):
        assert col in df.columns


def test_ids_unicos():
    ids = load_fixture()["Log_ID"]
    assert ids.is_unique


def test_agregados_calculan_los_valores_reales(df):
    por_id = {c.id: c.expected(df) for c in CASES if c.categoria == "aggregate"}
    assert por_id["agg-criticos"] == 97      # Severity=Warning → critico (SEVERITY_TO_LEVEL)
    assert por_id["agg-fallos"] == 102       # Result=Failure
    assert por_id["agg-dc01"] == 91          # Hostname=DC-01 (¡no DC01: hay 30!)
    assert por_id["agg-externos"] == 68      # fuera de 10.10.0.0/16
    assert por_id["agg-http500"] == 9        # Result="500" (string, no entero)


def test_agregados_usan_normalize_level(df):
    """Los críticos se cuentan con la MISMA función que usa el backend."""
    esperados = int(df["Severity"].map(normalize_level).eq("critico").sum())
    por_id = {c.id: c.expected(df) for c in CASES if c.categoria == "aggregate"}
    assert por_id["agg-criticos"] == esperados


def test_todo_caso_de_conjunto_devuelve_ids_del_dataset(df):
    ids = set(df["Log_ID"])
    for c in CASES:
        if c.categoria in ("filter_set", "retrieval", "evidence_qa"):
            esperado = c.expected(df)
            assert esperado, f"{c.id}: conjunto vacío"
            assert esperado <= ids, f"{c.id}: ids fuera del dataset"


def test_refusal_es_bool(df):
    for c in CASES:
        if c.categoria == "refusal":
            assert c.expected(df) is True


def test_los_agregados_son_preguntas_de_agregacion():
    for c in CASES:
        if c.categoria == "aggregate":
            assert is_aggregate_question(c.question), f"{c.id} no enruta al determinista"


def test_ids_de_caso_unicos():
    assert len({c.id for c in CASES}) == len(CASES) == 24


def test_categorias_conforme_al_plan():
    from collections import Counter

    conteo = Counter(c.categoria for c in CASES)
    assert dict(conteo) == {"aggregate": 5, "filter_set": 7, "refusal": 3,
                            "retrieval": 6, "evidence_qa": 3}
