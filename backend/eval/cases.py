"""cases.py — los 24 casos del harness con su verdad calculable.

Cada caso define `expected` como un cálculo pandas sobre la muestra fija
(`fixtures/sample.xlsx`, 715 filas del dataset real del bootcamp): NUNCA una
lista escrita a mano. Si el archivo cambia, la verdad cambia con él y los
tests de test_eval_cases.py la re-verifican antes de cualquier corrida.

Categorías:
  aggregate  — «¿cuántos...?» → entero exacto (scoring.number_in_answer).
  filter_set — «¿qué eventos...?» → set de Log_ID (precision@k o hit@k si estrecha).
  refusal    — consulta sin respuesta en los datos → el sistema debe negar
               con cero Log_ID citados (scoring.denial_ok).
  retrieval  — recuperación directa sin LLM (rag_store.query_events): mide el
               retriever por sí mismo, sin la redacción del modelo por medio.
  evidence_qa— respuesta del sistema citando IDs → precision@k sobre los
               relevantes del dataset (preguntas anchas: más de 2*k relevantes).

Además, `REPORT_CASES` (tarea 6): los 7 casos de la categoría `report` del
informe ejecutivo. Su verdad sale de `report_truth(df)` — derivada SOLO del
DataFrame con pandas — y sus `verifica` usan las funciones puras de
`eval/scoring.py`, implementadas de forma INDEPENDIENTE del validador de
`ai_service` para que ambos se crucen (spec §4: «el eval re-detecta los
mismos fallos por otro camino»). Este módulo sigue sin importar FastAPI,
Ollama ni ChromaDB.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

try:
    from backend.severity import LEVEL_LABELS, LEVEL_ORDER, normalize_level
except ImportError:  # pragma: no cover
    from severity import LEVEL_LABELS, LEVEL_ORDER, normalize_level

try:
    from eval import scoring
except ImportError:  # pragma: no cover – módulo suelto
    from backend.eval import scoring

FIXTURE = Path(__file__).parent / "fixtures" / "sample.xlsx"

K = 8   # n_results por defecto del endpoint /ask


def load_fixture() -> pd.DataFrame:
    """La muestra fija versionada (única fuente de verdad del eval)."""
    return pd.read_excel(FIXTURE)


@dataclass(frozen=True)
class Case:
    id: str
    categoria: str
    question: str
    expected: Callable[[pd.DataFrame], Any]
    unidades: str | None = None


def _ids(df: pd.DataFrame, mask) -> set[str]:
    return set(df.loc[mask, "Log_ID"].astype(str))


def _externa(df: pd.DataFrame) -> pd.Series:
    return ~df["Source_IP"].astype(str).str.startswith("10.")


def _critico(df: pd.DataFrame) -> pd.Series:
    return df["Severity"].map(normalize_level).eq("critico")


CASES: list[Case] = [
    # --- aggregate: la verdad es un entero del propio dataset ----------------
    Case("agg-criticos", "aggregate", "¿Cuántos eventos críticos hay?",
         lambda df: int(_critico(df).sum()), unidades="eventos"),
    Case("agg-fallos", "aggregate", "¿Cuántos intentos fallidos hay?",
         lambda df: int(df["Result"].astype(str).eq("Failure").sum()), unidades="intentos"),
    Case("agg-dc01", "aggregate", "¿Cuántos eventos hay en el host DC-01?",
         lambda df: int(df["Hostname"].astype(str).eq("DC-01").sum()), unidades="eventos"),
    Case("agg-externos", "aggregate", "¿Cuántos eventos vienen de IPs externas?",
         lambda df: int(_externa(df).sum()), unidades="eventos"),
    Case("agg-http500", "aggregate", "¿Cuántas peticiones devolvieron error 500?",
         lambda df: int(df["Result"].astype(str).eq("500").sum()), unidades="peticiones"),
    # --- filter_set: verdad = conjunto de Log_ID del dataset -----------------
    # Nota: las dos primeras llevan «muéstrame…/listar» a propósito. Sin ese
    # marcador de petición de filas (ROW_REQUEST_MARKERS) el enrutador clasifica
    # «fallidos/fallido» (RESULT_MARKERS) como agregación y el caso NUNCA llega
    # al LLM: su hit@k sería False por construcción y caparía filter_set para
    # siempre. Lo fija test_ningun_caso_no_agregado_enruta_a_agregacion.
    Case("fs-fail-ws018", "filter_set", "Muéstrame los eventos fallidos en WS-018",
         lambda df: _ids(df, df["Result"].astype(str).eq("Failure")
                         & df["Hostname"].astype(str).eq("WS-018"))),
    Case("fs-tarea-4698", "filter_set", "¿Se creó alguna tarea programada?",
         lambda df: _ids(df, df["Event_ID"].eq(4698))),
    Case("fs-svc-7045", "filter_set", "¿Se instaló algún servicio nuevo en APP-SRV01?",
         lambda df: _ids(df, df["Event_ID"].eq(7045)
                         & df["Hostname"].astype(str).eq("APP-SRV01"))),
    Case("fs-admin-4625", "filter_set", "Listar intentos de logon fallido del usuario admin",
         lambda df: _ids(df, df["Event_ID"].eq(4625)
                         & df["User"].astype(str).eq(r"CORP\admin"))),
    Case("fs-crit-externos", "filter_set", "Eventos críticos desde IPs externas",
         lambda df: _ids(df, _critico(df) & _externa(df))),
    Case("fs-iis-errores", "filter_set", "Errores del servidor web",
         lambda df: _ids(df, df["Log_Source"].astype(str).str.contains("IIS", case=False)
                         & df["Result"].astype(str).isin(["500", "404"]))),
    Case("fs-priv-4672", "filter_set", "¿Hubo asignación de privilegios especiales?",
         lambda df: _ids(df, df["Event_ID"].eq(4672))),
    # --- refusal: no existe en los datos; el sistema debe negar ---------------
    # (expected -> True: el runner exige denial_ok ∧ cero IDs citados)
    Case("ref-terremoto", "refusal", "¿Hubo un terremoto en la oficina?", lambda df: True),
    Case("ref-malware", "refusal", "¿Hay muestras de malware en los registros?", lambda df: True),
    Case("ref-sqli", "refusal",
         "¿Hubo un ataque de inyección SQL contra la base de datos?", lambda df: True),
    # --- retrieval: puntuación directa del retriever (sin LLM) ---------------
    Case("ret-fuerza-bruta", "retrieval", "Fuerza bruta contra el controlador DC-01",
         lambda df: _ids(df, df["Event_ID"].eq(4625) & df["Hostname"].astype(str).eq("DC-01"))),
    Case("ret-ip-externa", "retrieval", "Eventos desde la IP 203.0.113.74",
         lambda df: _ids(df, df["Source_IP"].astype(str).eq("203.0.113.74"))),
    Case("ret-cierre-4634", "retrieval", "Cierres de sesión de cuenta",
         lambda df: _ids(df, df["Event_ID"].eq(4634))),
    Case("ret-proxy", "retrieval", "¿Qué peticiones permitió el proxy?",
         lambda df: _ids(df, df["Log_Source"].astype(str).str.contains("proxy", case=False)
                         & df["Result"].astype(str).eq("Allowed"))),
    Case("ret-powershell", "retrieval", "Actividad de PowerShell en los registros",
         lambda df: _ids(df, df["Log_Source"].astype(str).str.contains("powershell",
                                                                        case=False))),
    Case("ret-7045", "retrieval", "Servicio instalado con Event ID 7045",
         lambda df: _ids(df, df["Event_ID"].eq(7045))),
    # --- evidence_qa: el sistema cita IDs → precision@k sobre relevantes -----
    Case("eq-dc01", "evidence_qa", "¿Qué incidentes hay en DC-01?",
         lambda df: _ids(df, df["Hostname"].astype(str).eq("DC-01"))),
    Case("eq-jlopez", "evidence_qa", "¿Qué hizo el usuario CORP\\jlopez?",
         lambda df: _ids(df, df["User"].astype(str).eq(r"CORP\jlopez"))),
    Case("eq-ip-ext-34", "evidence_qa", "¿Qué hizo la IP externa 203.0.113.74?",
         lambda df: _ids(df, df["Source_IP"].astype(str).eq("203.0.113.74"))),
]


# ---------------------------------------------------------------------------
# Informe ejecutivo: verdad derivada del df y los 7 casos `report` (tarea 6)
# ---------------------------------------------------------------------------
def _code_str(value: Any) -> str:
    """«4625.0» → «4625» (celdas vacías dejan floats) y « 404 » → «404».

    Misma SEMÁNTICA que el normalizador de Result de producción
    (`ai_service._result_text`), reimplementada aquí a mano: eval no importa
    al producto. La versión del test (`_resultados_txt`) es una tercera
    implementación independiente que re-verifica este criterio.
    """
    text = str(value).strip()
    if re.fullmatch(r"-?\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def report_truth(df: pd.DataFrame) -> dict:
    """Verdad del informe derivada ÚNICAMENTE del DataFrame (cero cifras a mano).

    - `level_counts`: `Severity` normalizado con `severity.normalize_level`,
      el módulo NEUTRO (solo stdlib, sin FastAPI/Ollama/ChromaDB) que el
      propio backend re-exporta desde `main.normalize_level` — el eval y el
      producto comparten el MISMO mapping, no un duplicado desincronizable.
    - `event_id_counts`: `value_counts()` COMPLETO de `Event_ID` (la regla de
      atribución puede citar cualquier id, no solo un top-N).
    - `http_codes`/`http_levels`: valores del campo `Result` que casan el
      patrón de código HTTP 4xx/5xx (`[45]\\d{2}`) — el patrón se deriva de
      los datos, nunca ids escritos a mano — y sus niveles reales por fila.
    - `ips`: `Source_IP` con sus conteos; `dataset_ids`: Log_ID para el
      grounding; `rows`, `levels_present` y `highest` (primer nivel del enum
      con conteo > 0, el mismo criterio que el riesgo global del backend).
    """
    levels = df["Severity"].map(normalize_level)
    level_counts = {lvl: int(levels.eq(lvl).sum()) for lvl in LEVEL_ORDER}
    levels_present = [lvl for lvl in LEVEL_ORDER if level_counts[lvl] > 0]

    event_id_counts = {
        str(key): int(count)
        for key, count in df["Event_ID"].map(_code_str).value_counts().items()
    }

    resultados = df["Result"].map(_code_str)
    http_codes: dict[str, int] = {}
    http_levels: dict[str, dict[str, int]] = {}
    for raw, count in resultados.value_counts().items():
        code = str(raw)
        if not re.fullmatch(r"[45]\d{2}", code):
            continue
        sub = levels[resultados == code]
        http_codes[code] = int(count)
        http_levels[code] = {
            lvl: int(sub.eq(lvl).sum()) for lvl in LEVEL_ORDER
            if int(sub.eq(lvl).sum()) > 0
        }

    ips = {
        str(key): int(count)
        for key, count in df["Source_IP"].dropna().astype(str).value_counts().items()
    }

    return {
        "rows": int(len(df)),
        "level_counts": level_counts,
        "levels_present": levels_present,
        "highest": levels_present[0] if levels_present else None,
        "event_id_counts": event_id_counts,
        "http_codes": http_codes,
        "http_levels": http_levels,
        "ips": ips,
        "dataset_ids": set(df["Log_ID"].astype(str)),
    }


@dataclass(frozen=True)
class ReportCase:
    id: str
    descripcion: str
    verifica: Callable[[dict, pd.DataFrame], bool]  # payload del informe + df
    cubre: str  # problema #1..#6 de la nota del ciclo («—» = contrato)


# Los 4 campos de prosa del informe (los demás los genera el sistema).
_PROSE_FIELDS = ("executive_summary", "methodology", "correlation", "conclusions")


def _narrativa(payload: dict) -> str:
    """Texto de los 4 campos de prosa concatenado, para escanear parejas y IDs."""
    return " ".join(str(payload.get(campo) or "") for campo in _PROSE_FIELDS)


def _verificar_distribucion(payload: dict, df: pd.DataFrame) -> bool:
    """#3: executive_summary con el total de registros y TODOS los niveles
    presentes nombrados con su conteo real (omisión del LLM sin distribución)."""
    truth = report_truth(df)
    text = str(payload.get("executive_summary") or "")
    if not scoring.number_in_answer(text, truth["rows"], "registros"):
        return False
    return all(
        scoring.level_count_near(text, LEVEL_LABELS[lvl], truth["level_counts"][lvl])
        for lvl in truth["levels_present"]
    )


def _verificar_atribucion(payload: dict, df: pd.DataFrame) -> bool:
    """#1: cero parejas «N … Event ID X» con N ≠ conteo real del df (o id
    ausente) — el 97→4625, detectado con `eventid_pairs_bad` (implementación
    propia, independiente de la regla B del validador)."""
    truth = report_truth(df)
    return not scoring.eventid_pairs_bad(_narrativa(payload), truth["event_id_counts"])


def _verificar_conclusiones(payload: dict, df: pd.DataFrame) -> bool:
    """#6: conclusions con etiqueta de riesgo, total de registros y
    distribución completa — ni prosa sin cifras."""
    truth = report_truth(df)
    text = str(payload.get("conclusions") or "")
    if truth["highest"] and scoring.required_tokens(
            text, [LEVEL_LABELS[truth["highest"]]]):
        return False
    if truth["rows"] and not scoring.number_in_answer(text, truth["rows"], "registros"):
        return False
    return all(
        scoring.level_count_near(text, LEVEL_LABELS[lvl], truth["level_counts"][lvl])
        for lvl in truth["levels_present"]
    )


def _verificar_correlation(payload: dict, df: pd.DataFrame) -> bool:
    """#3: correlation con al menos UNA IP que exista en el df (el LLM que
    redacta correlation sin una sola IP cae aquí)."""
    truth = report_truth(df)
    if not truth["ips"]:
        return True  # sin IPs en el archivo no hay nada que exigir
    return scoring.ip_cited(str(payload.get("correlation") or ""), list(truth["ips"]))


def _verificar_hallazgos(payload: dict, df: pd.DataFrame) -> bool:
    """#2, #4 y #5: findings con ids `H-##` únicos, títulos sin repetir,
    niveles del enum y — si el tema del hallazgo es un código HTTP que existe
    en `Result` — el nivel que esas filas HTTP realmente tienen (según
    `normalize_level`, derivado del df: «HTTP como bajo» cuando las filas son
    críticas cae aquí)."""
    truth = report_truth(df)
    findings = payload.get("findings")
    if not isinstance(findings, list) or not findings:
        return False
    ids: list[str] = []
    titulos: list[str] = []
    for hallazgo in findings:
        if not isinstance(hallazgo, dict):
            return False
        fid = str(hallazgo.get("id") or "")
        if not re.fullmatch(r"H-\d{2}", fid):
            return False
        ids.append(fid)
        titulo = " ".join(str(hallazgo.get("title") or "").split()).lower()
        if not titulo:
            return False
        titulos.append(titulo)
        if hallazgo.get("level") not in LEVEL_ORDER:
            return False
        # Tema HTTP: algún código 4xx/5xx del campo Result citado en el
        # hallazgo. Un id tipo «4624» no está en http_codes, así que los
        # Event ID de Windows no disparan esta comprobación por accidente.
        texto = " ".join([
            str(hallazgo.get("title") or ""),
            str(hallazgo.get("explanation") or ""),
            str(hallazgo.get("evidence") or ""),
        ])
        codigos = {tok for tok in re.findall(r"\b[45]\d{2}\b", texto)
                   if tok in truth["http_codes"]}
        if codigos:
            niveles_http = set().union(
                *[set(truth["http_levels"].get(c, ())) for c in codigos])
            if niveles_http and hallazgo.get("level") not in niveles_http:
                return False
    if len(set(ids)) != len(ids) or len(set(titulos)) != len(titulos):
        return False
    return True


def _verificar_grounding(payload: dict, df: pd.DataFrame) -> bool:
    """#5: cero Log_ID inventados en la narrativa y en las evidencias de los
    findings (`invented_ids` = ∅ sobre el set de Log_ID del df)."""
    truth = report_truth(df)
    partes = [_narrativa(payload)]
    findings = payload.get("findings")
    if isinstance(findings, list):
        for hallazgo in findings:
            if not isinstance(hallazgo, dict):
                continue
            partes.append(str(hallazgo.get("title") or ""))
            partes.append(str(hallazgo.get("explanation") or ""))
            for item in hallazgo.get("evidence") or []:
                if isinstance(item, dict):
                    partes.append(str(item.get("log_id") or ""))
                else:
                    partes.append(str(item))
    cited = scoring.cited_ids(" ".join(partes))
    return not scoring.invented_ids(cited, truth["dataset_ids"])


def _verificar_field_sources(payload: dict, df: pd.DataFrame) -> bool:
    """Contrato: las 6 claves de `field_sources` con valor «llm»/«local».

    No deriva nada del df — es el registro de procedencia de cada campo.
    """
    return scoring.field_sources_ok(payload)


REPORT_CASES: list[ReportCase] = [
    ReportCase(
        id="rep-distribucion",
        descripcion="executive_summary con total de registros y distribución "
                    "completa de niveles con sus conteos reales",
        verifica=_verificar_distribucion,
        cubre="#3",
    ),
    ReportCase(
        id="rep-atribucion-eventid",
        descripcion="toda pareja «N … Event ID X» del texto con N igual al "
                    "conteo global real de X en el df",
        verifica=_verificar_atribucion,
        cubre="#1",
    ),
    ReportCase(
        id="rep-conclusiones-cifras",
        descripcion="conclusions con etiqueta de riesgo, total de registros y "
                    "distribución completa",
        verifica=_verificar_conclusiones,
        cubre="#6",
    ),
    ReportCase(
        id="rep-correlation-ip",
        descripcion="correlation con al menos una IP que exista en el df",
        verifica=_verificar_correlation,
        cubre="#3",
    ),
    ReportCase(
        id="rep-hallazgos-estructura",
        descripcion="findings con ids H-## únicos, títulos únicos, niveles del "
                    "enum y hallazgo HTTP con el nivel real de sus filas",
        verifica=_verificar_hallazgos,
        cubre="#2, #4, #5",
    ),
    ReportCase(
        id="rep-grounding-ids",
        descripcion="cero Log_ID inventados en la narrativa ni en las "
                    "evidencias de los findings",
        verifica=_verificar_grounding,
        cubre="#5",
    ),
    ReportCase(
        id="rep-field-sources",
        descripcion="las 6 claves de field_sources presentes con valor «llm» "
                    "o «local»",
        verifica=_verificar_field_sources,
        cubre="—",
    ),
]
