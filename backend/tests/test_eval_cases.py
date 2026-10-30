"""Verdad calculable del harness: los 24 casos contra el Excel real.

Cada `expected` es un cálculo pandas sobre `fixtures/sample.xlsx` (los valores
97/102/91/68/9 se verificaron contra el archivo real de 715 filas): si alguien
cambia un caso, estos tests fallan antes de que una corrida mienta.

Ampliación (tarea 6): los 7 `REPORT_CASES` del informe ejecutivo. Su verdad
(`report_truth`) se re-verifica aquí con pandas hecho A MANO en el test, y los
`verifica` se ejercitan contra un payload correcto construido desde la propia
verdad y contra payloads con los 6 defectos conocidos de la nota.
"""
import asyncio
import copy
import re

import pandas as pd
import pytest

import ai_service
from ai_service import generate_report_narrative, is_aggregate_question
from eval.cases import CASES, REPORT_CASES, load_fixture, report_truth
from main import call_ai_service, normalize_level
from severity import LEVEL_LABELS, LEVEL_ORDER
from tests.conftest import COLUMNAS, DATASET_12_FILAS


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


def test_ningun_caso_no_agregado_enruta_a_agregacion():
    """Dirección negativa del ruteo: una consulta no-agregada que el enrutador
    clasifica como agregación NUNCA llega al LLM — `_aggregate_answer` devuelve
    un conteo sin Log_ID citados, y con `cited=[]` y un esperado estrecho no
    vacío, hit@k es False SIEMPRE, en ambas pasadas y con cualquier motor.

    Dos casos cayeron así («¿Qué eventos fallidos hay en WS-018?» e «Intentos
    de logon fallido del usuario admin»: «fallo»/«fallido» están en
    RESULT_MARKERS) y caparon filter_set al 71.4 % para siempre, deprimiendo
    el macro global sin que nada lo señalara. Se reformularon pidiendo FILAS
    (ROW_REQUEST_MARKERS), que el enrutador devuelve a la recuperación normal.
    """
    for c in CASES:
        if c.categoria != "aggregate":
            assert not is_aggregate_question(c.question), (
                f"{c.id} ({c.categoria}) enruta a agregación: jamás llega al "
                "LLM y su hit@k es False por construcción"
            )


def test_ids_de_caso_unicos():
    assert len({c.id for c in CASES}) == len(CASES) == 24


def test_categorias_conforme_al_plan():
    from collections import Counter

    conteo = Counter(c.categoria for c in CASES)
    assert dict(conteo) == {"aggregate": 5, "filter_set": 7, "refusal": 3,
                            "retrieval": 6, "evidence_qa": 3}


# ---------------------------------------------------------------------------
# Casos del informe ejecutivo (categoría report — tarea 6)
# ---------------------------------------------------------------------------
REPORT_IDS = [
    "rep-distribucion",
    "rep-atribucion-eventid",
    "rep-conclusiones-cifras",
    "rep-correlation-ip",
    "rep-hallazgos-estructura",
    "rep-grounding-ids",
    "rep-field-sources",
]

# Las 6 claves de field_sources (contrato estructural del informe, no datos).
CAMPOS_FS = ("executive_summary", "methodology", "findings", "correlation",
             "conclusions", "action_plan")


def _resultados_txt(df: pd.DataFrame) -> pd.Series:
    """Normalización INDEPENDIENTE del campo Result («404.0» → «404»), hecha
    a mano en el test para re-verificar que report_truth deriva del df."""
    return df["Result"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def test_report_truth_re_verificado_con_pandas(df):
    """report_truth recalculado A MANO con pandas aquí: si cases.py trajera
    una cifra escrita a mano o de otra fuente, esta comparación revienta."""
    truth = report_truth(df)

    assert truth["rows"] == len(df)
    niveles = {lvl: int(df["Severity"].map(normalize_level).eq(lvl).sum())
               for lvl in LEVEL_ORDER}
    assert truth["level_counts"] == niveles
    assert sum(truth["level_counts"].values()) == len(df)
    assert truth["highest"] == next(l for l in LEVEL_ORDER if niveles[l] > 0)
    assert set(truth["levels_present"]) == {l for l, n in niveles.items() if n > 0}

    assert truth["event_id_counts"] == {
        k: int(v) for k, v in
        df["Event_ID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        .value_counts().items()
    }
    assert truth["ips"] == {
        str(k): int(v)
        for k, v in df["Source_IP"].dropna().astype(str).value_counts().items()
    }
    res = _resultados_txt(df)
    assert truth["http_codes"] == {
        k: int(v) for k, v in res.value_counts().items()
        if re.fullmatch(r"[45]\d{2}", k)
    }
    for code, conteo in truth["http_codes"].items():
        sub = df["Severity"].map(normalize_level)[res == code]
        esperado = {lvl: int(sub.eq(lvl).sum()) for lvl in LEVEL_ORDER}
        assert truth["http_levels"][code] == {l: n for l, n in esperado.items() if n > 0}
        assert sum(truth["http_levels"][code].values()) == conteo

    assert truth["dataset_ids"] == set(df["Log_ID"].astype(str))


def test_report_truth_cruza_con_caso_existente(df):
    """Cruce independiente: el conteo global de 4625 en truth es coherente
    con el caso filter_set que filtra por ese mismo Event ID."""
    truth = report_truth(df)
    caso = next(c for c in CASES if c.id == "fs-admin-4625")
    assert len(caso.expected(df)) <= truth["event_id_counts"]["4625"]


def test_report_truth_sobre_dataset_12_filas():
    """Otra forma distinta del fixture: la verdad se recalcula del df dado,
    sin suponer cifras del sample.xlsx."""
    df12 = pd.DataFrame(DATASET_12_FILAS, columns=COLUMNAS)
    truth = report_truth(df12)
    assert truth["rows"] == len(df12)
    assert truth["level_counts"] == {
        lvl: int(df12["Severity"].map(normalize_level).eq(lvl).sum())
        for lvl in LEVEL_ORDER
    }
    assert sum(truth["level_counts"].values()) == len(df12)
    assert truth["event_id_counts"] == {
        str(k): int(v) for k, v in df12["Event_ID"].value_counts().items()
    }
    res = df12["Result"].astype(str)
    assert truth["http_codes"] == {
        k: int(v) for k, v in res.value_counts().items()
        if re.fullmatch(r"[45]\d{2}", k)
    }
    assert truth["ips"] == {str(k): int(v)
                            for k, v in df12["Source_IP"].value_counts().items()}
    assert truth["dataset_ids"] == set(df12["Log_ID"].astype(str))


def test_report_cases_los_7_ids_del_spec_y_su_cobertura():
    assert [c.id for c in REPORT_CASES] == REPORT_IDS
    assert len({c.id for c in REPORT_CASES}) == len(REPORT_CASES) == 7
    assert {c.id: c.cubre for c in REPORT_CASES} == {
        "rep-distribucion": "#3",
        "rep-atribucion-eventid": "#1",
        "rep-conclusiones-cifras": "#6",
        "rep-correlation-ip": "#3",
        "rep-hallazgos-estructura": "#2, #4, #5",
        "rep-grounding-ids": "#5",
        "rep-field-sources": "—",
    }
    for caso in REPORT_CASES:
        assert caso.descripcion and callable(caso.verifica)


def _payload_correcto(df: pd.DataFrame) -> dict:
    """Informe CORRECTO construido SOLO desde report_truth(df): ninguna cifra
    escrita a mano, así sirve igual para el fixture y para las 12 filas."""
    truth = report_truth(df)
    res = _resultados_txt(df)
    label = LEVEL_LABELS[truth["highest"]]
    distribucion = " · ".join(
        f"nivel {LEVEL_LABELS[l]}: {truth['level_counts'][l]}"
        for l in truth["levels_present"]
    )
    ip, ip_conteo = max(truth["ips"].items(), key=lambda par: par[1])
    findings = [{
        "id": "H-01",
        "title": "Distribución de registros por nivel",
        "level": truth["highest"],
        "explanation": f"Distribución completa: {distribucion}.",
        "evidence": [],
    }]
    if truth["http_codes"]:
        code = sorted(truth["http_codes"])[0]
        nivel_http = max(truth["http_levels"][code], key=LEVEL_ORDER.index)
        evidencias = [str(x) for x in df.loc[res == code, "Log_ID"].astype(str).head(2)]
        findings.append({
            "id": "H-02",
            "title": f"Respuestas HTTP {code} en los registros",
            "level": nivel_http,
            "explanation": f"Los registros con respuesta HTTP {code} requieren revisión.",
            "evidence": [{"log_id": e} for e in evidencias],
        })
    return {
        "executive_summary": (
            f"Se analizaron {truth['rows']} registros de seguridad. "
            f"El riesgo global es nivel {label}. Distribución — {distribucion}."
        ),
        "methodology": (
            "1) Importación del archivo. 2) Clasificación de severidad a niveles. "
            "3) Aplicación de reglas de sospecha."
        ),
        "correlation": (
            f"La IP de origen {ip} concentra {ip_conteo} registros de la muestra."
        ),
        "conclusions": (
            f"Riesgo global {label}. Total: {truth['rows']} registros analizados. "
            f"Distribución — {distribucion}."
        ),
        "findings": findings,
        "action_plan": [{"priority": "P1", "action": "Revisar los niveles elevados"}],
        "field_sources": {c: "local" for c in CAMPOS_FS},
    }


def _con(payload: dict, **cambios) -> dict:
    out = copy.deepcopy(payload)
    out.update({k: copy.deepcopy(v) for k, v in cambios.items()})
    return out


def _payloads_defectos(base: dict, df: pd.DataFrame) -> dict:
    """Los 6 defectos conocidos de la nota del ciclo (más la omisión de la
    distribución), derivados del payload correcto uno a uno."""
    truth = report_truth(df)

    # #1 · cifra reasignada a otro sustantivo (el «97 → 4625» histórico):
    # «N+1 … Event ID X» con N+1 ≠ conteo real derivado del df.
    x, real = sorted(truth["event_id_counts"].items())[0]
    atribucion = _con(base, conclusions=base["conclusions"] +
                      f" Se revisaron {real + 1} registros de Event ID {x}.")

    # #2 · hallazgos duplicados (mismo id y mismo título)
    duplicados = _con(base,
                      findings=base["findings"] + [copy.deepcopy(base["findings"][0])])

    # #5 · ids EVT-… en los hallazgos en vez de H-##
    ids_evt = _con(base, findings=[{**copy.deepcopy(f), "id": f"EVT-{i:05d}"}
                                   for i, f in enumerate(base["findings"], 1)])

    # #5 · evidencia con un Log_ID que no existe en el df (familia EVT)
    inventado = _con(base, findings=[
        {**copy.deepcopy(f), "evidence": [{"log_id": "EVT-99999"}]}
        for f in base["findings"]])

    # #4 · hallazgo HTTP con un nivel que las filas HTTP del df no tienen
    niveles_http = set().union(*[set(v) for v in truth["http_levels"].values()])
    absurdo = next(l for l in LEVEL_ORDER if l not in niveles_http)
    nivel_absurdo = _con(base, findings=[
        {**copy.deepcopy(f),
         **({"level": absurdo} if re.search(r"\b[45]\d{2}\b", f["title"]) else {})}
        for f in base["findings"]])

    # #3 · correlation sin una sola IP del df
    correlation_sin_ip = _con(
        base,
        correlation="No se observan correlaciones relevantes entre los activos "
                    "analizados en esta ventana de tiempo.")

    # #6 · conclusiones sin una sola cifra ni etiqueta de riesgo
    conclusiones_sin_cifras = _con(
        base,
        conclusions="El ambiente analizado presenta un riesgo aceptable que exige "
                    "monitoreo continuo del equipo de seguridad.")

    # Omisión (#3): resumen ejecutivo sin la distribución de niveles
    executive_sin_distribucion = _con(
        base,
        executive_summary=f"Se analizaron {truth['rows']} registros de seguridad "
                          "del archivo importado por el equipo.")

    return {
        "atribucion-cifra-mal-pegada": atribucion,
        "hallazgos-duplicados": duplicados,
        "ids-evt": ids_evt,
        "evidencia-inventada": inventado,
        "nivel-absurdo-http": nivel_absurdo,
        "correlation-sin-ip": correlation_sin_ip,
        "conclusiones-sin-cifras": conclusiones_sin_cifras,
        "executive-sin-distribucion": executive_sin_distribucion,
    }


def test_payload_correcto_pasa_los_7_casos(df):
    payload = _payload_correcto(df)
    for caso in REPORT_CASES:
        assert caso.verifica(payload, df), f"{caso.id} rechaza un informe correcto"


def test_payload_correcto_pasa_sobre_dataset_12_filas():
    """Data-driven de verdad: el mismo builder pasa con otro df cualquiera."""
    df12 = pd.DataFrame(DATASET_12_FILAS, columns=COLUMNAS)
    payload = _payload_correcto(df12)
    for caso in REPORT_CASES:
        assert caso.verifica(payload, df12), \
            f"{caso.id} rechaza un informe correcto (12 filas)"


def test_payloads_con_defectos_son_rechazados(df):
    """Cada defecto conocido cae en SU caso (los 6 de la nota + la omisión
    de distribución). Los ids inventados suponen la familia EVT del
    fixture, por eso este test corre sobre el sample.xlsx."""
    base = _payload_correcto(df)
    defectos = _payloads_defectos(base, df)
    esperado = {
        "atribucion-cifra-mal-pegada": "rep-atribucion-eventid",
        "hallazgos-duplicados": "rep-hallazgos-estructura",
        "ids-evt": "rep-hallazgos-estructura",
        "evidencia-inventada": "rep-grounding-ids",
        "nivel-absurdo-http": "rep-hallazgos-estructura",
        "correlation-sin-ip": "rep-correlation-ip",
        "conclusiones-sin-cifras": "rep-conclusiones-cifras",
        "executive-sin-distribucion": "rep-distribucion",
    }
    assert set(defectos) == set(esperado)
    for nombre, payload in defectos.items():
        caso = next(c for c in REPORT_CASES if c.id == esperado[nombre])
        assert not caso.verifica(payload, df), \
            f"el defecto «{nombre}» pasó {caso.id}"


def test_fallback_determinista_pasa_los_7_casos(df, monkeypatch):
    """La pasada LLM_OFF del harness puntúa con el informe del fallback
    determinista: ese payload TIENE que pasar los 7 casos (criterio de
    aceptación de la tarea 8, adelantado aquí)."""
    original = ai_service.ai_config.get
    monkeypatch.setattr(ai_service.ai_config, "get",
                        lambda: {**original(), "enabled": False})
    work = df.copy()
    if "Timestamp" in work.columns:
        work["Timestamp"] = pd.to_datetime(work["Timestamp"], errors="coerce")
    payload = asyncio.run(generate_report_narrative(call_ai_service(work)))
    for caso in REPORT_CASES:
        assert caso.verifica(payload, df), \
            f"{caso.id} rechaza el informe del fallback determinista"
