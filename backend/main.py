import uuid
import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import date, datetime
from io import BytesIO
from typing import Any, Dict, List, Tuple

import pandas as pd
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# Logs de la API y del índice RAG en la consola del backend
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("soc.backend")

# Tareas de indexación semántica en curso, por análisis. Evita que dos
# peticiones (o la subida + una pregunta) indexen el mismo análisis a la vez.
_INDEX_TASKS: Dict[str, "asyncio.Task"] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Precarga el índice semántico sin bloquear el arranque.

    Cargar el modelo de embeddings cuesta unos segundos; se lanza en un hilo
    mientras el usuario todavía está en el Dashboard, para que la primera
    consulta del asistente responda ya con el modelo listo. Si falla (p. ej.
    sin red la primera vez), rag_store.warmup() permite reintentar después.
    """
    asyncio.create_task(asyncio.to_thread(rag_store.warmup))
    asyncio.create_task(asyncio.to_thread(kb_store.warmup))
    yield

# El servicio de IA se importa como paquete (uvicorn backend.main:app) o como
# módulo suelto (test_backend.py añade /backend al sys.path).
try:
    from backend.ai_service import (
        generate_report_narrative,
        generate_data_insights,
        answer_question,
        is_aggregate_question,
    )
    from backend import rag_store, kb_store
except ImportError:  # pragma: no cover
    from ai_service import (
        generate_report_narrative,
        generate_data_insights,
        answer_question,
        is_aggregate_question,
    )
    import rag_store
    import kb_store

# El motor de filtros con el mismo patrón dual paquete/suelto: así todos los
# endpoints que muestran filas (/rows, /summary, /facets) comparten las
# mismas reglas de filtrado desde un único módulo, sea cual sea la forma en
# que se cargue `main` (uvicorn backend.main:app en prod, `main` en tests).
# `_facet_field` se reutiliza para que los conteos del panel de facetas
# sigan exactamente las mismas reglas (conteo, orden, distinct) que la
# detección, en lugar de duplicarlas aquí y que diverjan con el tiempo.
try:
    from backend.filters import (
        parse_filters,
        apply_filters,
        detect_facets,
        _facet_field,
    )
except ImportError:  # pragma: no cover
    from filters import (
        parse_filters,
        apply_filters,
        detect_facets,
        _facet_field,
    )

app = FastAPI(title="Comunidad Dojo – SOC Analyst API", lifespan=lifespan)

# Allow the Vite dev server to call the API
# NOTE: explicit origins – "*" + credentials is an invalid/unsafe combination.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5300", "http://127.0.0.1:5300"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In‑memory store – replace with a DB for production
analysis_store: Dict[str, Dict[str, Any]] = {}

# Columns the analysis expects (used for validation & friendly errors)
EXPECTED_COLUMNS = [
    "Log_ID", "Timestamp", "Hostname", "Event_ID", "User", "Source_IP",
    "Destination", "Log_Source", "Event_Description", "Result", "Severity",
]

# ---------------------------------------------------------------------------
# Risk classification (severity from the source file -> our 4 levels)
# ---------------------------------------------------------------------------
# Mapeo de valores originales del campo Severity (del Excel / fuente) →
# nuestros 4 niveles de riesgo. El orden refleja la política de la
# organización: warning se trata como crítico (no como medio).
SEVERITY_TO_LEVEL: Dict[str, str] = {
    "critical": "critico",
    "critico": "critico",
    "high": "alto",
    "alto": "alto",
    "medium": "medio",
    "medio": "medio",
    "warning": "critico",
    "low": "bajo",
    "bajo": "bajo",
    "informational": "bajo",
    "informative": "bajo",
    "info": "bajo",
}
LEVEL_ORDER = ["critico", "alto", "medio", "bajo"]
LEVEL_LABELS = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "bajo": "Bajo"}

# Palabras clave para mapear valores desconocidos (otros datasets) de forma
# robusta, sin depender de coincidencia exacta. Se revisan de más grave a más
# leve; el primero que gana se aplica. Default: "bajo" (sin evidencia de riesgo).
SEVERITY_KEYWORDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("critico", ("critical", "crit", "warn", "err", "severe", "fatal", "emergency", "alert")),
    ("alto", ("high", "major", "elevated")),
    ("medio", ("medium", "moderate", "average")),
    ("bajo", ("low", "inform", "info", "trivial", "minor")),
]

# Windows Security event IDs – explanation grounded in the dataset's own
# Event_Description column (no invented findings).
EVENT_ID_REASONS: Dict[int, str] = {
    4625: "Autenticación fallida (Event ID 4625)",
    4624: "Autenticación exitosa (Event ID 4624)",
    4648: "Inicio de sesión con credenciales explícitas (Event ID 4648)",
    4672: "Permisos especiales otorgados (Event ID 4672)",
    4688: "Nuevo proceso creado (Event ID 4688)",
    4698: "Tarea programada creada (Event ID 4698)",
    4720: "Creación de cuenta (Event ID 4720)",
    4728: "Miembro añadido a grupo global (Event ID 4728)",
    4732: "Miembro añadido a grupo local (Event ID 4732)",
    4740: "Cuenta bloqueada / lockout (Event ID 4740)",
    7045: "Servicio nuevo instalado (Event ID 7045)",
    1102: "Registro de seguridad reiniciado (Event ID 1102)",
}

# Keywords looked up in Event_Description (matched case-insensitively)
KEYWORD_REASONS: Dict[str, str] = {
    "failed": "intentos de autenticación fallidos",
    "failures": "fallos de autenticación",
    "failure": "fallo de autenticación",
    "unauthorized": "acceso no autorizado",
    "malware": "actividad relacionada con malware",
    "exploit": "posible explotación de vulnerabilidad",
    "suspicious": "comportamiento sospechoso",
    "alert": "alerta de seguridad",
    "denied": "acceso denegado",
    "locked": "cuenta bloqueada",
    "lockout": "bloqueo de cuenta",
    "brute": "posible ataque de fuerza bruta",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _counts(series: pd.Series, n: int | None = None) -> Dict[str, int]:
    """value_counts -> JSON friendly dict of str -> int."""
    vc = series.value_counts()
    if n is not None:
        vc = vc.head(n)
    return {str(k): int(v) for k, v in vc.items()}


def _records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """DataFrame -> list of JSON-serialisable dicts (NaN -> None, numpy -> python)."""
    if df.empty:
        return []
    out = df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")
    for row in out:
        for key, value in list(row.items()):
            if isinstance(value, (pd.Timestamp, datetime, date)):
                row[key] = value.isoformat()
            elif hasattr(value, "item"):  # numpy scalar
                try:
                    row[key] = value.item()
                except Exception:
                    row[key] = str(value)
    return out


def normalize_level(severity: Any) -> str:
    """Map a raw severity value to one of our four risk levels.

    Coincidencia exacta primero (catálogo conocido), luego coincidencia parcial
    (contiene) para ser robustos con otros datasets, y por defecto "bajo"
    (sin evidencia de riesgo destacada).
    """
    raw = str(severity).strip().lower()
    if not raw or raw in ("nan", "none", "null", ""):
        return "bajo"

    # 1. Coincidencia exacta (catálogo conocido)
    exact = SEVERITY_TO_LEVEL.get(raw)
    if exact:
        return exact

    # 2. Coincidencia parcial por palabras clave (otros datasets)
    for level, keywords in SEVERITY_KEYWORDS:
        if any(k in raw for k in keywords):
            return level

    return "bajo"


def row_reason(row: pd.Series) -> str:
    """Grounded justification for a single record (severity + result + keywords + event id)."""
    reasons: List[str] = []
    severity = str(row.get("Severity", "")).strip()
    if severity and severity.lower() != "nan":
        reasons.append(f"severidad «{severity}»")
    # Result: éxito/fallo de la operación o código HTTP de respuesta.
    # Se normaliza («404.0» → «404») para que la justificación muestre el
    # valor tal cual está en el archivo.
    result_txt = _result_label(row.get("Result", ""))
    if result_txt:
        reasons.append(f"resultado «{result_txt}»")
    desc = str(row.get("Event_Description", "") or "")
    desc_low = desc.lower()
    for keyword, text in KEYWORD_REASONS.items():
        if keyword in desc_low:
            reasons.append(text)
            break  # first keyword match is enough
    event_id = row.get("Event_ID")
    try:
        event_id_int = int(event_id)
    except (TypeError, ValueError):
        event_id_int = None
    if event_id_int in EVENT_ID_REASONS:
        reasons.append(EVENT_ID_REASONS[event_id_int])
    if not reasons:
        reasons.append("registro sin indicadores de riesgo destacados")
    return " · ".join(reasons)


def _is_suspicious(row: pd.Series) -> bool:
    level = normalize_level(row.get("Severity"))
    if level in ("critico", "alto"):
        return True
    desc = str(row.get("Event_Description", "") or "").lower()
    return any(kw in desc for kw in KEYWORD_REASONS)


def _pct(count: int, total: int) -> float:
    return round((count / total) * 100, 1) if total else 0.0


def _result_label(value: Any) -> str:
    """Texto normalizado del campo Result.

    Si la columna tiene celdas vacías pandas la lee como numérica y «404»
    llega como «404.0»: se muestra y se compara sin el «.0» para que el
    filtro, el dropdown y la detección de códigos HTTP casen con el valor
    real del archivo. NaN y vacíos quedan como cadena vacía.
    """
    if value is None or (isinstance(value, float) and value != value):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"-?\d+\.0+", text):
        text = text.split(".", 1)[0]
    return text


def _enrich_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Filas del Excel + nivel/justificación/sospechoso, para el índice RAG.

    Todo se calcula con las mismas funciones deterministas que usa la API
    (row_reason / _is_suspicious), así el texto indexado no añade nada que
    no pueda rastrearse en los registros originales.
    """
    records = _records(df)
    if not records:
        return records
    if "Severity" in df.columns:
        levels = df["Severity"].map(normalize_level)
    else:
        levels = pd.Series(["bajo"] * len(df), index=df.index)
    reasons = df.apply(row_reason, axis=1)
    flags = df.apply(_is_suspicious, axis=1)
    for position, record in enumerate(records):
        record["_level"] = str(levels.iloc[position])
        record["_reason"] = str(reasons.iloc[position])
        record["_suspicious"] = bool(flags.iloc[position])
    return records


def _recent_analysis_ids(limit: int = 5) -> List[str]:
    """Últimos análisis completados (orden de inserción del dict = cronológico)."""
    done = [key for key, value in analysis_store.items() if value.get("status") == "done"]
    return done[-limit:]


# Palabras vacías de las preguntas (ES/EN) — no aportan a la recuperación léxica
QA_STOPWORDS = {
    "que", "qual", "como", "cuando", "donde", "cuales", "cual", "por", "para",
    "con", "una", "unos", "unos", "los", "las", "del", "por", "hay", "hubo",
    "fue", "fueron", "esta", "estan", "este", "estos", "entre", "sobre", "mas",
    "muy", "hay", "algo", "algun", "alguna", "the", "and", "was", "were",
    "what", "when", "where", "which", "about", "there", "from", "with", "have",
    "has", "any", "some", "happened", "did", "does", "eventos", "evento",
    "registros", "registro", "pasó", "paso",
}


def _keyword_hits(
    df: pd.DataFrame,
    question: str,
    limit: int = 20,
    level: str = "",
    suspicious: bool = False,
) -> List[Dict[str, Any]]:
    """Recuperación léxica (coincidencia de términos) sobre los registros.

    Aporta lo que la búsqueda semántica no cubre bien: identificadores exactos
    (IP, Log_ID, Event_ID, host, usuario). No depende de ningún paquete extra,
    por lo que funciona aunque el RAG no esté instalado.
    """
    if df is None or df.empty:
        return []
    tokens = [
        token for token in re.findall(r"[\w.:@-]+", question.lower())
        if len(token) > 2 and token not in QA_STOPWORDS
    ]
    if not tokens:
        return []

    work = df.copy()
    work["_level"] = work["Severity"].map(normalize_level) if "Severity" in work.columns else "bajo"
    work["_reason"] = work.apply(row_reason, axis=1)
    work["_suspicious"] = work.apply(_is_suspicious, axis=1)
    if level in LEVEL_ORDER:
        work = work[work["_level"] == level]
    if suspicious:
        work = work[work["_suspicious"]]
    if work.empty:
        return []

    texts = work.astype(str).agg(" ".join, axis=1).str.lower()
    matched = texts.apply(lambda text: sum(1 for token in tokens if token in text))
    candidates = work.loc[matched > 0].copy()
    if candidates.empty:
        return []
    candidates["_score"] = matched[matched > 0]
    candidates = candidates.sort_values("_score", ascending=False).head(limit)

    hits: List[Dict[str, Any]] = []
    for _, row in candidates.iterrows():
        record = row.to_dict()
        score = min(1.0, float(record.pop("_score")) / len(tokens))
        level_value = str(record.pop("_level", "bajo"))
        reason_value = str(record.pop("_reason", ""))
        suspicious_value = bool(record.pop("_suspicious", False))
        record["_level"], record["_reason"], record["_suspicious"] = level_value, reason_value, suspicious_value
        hits.append({
            "score": round(score, 4),
            "distance": round(1.0 - score, 4),
            "document": rag_store.build_document(record),
            "metadata": rag_store.build_metadata(record, "", 0),
            "log_id": rag_store._clean(record.get("Log_ID")) or None,
            "level": level_value,
            "hostname": rag_store._clean(record.get("Hostname")) or None,
            "source_ip": rag_store._clean(record.get("Source_IP")) or None,
            "event_id": rag_store._clean(record.get("Event_ID")) or None,
            "suspicious": suspicious_value,
            "retrieval": "lexica",
        })
    return hits


def _hybrid_retrieve(
    analysis_id: str,
    question: str,
    df: pd.DataFrame,
    n_results: int = 8,
    level: str = "",
    suspicious: bool = False,
) -> List[Dict[str, Any]]:
    """Combina recuperación semántica (RAG) + léxica en una sola lista.

    - Semántica: entiende «fuerza bruta» ↔ «brute force» (ES↔EN).
    - Léxica: captura identificadores exactos (IP, Log_ID, Event_ID, host).
    - Fusiona por Log_ID; si ambas lo encuentran, sube la puntuación.
    """
    # Ampliación bilingüe: los logs están en inglés y el analista pregunta en
    # español, así que se enriquece SOLO la consulta con sinónimos técnicos.
    expanded = rag_store.expand_query(question)
    semantic: List[Dict[str, Any]] = []
    if rag_store.is_available():
        semantic = rag_store.query_events(
            analysis_id, expanded, n_results=n_results, level=level or None,
            suspicious=suspicious if suspicious else None,
        )
    lexical = _keyword_hits(df, expanded, level=level, suspicious=suspicious)

    merged: Dict[str, Dict[str, Any]] = {}
    for hit in semantic:
        hit["retrieval"] = "semantica"
        key = str(hit.get("log_id") or hit.get("document", "")[:80])
        merged[key] = hit

    for hit in lexical:
        key = str(hit.get("log_id") or hit.get("document", "")[:80])
        existing = merged.get(key)
        if existing is None:
            merged[key] = hit
        else:
            # Ambas rutas lo encontraron → evidencia más sólida
            existing["score"] = round(min(1.0, max(existing["score"], hit["score"]) + 0.05), 4)
            existing["retrieval"] = "hibrida"

    ordered = sorted(merged.values(), key=lambda item: -float(item.get("score", 0.0)))
    return ordered[: max(1, min(n_results, 20))]


# ---------------------------------------------------------------------------
# Analysis core
# ---------------------------------------------------------------------------
def call_ai_service(dataframe: pd.DataFrame) -> Dict[str, Any]:
    """Motor de análisis asistido: clasifica, correla y justifica los registros.

    Sustituye al antiguo stub de IA: ejecuta las reglas del analista a escala
    (normalización de severidad, detección de sospechosos, correlación
    IP↔hosts y justificación por nivel) y construye todos los payloads del
    dashboard. Todos los números, justificaciones y ejemplos se derivan
    estrictamente de los registros provistos – nada se inventa (rigor
    analítico).
    """
    df = dataframe.copy()
    total = len(df)

    # --- basic stats -------------------------------------------------------
    # Solo las columnas originales del archivo: si llega un frame ya
    # enriquecido (ingesta), las derivadas (_level, _reason, _suspicious)
    # no son «campos» del Excel y no deben filtrarse en el resumen.
    columns = [c for c in df.columns if not str(c).startswith("_")]
    unique_hosts = int(df["Hostname"].nunique()) if "Hostname" in df.columns else None

    # Event_IDs that are not numeric → records coming from another log source
    non_numeric_event_ids: List[str] = []
    if "Event_ID" in df.columns:
        values = df["Event_ID"].astype(str).str.strip()
        non_numeric_event_ids = sorted(
            {v for v in values if v and v.lower() != "nan" and not v.isdigit()}
        )[:10]

    # --- severity / level classification ----------------------------------
    raw_severity = df["Severity"] if "Severity" in df.columns else pd.Series([""] * total)
    df["_level"] = raw_severity.map(normalize_level) if "Severity" in df.columns else "bajo"
    severity_counts = _counts(raw_severity) if "Severity" in df.columns else {}
    # Resultado de la operación por registro: Success/Failure/Allowed o
    # código HTTP (200/404/500) según el origen del log.
    if "Result" in df.columns:
        _rc = df["Result"].map(_result_label)
        result_counts = {str(k): int(v) for k, v in _rc[_rc != ""].value_counts().items()}
    else:
        result_counts = {}
    # Hosts por valor de Result: permite responder «¿de qué hosts son los 500?»
    # con recuentos exactos, igual que ya se hace por nivel de criticidad.
    result_hosts: Dict[str, Dict[str, int]] = {}
    if "Result" in df.columns and "Hostname" in df.columns and total:
        _res_tmp = pd.DataFrame({
            "value": df["Result"].map(_result_label),
            "host": df["Hostname"].astype(str),
        })
        _res_tmp = _res_tmp[
            (_res_tmp["value"] != "")
            & (_res_tmp["value"].str.lower() != "nan")
            & (_res_tmp["host"] != "")
            & (_res_tmp["host"].str.lower() != "nan")
        ]
        if len(_res_tmp):
            _res_grp = (
                _res_tmp.groupby(["value", "host"]).size().reset_index(name="n")
            )
            for _value, _grp in _res_grp.groupby("value"):
                _top = _grp.sort_values(["n", "host"], ascending=[False, True]).head(4)
                result_hosts[str(_value)] = {
                    str(row["host"]): int(row["n"]) for row in _top.to_dict("records")
                }
    level_counts = {lvl: int((df["_level"] == lvl).sum()) for lvl in LEVEL_ORDER}

    # Correspondencia auditable: cada valor original del campo Severity y el
    # nivel de riesgo al que se traduce (misma función normalize_level que
    # alimenta todos los gráficos y el informe).
    severity_mapping: List[Dict[str, Any]] = []
    if "Severity" in df.columns and total:
        severity_mapping = [
            {
                "raw": str(value),
                "level": normalize_level(value),
                "count": int(count),
                "percentage": _pct(int(count), total),
            }
            for value, count in raw_severity.value_counts(dropna=True).items()
        ]

    # --- timestamps --------------------------------------------------------
    events_per_day: Dict[str, int] = {}
    events_per_day_by_level: Dict[str, Dict[str, int]] = {}
    events_per_hour_by_level: Dict[str, Dict[str, int]] = {}
    if "Timestamp" in df.columns:
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
        dated = df.dropna(subset=["Timestamp"])
        events_per_day = {str(k): int(v) for k, v in dated["Timestamp"].dt.date.value_counts().sort_index().items()}
        if len(dated):
            pivot = dated.groupby([dated["Timestamp"].dt.date, "_level"]).size().unstack(fill_value=0)
            for lvl in LEVEL_ORDER:
                if lvl not in pivot.columns:
                    pivot[lvl] = 0
            events_per_day_by_level = {
                str(day): {lvl: int(pivot.loc[day, lvl]) for lvl in LEVEL_ORDER}
                for day in pivot.index
            }

            # Serie horaria real: una clave por fecha+hora presente en los
            # timestamps (no se rellenan horas sin datos). Es la serie que usa
            # el dashboard cuando hay menos de un día completo de información.
            hour_keys = dated["Timestamp"].dt.strftime("%Y-%m-%d %H")
            pivot_h = dated.groupby([hour_keys, "_level"]).size().unstack(fill_value=0)
            for lvl in LEVEL_ORDER:
                if lvl not in pivot_h.columns:
                    pivot_h[lvl] = 0
            events_per_hour_by_level = {
                f"{stamp}:00": {lvl: int(pivot_h.loc[stamp, lvl]) for lvl in LEVEL_ORDER}
                for stamp in sorted(pivot_h.index)
            }

    # --- hosts -------------------------------------------------------------
    top_hosts: Dict[str, int] = {}
    top_hosts_by_level: Dict[str, Dict[str, int]] = {}
    if "Hostname" in df.columns:
        top_hosts = _counts(df["Hostname"], n=6)
        hosts = list(top_hosts.keys())
        sub = df[df["Hostname"].isin(hosts)]
        grouped = sub.groupby(["Hostname", "_level"]).size().unstack(fill_value=0)
        for lvl in LEVEL_ORDER:
            if lvl not in grouped.columns:
                grouped[lvl] = 0
        top_hosts_by_level = {
            host: {lvl: int(grouped.loc[host, lvl]) for lvl in LEVEL_ORDER}
            for host in hosts if host in grouped.index
        }

    # --- suspicious events -------------------------------------------------
    suspicious_mask = df.apply(_is_suspicious, axis=1) if total else pd.Series([], dtype=bool)
    df["_suspicious"] = suspicious_mask
    df["_reason"] = df.apply(row_reason, axis=1) if total else pd.Series([], dtype=str)

    suspicious_events = int(df["_suspicious"].sum())
    detail_cols = [
        c for c in ["Log_ID", "Timestamp", "Hostname", "Event_ID", "User",
                    "Source_IP", "Event_Description", "Severity"]
        if c in df.columns
    ]
    suspicious_rows = df[df["_suspicious"]]
    suspicious_details = _records(suspicious_rows[detail_cols + ["_reason"]].head(10)) if suspicious_events else []

    # --- HTTP error responses (Result = 4xx/5xx) ---------------------------
    # Registros con un código de respuesta HTTP de error en el campo Result:
    # son la base del hallazgo «respuestas HTTP de error» del informe, con
    # Log_ID, Timestamp y Severity de cada uno (nada se deduce, todo se cita).
    http_errors: Dict[str, Any] = {"codes": {}, "hosts": {}, "levels": {},
                                   "severities": {}, "total": 0, "examples": []}
    if "Result" in df.columns and total:
        _res_str = df["Result"].map(_result_label)
        _err_mask = _res_str.str.fullmatch(r"[45]\d\d").fillna(False)
        _err_rows = df[_err_mask]
        if len(_err_rows):
            _err_cols = detail_cols + [c for c in ("Result", "_reason") if c in _err_rows.columns]
            http_errors = {
                "codes": {str(k): int(v) for k, v in _res_str[_err_mask].value_counts().items()},
                "hosts": _counts(_err_rows["Hostname"], n=6) if "Hostname" in _err_rows.columns else {},
                "levels": _counts(_err_rows["_level"]) if "_level" in _err_rows.columns else {},
                "severities": _counts(_err_rows["Severity"]) if "Severity" in _err_rows.columns else {},
                "total": int(len(_err_rows)),
                "examples": _records(_err_rows[_err_cols].head(12)),
            }

    suspicious_correlations: Dict[str, int] = {}
    suspicious_by_ip: List[Dict[str, Any]] = []
    if suspicious_events and "Hostname" in df.columns:
        suspicious_correlations = _counts(suspicious_rows["Hostname"], n=6)
    if suspicious_events and "Source_IP" in df.columns and "Hostname" in df.columns:
        grouped_ip = (
            suspicious_rows.groupby("Source_IP")
            .agg(events=("Source_IP", "size"), hosts=("Hostname", "nunique"))
            .sort_values(["hosts", "events"], ascending=False)
            .head(6)
        )
        suspicious_by_ip = [
            {"ip": str(ip), "events": int(r.events), "hosts": int(r.hosts)}
            for ip, r in grouped_ip.iterrows()
        ]
    elif suspicious_events and "Source_IP" in df.columns:
        # Sin columna Hostname no hay «alcance por host»: se cuenta el volumen
        # por IP sin inventar hosts.
        grouped_ip = (
            suspicious_rows.groupby("Source_IP")
            .size()
            .sort_values(ascending=False)
            .head(6)
        )
        suspicious_by_ip = [
            {"ip": str(ip), "events": int(n), "hosts": 0}
            for ip, n in grouped_ip.items()
        ]

    # --- risk level with data-grounded justification -----------------------
    highest = next((lvl for lvl in LEVEL_ORDER if level_counts[lvl] > 0), None)
    if highest is None:
        risk = "bajo"
        risk_justification = "Sin registros clasificados en el archivo analizado."
    else:
        risk = highest
        sub = df[df["_level"] == highest]
        top_events = sub["Event_ID"].value_counts().head(3) if "Event_ID" in sub.columns else pd.Series(dtype=int)
        top_hosts_high = sub["Hostname"].value_counts().head(3) if "Hostname" in sub.columns else pd.Series(dtype=int)
        events_txt = ", ".join(f"{e} ({int(c)})" for e, c in top_events.items()) if len(top_events) else (
            "n/d" if "Event_ID" in sub.columns else "n/d (el archivo no trae la columna Event_ID)"
        )
        hosts_txt = ", ".join(f"{h} ({int(c)})" for h, c in top_hosts_high.items()) if len(top_hosts_high) else (
            "n/d" if "Hostname" in sub.columns else "n/d (el archivo no trae la columna Hostname)"
        )
        risk_justification = (
            f"Nivel «{LEVEL_LABELS[highest]}»: {level_counts[highest]} de {total} registros "
            f"({_pct(level_counts[highest], total)}%) clasificados en este nivel. "
            f"Event IDs dominantes: {events_txt}. Hosts con más registros: {hosts_txt}."
        )

    # --- per-level blocks (count + justification + examples) ---------------
    risk_levels: Dict[str, Dict[str, Any]] = {}
    example_cols = [c for c in ["Log_ID", "Timestamp", "Hostname", "Event_ID",
                                "Source_IP", "Event_Description", "Severity"]
                    if c in df.columns]
    for lvl in LEVEL_ORDER:
        sub = df[df["_level"] == lvl]
        count = len(sub)
        if count == 0:
            justification = "Sin registros en este nivel dentro de los datos analizados."
            examples: List[Dict[str, Any]] = []
        else:
            top_events = sub["Event_ID"].value_counts().head(3) if "Event_ID" in sub.columns else pd.Series(dtype=int)
            top_hosts_lvl = sub["Hostname"].value_counts().head(3) if "Hostname" in sub.columns else pd.Series(dtype=int)
            ev_txt = ", ".join(f"{e} ×{int(c)}" for e, c in top_events.items())
            hs_txt = ", ".join(f"{h} ×{int(c)}" for h, c in top_hosts_lvl.items())
            justification = (
                f"{count} registros ({_pct(count, total)}% del total). "
                f"Distribución de Event IDs: {ev_txt or 'n/d'}. "
                f"Hosts principales: {hs_txt or 'n/d'}."
            )
            examples = _records(sub[example_cols + ["_reason"]].head(5))
        risk_levels[lvl] = {
            "level": lvl,
            "label": LEVEL_LABELS[lvl],
            "count": count,
            "percentage": _pct(count, total),
            "justification": justification,
            "examples": examples,
        }

    return {
        "rows": total,
        "columns": columns,
        "non_numeric_event_ids": non_numeric_event_ids,
        "risk": risk,
        "risk_justification": risk_justification,
        "severity_counts": severity_counts,
        "severity_mapping": severity_mapping,
        "result_counts": result_counts,
        "result_hosts": result_hosts,
        "level_counts": level_counts,
        "risk_levels": risk_levels,
        "unique_hosts": unique_hosts,
        "events_per_day": events_per_day,
        "events_per_day_by_level": events_per_day_by_level,
        "events_per_hour_by_level": events_per_hour_by_level,
        "top_hosts": top_hosts,
        "top_hosts_by_level": top_hosts_by_level,
        "suspicious_events": suspicious_events,
        "suspicious_details": suspicious_details,
        "http_errors": http_errors,
        "suspicious_correlations": suspicious_correlations,
        "suspicious_by_ip": suspicious_by_ip,
    }


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------
async def process_analysis(analysis_id: str, file_bytes: bytes, file_name: str = ""):
    try:
        analysis_store[analysis_id]["status"] = "processing"
        await asyncio.sleep(0.5)
        # Read EVERY sheet (BytesIO avoids the deprecated bytes argument)
        sheets: Dict[str, pd.DataFrame] = pd.read_excel(
            BytesIO(file_bytes), sheet_name=None, engine="openpyxl"
        )
        if not sheets:
            raise ValueError("El archivo Excel no contiene hojas.")
        # Analyse the whole workbook
        df = pd.concat(sheets.values(), ignore_index=True)
        # Enriquecimiento ÚNICO en ingesta: las columnas derivadas (_level,
        # _reason, _suspicious) y el Timestamp tipado se calculan una sola vez
        # aquí y se guardan en el store. /rows, el informe y el índice RAG las
        # consumen a partir de ahí sin recalcular nada por petición.
        if "Timestamp" in df.columns:
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
        if "Severity" in df.columns:
            df["_level"] = df["Severity"].map(normalize_level)
        else:
            # Un Excel sin Severity no aporta evidencia de riesgo: todo «bajo»
            # (mismo criterio que aplica /rows).
            df["_level"] = "bajo"
        if len(df):
            df["_reason"] = df.apply(row_reason, axis=1)
            df["_suspicious"] = df.apply(_is_suspicious, axis=1)
        else:
            # apply sobre 0 filas devolvería un DataFrame, no una Serie: se
            # crean Series vacías tipadas (mismo criterio que /rows).
            df["_reason"] = pd.Series([], dtype=str)
            df["_suspicious"] = pd.Series([], dtype=bool)
        missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
        summary = call_ai_service(df)
        summary["sheet_names"] = list(sheets.keys())
        summary["missing_columns"] = missing
        summary["file_name"] = file_name
        summary["generated_at"] = datetime.now().isoformat(timespec="seconds")
        # Observaciones automáticas de la IA sobre los datos (grounded)
        summary["ai_insights"] = generate_data_insights(summary)

        # Keep the classified dataframe so the UI can visualise every record
        analysis_store[analysis_id]["dataframe"] = df
        analysis_store[analysis_id]["summary"] = summary
        analysis_store[analysis_id]["status"] = "done"
    except Exception as exc:  # noqa: BLE001 – surface any failure to the UI
        analysis_store[analysis_id]["status"] = "error"
        analysis_store[analysis_id]["error"] = str(exc)
        return

    # ------------------------------------------------------------------
    # Índice semántico (RAG) — en un hilo para no bloquear el event loop.
    # Nunca invalida el análisis: si falla, la app sigue en modo determinista.
    # ------------------------------------------------------------------
    records = _enrich_records(df)
    analysis_store[analysis_id]["rag"] = {
        "available": rag_store.is_available(),
        "indexed": 0,
        "total": len(records),
        "reason": "indexando",
    }
    # Se registra la tarea para que /ask no lance una segunda indexación
    # concurrente del mismo análisis si el usuario pregunta mientras corre.
    current = asyncio.current_task()
    if current is not None:
        _INDEX_TASKS[analysis_id] = current
    try:
        rag_result = await asyncio.to_thread(rag_store.index_events, analysis_id, records)
        analysis_store[analysis_id]["rag"] = rag_result
        # El índice persiste en disco: nos quedamos solo con los últimos análisis.
        rag_store.purge_except(_recent_analysis_ids(), max_analyses=5)
    except Exception as rag_exc:  # noqa: BLE001
        logger.warning("RAG no disponible para %s: %s", analysis_id[:8], rag_exc)
        analysis_store[analysis_id]["rag"] = {
            "available": False,
            "indexed": 0,
            "total": len(records),
            "reason": str(rag_exc),
        }
    finally:
        _INDEX_TASKS.pop(analysis_id, None)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class AnalysisResult(BaseModel):
    analysis_id: str
    status: str  # pending | processing | done | error
    summary: Dict[str, Any] | None = None
    error: str | None = None
    # Estado del índice semántico (RAG): available / indexed / total / reason
    rag: Dict[str, Any] | None = None


@app.post("/api/analyze", response_model=AnalysisResult)
async def upload_excel(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .xlsx")
    content = await file.read()
    analysis_id = str(uuid.uuid4())
    analysis_store[analysis_id] = {"status": "pending", "summary": None, "error": None}
    background_tasks.add_task(process_analysis, analysis_id, content, file.filename or "")
    return AnalysisResult(analysis_id=analysis_id, status="pending")


@app.get("/api/analysis/{analysis_id}", response_model=AnalysisResult)
async def get_analysis(analysis_id: str):
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    entry = analysis_store[analysis_id]
    return AnalysisResult(
        analysis_id=analysis_id,
        status=entry["status"],
        summary=entry.get("summary"),
        error=entry.get("error"),
        rag=entry.get("rag"),
    )


@app.get("/api/analysis/{analysis_id}/rows")
async def get_rows(
    analysis_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    q: str = Query("", max_length=200),
    level: str = Query("", description="critico|alto|medio|bajo"),
    suspicious: bool = Query(False),
    result: str = Query("", description="valor exacto del campo Result (Success, Failure, Allowed, 200…)"),
):
    """Paginated view of the imported records (visualisation of the Excel).

    Supports a free-text search (q), a risk-level filter, a "suspicious only"
    toggle and an exact Result filter – everything is computed server-side.
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    df: pd.DataFrame | None = analysis_store[analysis_id].get("dataframe")
    if df is None:
        raise HTTPException(status_code=409, detail="El análisis aún no está listo")

    work = df.copy()
    # classification columns (recomputed on request – cheap for these sizes)
    if "Severity" in work.columns:
        work["_level"] = work["Severity"].map(normalize_level)
    else:
        work["_level"] = "bajo"
    work["_reason"] = work.apply(row_reason, axis=1) if len(work) else pd.Series([], dtype=str)
    work["_suspicious"] = work.apply(_is_suspicious, axis=1) if len(work) else pd.Series(dtype=bool)

    if level in LEVEL_ORDER:
        work = work[work["_level"] == level]
    if suspicious:
        work = work[work["_suspicious"]]
    if result.strip() and "Result" in work.columns:
        wanted = result.strip().lower()
        work = work[work["Result"].map(_result_label).str.lower() == wanted]
    if q.strip():
        # La búsqueda libre se limita a las columnas originales del Excel: las
        # derivadas (_level, _reason, _suspicious) no son datos del archivo y
        # harían match con textos que el analista no ve en la tabla.
        needle = q.strip().lower()
        search_cols = [c for c in work.columns if not str(c).startswith("_")]
        if search_cols:
            mask = work[search_cols].astype(str).apply(
                lambda col: col.str.lower().str.contains(needle, regex=False, na=False)
            ).any(axis=1)
            work = work[mask]

    total_filtered = len(work)
    start = (page - 1) * page_size
    if total_filtered and start >= total_filtered:
        # Página fuera de rango (p. ej. el archivo nuevo tiene menos páginas
        # que el anterior): se devuelve la última con datos en lugar de 0 filas.
        page = (total_filtered - 1) // page_size + 1
        start = (page - 1) * page_size
    page_df = work.iloc[start:start + page_size]

    out_cols = [c for c in df.columns if not str(c).startswith("_")]
    rows = _records(page_df[out_cols + ["_level", "_reason", "_suspicious"]])
    for row in rows:
        row["risk_level"] = row.pop("_level", "bajo")
        row["risk_reason"] = row.pop("_reason", "")
        row["suspicious"] = bool(row.pop("_suspicious", False))

    return {
        "total": int(total_filtered),
        "total_rows": int(len(df)),
        "page": page,
        "page_size": page_size,
        "columns": out_cols,
        "rows": rows,
    }


@app.get("/api/analysis/{analysis_id}/summary")
async def get_scoped_summary(analysis_id: str, request: Request):
    """Resumen completo del análisis acotado por los filtros de la URL (spec §4.4).

    Aplica exactamente los mismos filtros que /rows para que los KPI
    coincidan con lo que el analista ve en la tabla: si divergieran, el
    resumen no sería creíble. `total` es el tamaño del archivo completo y
    `filtered_total`/`rows` el de la selección actual, así la UI puede
    mostrar «de 12 a 8 filas».
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    df: pd.DataFrame | None = analysis_store[analysis_id].get("dataframe")
    if df is None:
        raise HTTPException(status_code=409, detail="El análisis aún no está listo")

    try:
        params = parse_filters(request, df)
    except ValueError as exc:
        # Filtro imposible (nivel fuera de catálogo, ventana invertida o
        # fecha ilegible): 400 con el motivo, nunca un 500 (R10).
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    work = apply_filters(df, params)
    # call_ai_service es pura y copia su entrada: el store nunca se toca y
    # el resumen refleja solo las filas que sobreviven al filtro.
    resumen = call_ai_service(work)
    resumen["total"] = int(len(df))
    resumen["filtered_total"] = int(len(work))
    resumen["rows"] = int(len(work))
    return resumen


@app.get("/api/analysis/{analysis_id}/facets")
async def get_facets(analysis_id: str, request: Request):
    """Campos del panel de facetas con sus valores y conteos acotados (spec §4.4).

    La detección se hace sobre el dataset completo para que la lista de
    campos sea estable: un campo no debe desaparecer del panel solo porque
    el filtro actual lo haya dejado vacío. Los conteos van sobre las filas
    filtradas con los mismos filtros que /summary, y cada campo se recuenta
    ignorando la selección de ese mismo campo (estilo Splunk): así con un
    valor activo el panel sigue ofreciendo los demás para poder cambiar.
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    df: pd.DataFrame | None = analysis_store[analysis_id].get("dataframe")
    if df is None:
        raise HTTPException(status_code=409, detail="El análisis aún no está listo")

    try:
        params = parse_filters(request, df)
    except ValueError as exc:
        # Mismos 400 que /summary: una query inválida es inválida para
        # cualquier endpoint, nunca un 500 (R10).
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    filtrado = apply_filters(df, params)
    campos = []
    # top_n=12 por campo: acorta la respuesta sin perder los valores que
    # el panel enseña de todos modos (spec §4.4).
    for campo in detect_facets(df, top_n=12):
        parcial = apply_filters(df, params, skip_fields={campo.name})
        campos.append(_facet_field(parcial[campo.name], campo.name, campo.label, 12))

    return {
        "total": int(len(df)),
        "filtered_total": int(len(filtrado)),
        "fields": campos,
    }


@app.post("/api/analysis/{analysis_id}/report")
async def build_report(analysis_id: str):
    """Redacta el informe técnico con la IA, usando solo datos calculados.

    El backend envía al servicio de IA exclusivamente estadísticas y
    evidencias derivadas de los registros importados, de modo que no puede
    haber hallazgos inventados (grounding estricto).
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    entry = analysis_store[analysis_id]
    if entry.get("status") != "done" or not entry.get("summary"):
        raise HTTPException(status_code=409, detail="El análisis aún no está listo")
    narrative = await generate_report_narrative(entry["summary"])
    analysis_store[analysis_id]["report"] = narrative
    return narrative


@app.get("/api/analysis/{analysis_id}/events")
async def stream_events(analysis_id: str):
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")

    async def event_generator():
        while True:
            entry = analysis_store[analysis_id]
            yield {"event": "status", "data": entry["status"]}
            if entry["status"] in ("done", "error"):
                break
            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


class AskRequest(BaseModel):
    question: str = ""
    level: str = ""  # filtro opcional: critico|alto|medio|bajo
    suspicious: bool = False
    n_results: int = 8


def _ensure_semantic_index(
    analysis_id: str, entry: Dict[str, Any], df: pd.DataFrame | None
) -> bool:
    """Autorepara el índice semántico si ha desaparecido; no bloquea.

    El índice vive en disco y se comparte con las suites de prueba, que purgan
    lo que no usan: si el análisis ya está hecho pero sus eventos no están
    indexados, se reconstruye en un hilo. Devuelve ``True`` si hay una
    indexación en curso (la propia de la subida o esta), para que el llamador
    pueda advertir de que la respuesta usa solo recuperación léxica.
    """
    running = _INDEX_TASKS.get(analysis_id)
    if running is not None and not running.done():
        return True
    if df is None or df.empty or not rag_store.is_available():
        return False
    if rag_store.has_index(analysis_id):
        return False

    entry["rag"] = {"available": True, "indexed": 0, "total": len(df), "reason": "indexando"}

    async def _reindex() -> None:
        try:
            records = _enrich_records(df)
            result = await asyncio.to_thread(rag_store.index_events, analysis_id, records)
            entry["rag"] = result
            logger.info("Índice semántico reconstruido para %s", analysis_id[:8])
        except Exception as exc:  # noqa: BLE001 – nunca invalida la consulta
            logger.warning("Autoreparación del índice falló para %s: %s", analysis_id[:8], exc)
            entry["rag"] = {"available": rag_store.is_available(), "indexed": 0,
                            "total": len(df), "reason": str(exc)}
        finally:
            _INDEX_TASKS.pop(analysis_id, None)

    _INDEX_TASKS[analysis_id] = asyncio.create_task(_reindex())
    return True


@app.post("/api/analysis/{analysis_id}/ask")
async def ask_question(analysis_id: str, payload: AskRequest):
    """Consulta en lenguaje natural sobre los eventos importados (RAG).

    Recuperación híbrida (semántica + léxica) y respuesta anclada a los
    eventos recuperados: cada cifra y cada Log_ID procede de los registros,
    nunca del modelo. Las consultas de agregación (conteos, distribución,
    hosts dominantes) se responden con cifras exactas del resumen del dataset.
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    entry = analysis_store[analysis_id]
    if entry.get("status") != "done" or not entry.get("summary"):
        raise HTTPException(status_code=409, detail="El análisis aún no está listo")

    question = (payload.question or "").strip()
    if len(question) < 3:
        raise HTTPException(status_code=400, detail="Escribe una consulta de al menos 3 caracteres")

    n_results = max(1, min(int(payload.n_results or 8), 20))
    level = payload.level if payload.level in LEVEL_ORDER else ""
    filters = {"level": level or None, "suspicious": bool(payload.suspicious)}
    common = {
        "analysis_id": analysis_id,
        "question": question,
        "filters": filters,
        "rag": rag_store.get_stats(analysis_id),
        "kb": kb_store.status(),
    }

    # Autoreparación del índice (no bloquea): se comprueba SIEMPRE, también en
    # las consultas de agregación, para que el índice vuelva a estar completo.
    df: pd.DataFrame | None = entry.get("dataframe")
    rag_state = entry.get("rag") or {}
    indexing = _ensure_semantic_index(analysis_id, entry, df)

    # --- Consulta de agregación: cifras exactas, sin recuperación -----------
    if is_aggregate_question(question):
        answer = await answer_question(question, [], entry["summary"], reference=None)
        return {**common, "retrieved": 0, **answer}

    # Si el índice semántico todavía no está listo (acaba de subirse o se está
    # reconstruyendo), se avisa en la respuesta: lo recuperado procede solo de
    # la búsqueda léxica. Nunca se bloquea la petición esperando al índice.
    index_note = ""
    if (indexing or rag_state.get("reason") == "indexando") and not rag_store.has_index(analysis_id):
        total = int(rag_state.get("total") or len(df) or 0)
        index_note = (
            f"El índice semántico local se está construyendo ({total} eventos); "
            "mientras tanto la recuperación es solo léxica sobre tus registros. "
            "Vuelve a preguntar en unos segundos para la recuperación semántica."
        )

    hits = _hybrid_retrieve(
        analysis_id, question, df, n_results=n_results,
        level=level, suspicious=bool(payload.suspicious),
    )

    # Base de conocimiento: significado de los Event IDs observados y técnicas
    # MITRE ATT&CK asociadas (referencia del marco, no un hallazgo sobre datos).
    reference = await asyncio.to_thread(kb_store.build_reference, question, hits)
    answer = await answer_question(
        question, hits, entry["summary"], reference=reference, index_note=index_note
    )

    return {**common, "retrieved": len(hits), **answer}


@app.get("/api/kb/status")
async def kb_status():
    """Estado de la base de conocimiento (técnicas ATT&CK + Event IDs).

    Responde siempre 200: si faltan dependencias opcionales devuelve
    `available: false` con el motivo y la app sigue operativa.
    """
    return kb_store.status()


@app.get("/api/rag/status")
async def rag_status(analysis_id: str = Query("", description="Análisis concreto (opcional)")):
    """Estado del índice semántico local (motor + eventos indexados).

    Responde siempre 200: si el motor no está instalado devuelve
    `available: false` y la app sigue operativa en modo determinista.
    """
    info = rag_store.get_stats(analysis_id or None)
    if analysis_id and analysis_id not in analysis_store:
        info["known_analysis"] = False
    return info
