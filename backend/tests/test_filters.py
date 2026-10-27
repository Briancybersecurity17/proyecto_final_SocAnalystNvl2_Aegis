from types import SimpleNamespace

import pandas as pd
import pytest
from starlette.datastructures import QueryParams
from filters import FilterParams, apply_filters, detect_facets, parse_filters

@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame({
        "Log_ID": [1, 2, 3, 4, 5],
        "Timestamp": pd.to_datetime([
            "2026-01-10 08:00", "2026-01-10 09:30", "2026-01-11 10:00",
            "2026-01-12 08:00", "2026-01-12 09:00",
        ]),
        "Hostname": ["DC-01", "WS-018", "DC-01", "WS-018", "WEB-01"],
        "User": ["jsmith", "jsmith", "admin", "admin", "anon"],
        "Result": ["Success", "Failure", "Failure", "Success", "Success"],
        "Severity": ["High", "Critical", "Warning", "Informational", "Medium"],
        "Event_Description": ["cuenta iniciada" * 4, "fallo de acceso" * 4, "bloqueo" * 8,
                              "servicio instalado" * 3, "peticion http" * 4],
        "_level": ["alto", "critico", "critico", "bajo", "medio"],
        "_reason": [""] * 5,
        "_suspicious": [True, True, True, False, False],
    })

def vacio() -> FilterParams:
    return FilterParams()

def test_sin_filtros_devuelve_todo(df):
    assert len(apply_filters(df, vacio())) == 5

def test_filtro_por_nivel(df):
    out = apply_filters(df, FilterParams(level="critico"))
    assert sorted(out["Log_ID"]) == [2, 3]

def test_filtro_por_sospechosos(df):
    out = apply_filters(df, FilterParams(suspicious=True))
    assert sorted(out["Log_ID"]) == [1, 2, 3]

def test_filtro_por_result_insensible_a_mayusculas(df):
    out = apply_filters(df, FilterParams(result="failure"))
    assert sorted(out["Log_ID"]) == [2, 3]

def test_filtro_ventana_temporal_inclusiva(df):
    out = apply_filters(df, FilterParams(
        from_dt=pd.Timestamp("2026-01-10 09:00"),
        to_dt=pd.Timestamp("2026-01-11 23:59")))
    assert sorted(out["Log_ID"]) == [2, 3]

def test_q_busca_solo_en_columnas_originales(df):
    out = apply_filters(df, FilterParams(q="alto"))   # "alto" está en _level, no en los datos
    assert len(out) == 0

def test_q_encuentra_texto_de_una_columna_original(df):
    out = apply_filters(df, FilterParams(q="web-01"))
    assert list(out["Log_ID"]) == [5]

def test_dos_facetas_se_combinan_con_and(df):
    out = apply_filters(df, FilterParams(facets={"Hostname": ["WS-018"], "User": ["admin"]}))
    assert list(out["Log_ID"]) == [4]

def test_valores_de_una_misma_faceta_se_combinan_con_or(df):
    out = apply_filters(df, FilterParams(facets={"Hostname": ["DC-01", "WEB-01"]}))
    assert sorted(out["Log_ID"]) == [1, 3, 5]

def test_detect_facets_incluye_columnas_categoricas(df):
    nombres = [f.name for f in detect_facets(df)]
    assert {"Hostname", "User", "Result"} <= set(nombres)
    assert "Timestamp" not in nombres and "Log_ID" not in nombres

def test_detect_facets_descarta_texto_largo_y_log_id(df):
    nombres = [f.name for f in detect_facets(df)]
    assert "Event_Description" not in nombres   # >40 chars de media

def test_detect_facets_incluye_criticidad_derivada(df):
    nombres = [f.name for f in detect_facets(df)]
    assert "_level" in nombres and "_suspicious" in nombres

def test_detect_facets_trae_valores_con_conteo(df):
    campo = next(f for f in detect_facets(df) if f.name == "Hostname")
    assert campo.distinct == 3
    assert {"value": "DC-01", "count": 2} in campo.values   # sin depender del desempate

def test_detect_facets_descarta_columna_solo_vacia():
    datos = pd.DataFrame({
        "Hostname": ["DC-01", "WS-018", "WEB-01"],
        "Comentario": ["", "   ", float("nan")],   # solo NaN/espacios: nada que filtrar
        "_level": ["alto", "bajo", "medio"],
        "_suspicious": [True, False, True],
    })
    nombres = [f.name for f in detect_facets(datos)]
    assert "Comentario" not in nombres
    assert "Hostname" in nombres   # el resto de reglas sigue pasando

def test_faceta_sobre_columna_inexistente_se_ignora(df):
    out = apply_filters(df, FilterParams(facets={"Inexistente": ["x"]}))
    assert len(out) == 5   # la faceta se descarta en silencio, sin restringir (spec §8)


def _peticion(query: str) -> SimpleNamespace:
    """Stub mínimo de Request: a parse_filters solo le llega query_params."""
    return SimpleNamespace(query_params=QueryParams(query))


@pytest.fixture
def df_con_columna_page() -> pd.DataFrame:
    """Excel con una columna llamada literalmente «page» (el caso de R17)."""
    return pd.DataFrame({
        "page": ["1", "2", "1", "2", "1"],
        "Hostname": ["DC-01", "WS-018", "DC-01", "WS-018", "WEB-01"],
    })


def test_parametros_de_paginacion_no_son_faceta(df_con_columna_page):
    # R17: /rows manda page/page_size en CADA petición (la UI los envía
    # siempre). Con `reserved`, esos nombres se ignoran por completo: aunque
    # el Excel tenga una columna «page», la tabla no se filtra en silencio.
    stub = _peticion("page=2&page_size=50&Hostname=DC-01")
    params = parse_filters(stub, df_con_columna_page, reserved={"page", "page_size"})
    assert "page" not in params.facets and "page_size" not in params.facets
    assert params.facets == {"Hostname": ["DC-01"]}   # las facetas reales siguen
    # parse_filters no consume ni altera la query: la paginación sigue
    # leyéndose igual desde request.query_params en el endpoint.
    assert stub.query_params.get("page") == "2"


def test_sin_reserved_la_paginacion_sigue_puediendo_ser_faceta(df_con_columna_page):
    # La otra mitad de R17: /summary y /facets NO reservan nada, así que un
    # parámetro «page» sigue comportándose como la faceta que es allí.
    params = parse_filters(_peticion("page=2"), df_con_columna_page)
    assert params.facets == {"page": ["2"]}


def test_ventana_to_fin_de_tramo_cubre_el_dia_completo():
    df = pd.DataFrame({
        "Log_ID": [1, 2, 3, 4],
        "Timestamp": pd.to_datetime([
            "2026-01-12 00:00:00",
            "2026-01-12 12:00:00",
            "2026-01-12 23:59:59.999",
            "2026-01-13 00:00:00",
        ], format="mixed"),
    })
    out = apply_filters(
        df,
        FilterParams(
            from_dt=pd.Timestamp("2026-01-12"),
            to_dt=pd.Timestamp("2026-01-12 23:59:59.999"),
        ),
    )
    assert sorted(out["Log_ID"].tolist()) == [1, 2, 3]

