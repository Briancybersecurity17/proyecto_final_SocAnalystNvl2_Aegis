import uuid
import asyncio
import json
import logging
import re
import time
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
    # Cross-encoder del rerank: solo ordena la evidencia, pero cuesta ~1 s en
    # cargarse, así que se precarga en un hilo y no en la primera consulta.
    asyncio.create_task(asyncio.to_thread(rerank.warmup))
    # El LLM vive en cloud: no hay nada que calentar en el arranque (la
    # primera llamada tarda lo mismo que la segunda). Si el proveedor está
    # caído o sin configurar, chat() devuelve None y el asistente cae al
    # motor determinista como siempre.
    # Memoria de criterio del analista (few-shot): lee el JSON y sincroniza el
    # índice semántico en segundo plano, igual que el resto de precargas.
    asyncio.create_task(asyncio.to_thread(feedback_store.warmup))
    # Spec A: los análisis viven en disco, así que un reinicio ya no los
    # pierde. Primero se restaura el store desde backend/data/analyses y
    # después se aplica la retención (Opciones IA) para no resucitar nada
    # que acaba de caducar. Ambas operaciones son de disco: a hilos.
    await asyncio.to_thread(rehidratar_desde_disco)
    await asyncio.to_thread(aplicar_retencion_config)
    yield

# El servicio de IA se importa como paquete (uvicorn backend.main:app) o como
# módulo suelto (test_backend.py añade /backend al sys.path).
try:
    from backend.ai_service import (
        CHAT_HISTORY_TURNS,
        QA_HISTORY_ANSWER_CHARS,
        generate_report_narrative,
        generate_data_insights,
        generate_local_insights,
        answer_question,
        is_aggregate_question,
        quick_answer,
        routes_aggregate,
        routes_pattern,
        routes_list_level,
        stream_question,
    )
    from backend import rag_store, kb_store, ai_config, rerank, feedback_store
    from backend import analysis_persist
    from backend.behavior_correlations import compute_behavior_patterns
except ImportError:  # pragma: no cover
    from ai_service import (
        CHAT_HISTORY_TURNS,
        QA_HISTORY_ANSWER_CHARS,
        generate_report_narrative,
        generate_data_insights,
        generate_local_insights,
        answer_question,
        is_aggregate_question,
        quick_answer,
        routes_aggregate,
        routes_pattern,
        routes_list_level,
        stream_question,
    )
    import rag_store
    import kb_store
    import ai_config
    import rerank
    import feedback_store
    import analysis_persist
    from behavior_correlations import compute_behavior_patterns

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


def _enrichir_df(df: pd.DataFrame) -> pd.DataFrame:
    """Columnas derivadas de ingesta: Timestamp tipado, _level, _reason, _suspicious.

    Se calculan UNA sola vez por dataframe — al ingerir y al rehidratar desde
    disco — para que /rows, el informe y el índice RAG consuman siempre las
    mismas columnas sin recalcular nada por petición.
    """
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
    return df


def rehidratar_desde_disco() -> int:
    """Restaura en el store los análisis persistidos en backend/data/analyses.

    Spec A: el backend ya no pierde los análisis al reiniciar. Por cada
    carpeta se relé el Excel original (fuente de verdad) para reconstruir el
    dataframe con sus columnas derivadas, y se recargan lo calculado
    (summary.json: estado, error, resumen, chat) y el informe cacheado
    (report.json). Un Excel ilegible marca ESE análisis como error sin
    tumbar el resto. Devuelve cuántos análisis se restauraron.
    """
    if not analysis_persist.ANALYSES_DIR.is_dir():
        return 0
    restaurados = 0
    for analysis_id in analysis_persist.listar_ids():
        if analysis_id in analysis_store:
            continue  # ya en memoria (p. ej. subido en esta misma vida)
        estado = analysis_persist.cargar_estado(analysis_id)
        entry: Dict[str, Any] = {
            # El id dentro de la entrada: _remember_turn lo usa para etiquetar
            # los ejemplos few-shot, igual que en una subida nueva.
            "analysis_id": analysis_id,
            "status": "error" if estado is None else (estado.get("status") or "error"),
            "error": None if estado is None else estado.get("error"),
            "summary": None if estado is None else estado.get("summary"),
            "chat_history": [] if estado is None else (estado.get("chat_history") or []),
            "rag": None if estado is None else estado.get("rag"),
            "report": analysis_persist.cargar_informe(analysis_id),
        }
        if estado is None:
            entry["error"] = "summary.json ausente o ilegible al arrancar"
        content = analysis_persist.leer_excel(analysis_id)
        try:
            if content is None:
                raise ValueError("El Excel original del análisis no está en disco")
            sheets: Dict[str, pd.DataFrame] = pd.read_excel(
                BytesIO(content), sheet_name=None, engine="openpyxl"
            )
            if not sheets:
                raise ValueError("El archivo Excel no contiene hojas.")
            entry["dataframe"] = _enrichir_df(pd.concat(sheets.values(), ignore_index=True))
            # Un análisis que estaba «pending/processing» al reiniciar quedó
            # interrumpido: sin resumen no hay nada que servir, se marca error.
            if entry["status"] in ("pending", "processing"):
                entry["status"] = "error"
                entry["error"] = "Análisis interrumpido por un reinicio del backend"
        except Exception as exc:  # noqa: BLE001 – archivo corrupto: solo ese análisis
            logger.warning("Rehidratación falló para %s: %s", analysis_id[:8], exc)
            entry["status"] = "error"
            entry["error"] = f"No se pudo leer el Excel persistido: {exc}"
            entry["dataframe"] = None
        analysis_store[analysis_id] = entry
        restaurados += 1
    if restaurados:
        logger.info("Análisis restaurados desde disco: %d", restaurados)
    return restaurados


def aplicar_retencion_config() -> List[str]:
    """Borra de disco los análisis que superen `retention_days` (Opciones IA).

    0 = conservar para siempre. Además de la carpeta, se limpian su entrada
    en memoria y su índice RAG para que no queden fantasmas en el historial.
    """
    try:
        dias = int(ai_config.get().get("retention_days", 30))
    except (TypeError, ValueError):
        dias = 30
    borrados = analysis_persist.aplicar_retencion(dias)
    for analysis_id in borrados:
        analysis_store.pop(analysis_id, None)
        _INDEX_TASKS.pop(analysis_id, None)
        try:
            rag_store.delete_analysis(analysis_id)
        except Exception as exc:  # noqa: BLE001 – la retención no puede fallar
            logger.warning("Índice RAG no borrado para %s: %s", analysis_id[:8], exc)
    if borrados:
        logger.info("Retención: %d análisis borrados de disco", len(borrados))
    return borrados

# ---------------------------------------------------------------------------
# Risk classification (severity from the source file -> our 4 levels)
# ---------------------------------------------------------------------------
# Clasificación de severidad: el catálogo y normalize_level viven en un
# módulo NEUTRAL (severity.py) sin FastAPI ni pandas, para que eval/cases.py
# calcule la verdad sin arrastrar la app completa (spec §3). Re-exportados
# aquí: la verdad del eval y la clasificación del producto son el MISMO código.
# Patrón dual como el resto del repo (main.py:95-108): con `uvicorn
# backend.main:app` desde la raíz, cwd está en sys.path pero `severity.py`
# vive en backend/, así que el import absoluto rompería el arranque.
try:
    from backend.severity import (
        LEVEL_LABELS,
        LEVEL_ORDER,
        SEVERITY_KEYWORDS,
        SEVERITY_TO_LEVEL,
        normalize_level,
    )
except ImportError:  # pragma: no cover
    from severity import (
        LEVEL_LABELS,
        LEVEL_ORDER,
        SEVERITY_KEYWORDS,
        SEVERITY_TO_LEVEL,
        normalize_level,
    )

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
    limit = max(1, min(n_results, 20))

    # Reordenación con cross-encoder: la fusión coseno+léxica da un score grosero
    # y con muchos empates; el cross-encoder lee consulta y registro juntos y
    # decide el orden real. Se le pasa un pool más ancho que `limit` para que
    # haya margen de promover candidatos que la similitud coseno dejó fuera.
    # `score` se conserva tal cual (es el que interpretan QA_MIN_SCORE y la
    # respuesta determinista); el logit del modelo va en `rerank_score`.
    pool = ordered[: max(limit, rerank.POOL_MIN)]
    return rerank.apply(question, pool)[:limit]


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

    # Top-20 de Event_ID del archivo (o de la selección, si /summary viene
    # filtrado): los IDs dominantes que el informe ejecutivo cita como
    # patrón de ataque. Claves como texto (el Excel trae enteros).
    event_id_counts: Dict[str, int] = {}
    if "Event_ID" in df.columns:
        event_id_counts = {str(k): int(v) for k, v in
                           df["Event_ID"].value_counts().head(20).items()}

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

    # --- series temporales SOLO de los sospechosos --------------------------
    # El panel ya situaba en el tiempo TODO el dataset, pero no los eventos
    # marcados: sin este desglose no se ve si los sospechosos se concentran en
    # un dia/hora concreto. Mismo pivote y mismo formato de claves
    # ("YYYY-MM-DD" / "YYYY-MM-DD HH:00") que events_per_* para que el
    # frontend reutilice buildTimeBuckets sin logica paralela. Sin columna
    # Timestamp no se calcula nada: la serie viene vacia, nunca inventada.
    suspicious_per_day_by_level: Dict[str, Dict[str, int]] = {}
    suspicious_per_hour_by_level: Dict[str, Dict[str, int]] = {}
    if suspicious_events and "Timestamp" in df.columns:
        susp_dated = suspicious_rows.dropna(subset=["Timestamp"])
        if len(susp_dated):
            susp_pivot = susp_dated.groupby(
                [susp_dated["Timestamp"].dt.date, "_level"]
            ).size().unstack(fill_value=0)
            for lvl in LEVEL_ORDER:
                if lvl not in susp_pivot.columns:
                    susp_pivot[lvl] = 0
            suspicious_per_day_by_level = {
                str(day): {lvl: int(susp_pivot.loc[day, lvl]) for lvl in LEVEL_ORDER}
                for day in susp_pivot.index
            }

            susp_hour_keys = susp_dated["Timestamp"].dt.strftime("%Y-%m-%d %H")
            susp_pivot_h = susp_dated.groupby([susp_hour_keys, "_level"]).size().unstack(fill_value=0)
            for lvl in LEVEL_ORDER:
                if lvl not in susp_pivot_h.columns:
                    susp_pivot_h[lvl] = 0
            suspicious_per_hour_by_level = {
                f"{stamp}:00": {lvl: int(susp_pivot_h.loc[stamp, lvl]) for lvl in LEVEL_ORDER}
                for stamp in sorted(susp_pivot_h.index)
            }

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
        # Listas completas con tope de 50 (antes top-6: el panel no tenia
        # como enseñar el resto). El tope protege contra archivos con miles
        # de hosts; el total distinct viaja aparte, sin recorte.
        suspicious_correlations = _counts(suspicious_rows["Hostname"], n=50)
    if suspicious_events and "Source_IP" in df.columns and "Hostname" in df.columns:
        grouped_ip = (
            suspicious_rows.groupby("Source_IP")
            .agg(events=("Source_IP", "size"), hosts=("Hostname", "nunique"))
            .sort_values(["hosts", "events"], ascending=False)
            .head(50)
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
            .head(50)
        )
        suspicious_by_ip = [
            {"ip": str(ip), "events": int(n), "hosts": 0}
            for ip, n in grouped_ip.items()
        ]

    # --- totales sin truncar de hosts e IPs sospechosos ---------------------
    # Revisión funcional #1: las listas de arriba van con tope de 50 y el
    # dashboard las estaba mostrando como si fueran los totales («6 hosts»
    # cuando el archivo real tenía 11). Los conteos distinct reales viajan
    # aparte, sin tocar las listas que alimentan las gráficas.
    suspicious_hosts_total = 0
    suspicious_ips_total = 0
    if suspicious_events:
        if "Hostname" in df.columns:
            hosts_series = suspicious_rows["Hostname"].astype(str).str.strip()
            hosts_series = hosts_series[(hosts_series != "") & (hosts_series.str.lower() != "nan")]
            suspicious_hosts_total = int(hosts_series.nunique())
        if "Source_IP" in df.columns:
            ips_series = suspicious_rows["Source_IP"].astype(str).str.strip()
            ips_series = ips_series[(ips_series != "") & (ips_series.str.lower() != "nan")]
            suspicious_ips_total = int(ips_series.nunique())

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
            f"Event IDs dominantes en este nivel: {events_txt}. "
            f"Hosts con más registros en este nivel: {hosts_txt}."
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

    # --- correlaciones conductuales (behavior_correlations.py) -------------
    # Motor ÚNICO que comparten el informe (el prompt y la regla C de
    # «correlation» los citan) y el panel de correlaciones ATT&CK. Se calcula
    # aquí para que /summary y el informe vean exactamente los mismos patrones.
    behavior_patterns = compute_behavior_patterns(df)

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
        "event_id_counts": event_id_counts,
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
        "suspicious_hosts_total": suspicious_hosts_total,
        "suspicious_ips_total": suspicious_ips_total,
        "suspicious_per_day_by_level": suspicious_per_day_by_level,
        "suspicious_per_hour_by_level": suspicious_per_hour_by_level,
        "behavior_patterns": behavior_patterns,
    }


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------
async def process_analysis(analysis_id: str, file_bytes: bytes, file_name: str = ""):
    # El análisis puede borrarse (botón BORRAR del historial, retención) mientras
    # este proceso de fondo sigue corriendo. La entrada se captura una sola vez y
    # cada escritura a disco se comprueba: sin eso la tarea estallaba con
    # KeyError al escribir en un store que ya no contiene el id, o resucitaba la
    # carpeta que el DELETE acaba de borrar.
    entrada = analysis_store.get(analysis_id)
    if entrada is None:
        return
    try:
        entrada["status"] = "processing"
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
        _enrichir_df(df)
        missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
        summary = call_ai_service(df)
        summary["sheet_names"] = list(sheets.keys())
        summary["missing_columns"] = missing
        summary["file_name"] = file_name
        summary["generated_at"] = datetime.now().isoformat(timespec="seconds")
        # Observaciones automáticas de la IA sobre los datos: el modelo
        # redacta y el sistema valida cada cifra; si falla o está apagado,
        # cae a las locales. Se generan ANTES de marcar «done» para que el
        # /summary pinte directamente las de IA (una sola llamada por
        # análisis, cacheadas en el store).
        summary["ai_insights"] = await generate_data_insights(summary)

        # Keep the classified dataframe so the UI can visualise every record
        entrada["dataframe"] = df
        entrada["summary"] = summary
        entrada["status"] = "done"
    except Exception as exc:  # noqa: BLE001 – surface any failure to the UI
        if analysis_id in analysis_store:
            entrada["status"] = "error"
            entrada["error"] = str(exc)
            # Spec A: el error también se persiste, para que un reinicio no
            # convierta un análisis fallido en «ya no existe».
            analysis_persist.guardar_estado(analysis_id, entrada)
        return

    # Borrado en caliente durante el cálculo: no se persiste ni se indexa.
    if analysis_id not in analysis_store:
        return

    # Spec A: estado y resumen a disco (summary.json) — el dataframe no
    # entra: se reconstruye del Excel al arrancar.
    analysis_persist.guardar_estado(analysis_id, entrada)

    # ------------------------------------------------------------------
    # Índice semántico (RAG) — en un hilo para no bloquear el event loop.
    # Nunca invalida el análisis: si falla, la app sigue en modo determinista.
    # ------------------------------------------------------------------
    records = _enrich_records(df)
    # is_available() consulta el cliente de Chroma: a un hilo, porque otra
    # indexación en curso retiene su candado y esto no debe congelar la API.
    entrada["rag"] = {
        "available": await asyncio.to_thread(rag_store.is_available),
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
        entrada["rag"] = rag_result
        # El índice persiste en disco: nos quedamos solo con los últimos análisis.
        rag_store.purge_except(_recent_analysis_ids(), max_analyses=5)
    except Exception as rag_exc:  # noqa: BLE001
        logger.warning("RAG no disponible para %s: %s", analysis_id[:8], rag_exc)
        entrada["rag"] = {
            "available": False,
            "indexed": 0,
            "total": len(records),
            "reason": str(rag_exc),
        }
    finally:
        _INDEX_TASKS.pop(analysis_id, None)

    # Se vuelve a persistir para que el estado del índice RAG (available,
    # indexed, total, reason) sobreviva también a un reinicio. Solo si el
    # análisis sigue vivo: si se borró durante la indexación, la carpeta ya
    # no debe volver a aparecer.
    if analysis_id in analysis_store:
        analysis_persist.guardar_estado(analysis_id, entrada)


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
    # Spec A: el Excel original es la fuente de verdad en disco; sin él el
    # análisis no podría reconstruirse al reiniciar el backend.
    await asyncio.to_thread(analysis_persist.guardar_excel, analysis_id, content)
    analysis_store[analysis_id] = {
        # Se guarda el propio id: _remember_turn lo usa para etiquetar los
        # ejemplos few-shot que salen de la conversación (auditoría en el JSON).
        "analysis_id": analysis_id,
        "status": "pending",
        "summary": None,
        "error": None,
    }
    # La retención se aplica también en cada subida: así un backend con poca
    # actividad no acumula análisis caducados hasta el próximo arranque.
    try:
        await asyncio.to_thread(aplicar_retencion_config)
    except Exception as exc:  # noqa: BLE001 – limpiar nunca invalida la subida
        logger.warning("Retención no aplicada al subir: %s", exc)
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


@app.get("/api/analyses")
async def list_analyses(limit: int = Query(8, ge=1, le=8)):
    """Historial de subidas (spec sub-3 §3.5): últimos análisis completados.

    Solo `status == 'done'`: un análisis pending/error no se puede reabrir.
    El orden de inserción del dict es cronológico, así que se invierte para
    mostrar lo más reciente primero. No depende de RAG: el dashboard y la
    tabla se alimentan del `analysis_store`, no del índice.
    """
    done = [
        (key, value)
        for key, value in analysis_store.items()
        if value.get("status") == "done"
    ]
    out = []
    for key, value in reversed(done[-limit:]):
        summary = value.get("summary") or {}
        out.append({
            "analysis_id": key,
            "file_name": summary.get("file_name") or "archivo importado",
            "generated_at": summary.get("generated_at"),
            "rows": summary.get("rows", 0),
            "risk": summary.get("risk") or "—",
        })
    return out


@app.get("/api/analysis/{analysis_id}/rows")
async def get_rows(
    analysis_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    """Vista paginada de los registros importados (visualización del Excel).

    El juego completo de filtros (ventana from/to, nivel, sospechosos,
    result, texto libre q y facetas por columna) es el mismo que comparten
    `/summary` y `/facets`: lo traduce `parse_filters` y lo aplica
    `apply_filters`, de modo que la tabla enseña exactamente las mismas
    filas que los KPI del panel. Solo la paginación es exclusiva de aquí.
    Un valor de filtro inválido (nivel fuera de catálogo, booleano o fecha
    ilegibles) responde 400 con el motivo, igual que en `/summary`.
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    df: pd.DataFrame | None = analysis_store[analysis_id].get("dataframe")
    if df is None:
        raise HTTPException(status_code=409, detail="El análisis aún no está listo")

    try:
        # page/page_size viajan SIEMPRE en la URL de la tabla (la UI los
        # envía en cada petición): sin reservarlos, un Excel con una columna
        # «page» se filtraría en silencio por page="1" en cada recarga (R17).
        params = parse_filters(request, df, reserved={"page", "page_size"})
    except ValueError as exc:
        # Filtro imposible (nivel fuera de catálogo, booleano o fecha
        # ilegibles, ventana invertida): 400 con el motivo, igual que en
        # /summary, en vez del silencio anterior (R16/R18).
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # El DataFrame del store ya viene enriquecido desde la ingesta (_level,
    # _reason, _suspicious y Timestamp tipado) y apply_filters no muta su
    # entrada: ni se copia ni se recalcula nada por petición (R3).
    work = apply_filters(df, params)

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
    """Resumen completo del análisis acotado por los filtros de la URL (spec §4.3).

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
        # Filtro imposible (nivel fuera de catálogo, booleano ilegible,
        # ventana invertida o fecha ilegible): 400 con el motivo, nunca un 500 (R10).
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    work = apply_filters(df, params)
    # call_ai_service es pura y copia su entrada: el store nunca se toca y
    # el resumen refleja solo las filas que sobreviven al filtro.
    resumen = call_ai_service(work)
    resumen["total"] = int(len(df))
    resumen["filtered_total"] = int(len(work))
    resumen["rows"] = int(len(work))

    # R14 (spec §4.3 «mismo AnalysisSummary»): estas cuatro claves las añade
    # process_analysis al resumen global y son datos del ARCHIVO/análisis
    # (nombre, hojas, columnas que faltan, fecha de generación): se copian
    # tal cual para que NO cambien mientras el usuario mueve los filtros.
    # El store las garantiza: el endpoint ya exige el DataFrame, que
    # process_analysis guarda en el mismo bloque síncrono que el resumen.
    global_summary = analysis_store[analysis_id]["summary"]
    for clave in ("sheet_names", "missing_columns", "file_name", "generated_at"):
        resumen[clave] = global_summary[clave]

    # ai_insights: las de IA se generan UNA vez al terminar el análisis y
    # viven cacheadas en el store. Si los filtros no han cambiado las filas
    # (misma selección que el archivo) se sirve esa caché — así /summary no
    # espera al modelo en cada movimiento del usuario—; con filtros activos
    # las cifras mostradas cambian y se recalculan en LOCAL sobre la
    # selección, para que las observaciones describan exactamente lo que la
    # UI está pintando. build_context es solo .get() y _local_insights
    # devuelve su lista de «sin registros» con rows=0, así que nunca lanza
    # (R14 exige 200 aunque no haya coincidencias).
    if int(resumen["rows"]) == int(resumen["total"]):
        resumen["ai_insights"] = (
            list(global_summary.get("ai_insights") or [])
            or generate_local_insights(resumen)
        )
    else:
        resumen["ai_insights"] = generate_local_insights(resumen)
    return resumen


@app.get("/api/analysis/{analysis_id}/facets")
async def get_facets(analysis_id: str, request: Request):
    """Campos con sus valores y conteos acotados (spec §4.4) — panel y AutoFilter.

    Dos modos según `fields`:
    - sin `fields`: la detección clásica de `detect_facets` (solo columnas
      categóricas), que es lo que usa el panel de facetas. La detección se
      hace sobre el dataset completo para que la lista de campos sea
      estable: un campo no debe desaparecer del panel solo porque el filtro
      actual lo haya dejado vacío.
    - con `fields=A,B` (AutoFilter de la tabla): se devuelven EXACTAMENTE
      esas columnas aunque `detect_facets` las descarte por cardinalidad o
      longitud (Log_ID, Event_Description…), porque en el desplegable la
      búsqueda (`value_q`) y `top_n` acotan la respuesta.

    Los conteos van sobre las filas filtradas con los mismos filtros que
    /summary, y cada campo se recuenta ignorando la selección de ese mismo
    campo (estilo Splunk): así con un valor activo el panel sigue ofreciendo
    los demás para poder cambiar.
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    df: pd.DataFrame | None = analysis_store[analysis_id].get("dataframe")
    if df is None:
        raise HTTPException(status_code=409, detail="El análisis aún no está listo")

    query = request.query_params
    # `fields`, `top_n` y `value_q` configuran ESTA respuesta; nunca son
    # facetas: van como `reserved` para que parse_filters los ignore (si un
    # Excel tuviera una columna «fields», el AutoFilter de esa columna viaja
    # en la misma clave y solo el reserved evita que se autofiltre).
    try:
        top_n = int(query.get("top_n", "12"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="«top_n» debe ser un entero") from exc
    if not 1 <= top_n <= 300:
        raise HTTPException(
            status_code=400, detail="«top_n» debe estar entre 1 y 300"
        )
    value_q = str(query.get("value_q", "")).strip()

    try:
        params = parse_filters(request, df, reserved={"fields", "top_n", "value_q"})
    except ValueError as exc:
        # Mismos 400 que /summary: una query inválida es inválida para
        # cualquier endpoint, nunca un 500 (R10).
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    solicitados = [n.strip() for n in str(query.get("fields", "")).split(",") if n.strip()]
    if solicitados:
        # Pedidos por la tabla: se conserva el orden pedido, sin duplicados
        # y solo columnas reales del archivo. Las derivadas (_level…) no
        # empiezan por «_» por casualidad: parse_filters nunca las acepta
        # como faceta, así que ofrecerlas engañaría (no filtrarían).
        objetivos = [
            (n, n)
            for n in dict.fromkeys(solicitados)
            if n in df.columns and not n.startswith("_")
        ]
    else:
        objetivos = [(c.name, c.label) for c in detect_facets(df, top_n=top_n)]

    filtrado = apply_filters(df, params)
    campos = []
    for name, label in objetivos:
        parcial = apply_filters(df, params, skip_fields={name})
        campos.append(
            _facet_field(parcial[name], name, label, top_n, contiene=value_q or None)
        )

    return {
        "total": int(len(df)),
        "filtered_total": int(len(filtrado)),
        "fields": campos,
    }


@app.get("/api/analysis/{analysis_id}/report")
async def get_report(analysis_id: str):
    """Sirve el informe ya generado (spec A: se genera UNA sola vez).

    404 si el análisis todavía no tiene informe: el frontend lo pinta como
    «todavía no hay informe» en lugar de regenerarlo en cada visita.
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    narrative = analysis_store[analysis_id].get("report")
    if narrative is None:
        narrative = analysis_persist.cargar_informe(analysis_id)
    if narrative is None:
        raise HTTPException(status_code=404, detail="Report not generated")
    analysis_store[analysis_id]["report"] = narrative
    return narrative


@app.post("/api/analysis/{analysis_id}/report")
async def build_report(analysis_id: str, regenerate: bool = Query(False)):
    """Redacta el informe técnico con la IA, usando solo datos calculados.

    El backend envía al servicio de IA exclusivamente estadísticas y
    evidencias derivadas de los registros importados, de modo que no puede
    haber hallazgos inventados (grounding estricto).

    Spec A: el informe se genera UNA vez. Sin `regenerate` se devuelve el
    cacheado (memoria o report.json) sin tocar la IA; `?regenerate=true`
    (botón REGENERAR del frontend) fuerza una versión nueva.
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    entry = analysis_store[analysis_id]
    if entry.get("status") != "done" or not entry.get("summary"):
        raise HTTPException(status_code=409, detail="El análisis aún no está listo")
    if not regenerate:
        narrative = entry.get("report") or analysis_persist.cargar_informe(analysis_id)
        if narrative is not None:
            entry["report"] = narrative
            return narrative
    narrative = await generate_report_narrative(entry["summary"])
    entry["report"] = narrative
    analysis_persist.guardar_informe(analysis_id, narrative)
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


class FeedbackRequest(BaseModel):
    """Corrección del analista sobre una respuesta del asistente (few-shot)."""
    question: str = ""
    corrected_answer: str = ""  # respuesta correcta escrita por el analista
    bad_answer: str = ""        # respuesta del modelo que se descarta (auditable)


async def _ensure_semantic_index(
    analysis_id: str, entry: Dict[str, Any], df: pd.DataFrame | None
) -> bool:
    """Autorepara el índice semántico si ha desaparecido; no bloquea.

    El índice vive en disco y se comparte con las suites de prueba, que purgan
    lo que no usan: si el análisis ya está hecho pero sus eventos no están
    indexados, se reconstruye en un hilo. Devuelve ``True`` si hay una
    indexación en curso (la propia de la subida o esta), para que el llamador
    pueda advertir de que la respuesta usa solo recuperación léxica.

    Las consultas al cliente de Chroma (`is_available`, `has_index`) van a
    hilos: mientras el índice se reconstruye, Chroma retiene su candado y
    ejecutar esas llamadas dentro del event loop congelaría TODA la API.
    """
    running = _INDEX_TASKS.get(analysis_id)
    if running is not None and not running.done():
        return True
    if df is None or df.empty or not await asyncio.to_thread(rag_store.is_available):
        return False
    if await asyncio.to_thread(rag_store.has_index, analysis_id):
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
            entry["rag"] = {"available": await asyncio.to_thread(rag_store.is_available),
                            "indexed": 0, "total": len(df), "reason": str(exc)}
        finally:
            _INDEX_TASKS.pop(analysis_id, None)
            # Spec A: el estado del índice también vive en disco; sin este
            # guardado, un arranque posterior restauraría el «indexando»
            # previo a la reparación.
            try:
                analysis_persist.guardar_estado(analysis_id, entry)
            except Exception:  # noqa: BLE001 – persistir nunca rompe la consulta
                logger.debug("No se pudo persistir el estado del índice", exc_info=True)

    _INDEX_TASKS[analysis_id] = asyncio.create_task(_reindex())
    return True


async def _ask_prep(analysis_id: str, payload: AskRequest) -> Dict[str, Any]:
    """Valida una consulta del asistente y prepara el contexto compartido.

    Lo usan el endpoint normal (/ask) y el de streaming (/ask/stream) para que
    ambas rutas apliquen exactamente las mismas reglas: validación, aviso de
    índice en construcción y recuperación híbrida idéntica.

    Las consultas a Chroma (`get_stats`, `status`, `has_index`) se ejecutan en
    hilos: mientras el índice se reconstruye (~2.5 min embebiendo 715 eventos),
    Chroma retiene su candado y llamarlo dentro del event loop congelaría toda
    la API, hasta peticiones sin relación con RAG.
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    entry = analysis_store[analysis_id]
    if entry.get("status") != "done" or not entry.get("summary"):
        raise HTTPException(status_code=409, detail="El análisis aún no está listo")

    question = (payload.question or "").strip()
    if len(question) < 3:
        raise HTTPException(status_code=400, detail="Escribe una consulta de al menos 3 caracteres")
    # Tope de longitud: la UI ya limita a 300, pero por API podría llegar una
    # consulta enorme y rellenar el prompt entero del modelo (prompt flooding).
    question = question[:500]

    n_results = max(1, min(int(payload.n_results or 8), 20))
    level = payload.level if payload.level in LEVEL_ORDER else ""
    filters = {"level": level or None, "suspicious": bool(payload.suspicious)}
    common = {
        "analysis_id": analysis_id,
        "question": question,
        "filters": filters,
        "rag": await asyncio.to_thread(rag_store.get_stats, analysis_id),
        "kb": await asyncio.to_thread(kb_store.status),
    }

    # Autoreparación del índice (no bloquea): se comprueba SIEMPRE, también en
    # las consultas de agregación, para que el índice vuelva a estar completo.
    df: pd.DataFrame | None = entry.get("dataframe")
    rag_state = entry.get("rag") or {}
    indexing = await _ensure_semantic_index(analysis_id, entry, df)

    # Si el índice semántico todavía no está listo (acaba de subirse o se está
    # reconstruyendo), se avisa en la respuesta: lo recuperado procede solo de
    # la búsqueda léxica. Nunca se bloquea la petición esperando al índice.
    index_note = ""
    if (indexing or rag_state.get("reason") == "indexando") and not await asyncio.to_thread(
        rag_store.has_index, analysis_id
    ):
        total = int(rag_state.get("total") or len(df) or 0)
        index_note = (
            f"El índice semántico local se está construyendo ({total} eventos); "
            "mientras tanto la recuperación es solo léxica sobre tus registros. "
            "Vuelve a preguntar en unos segundos para la recuperación semántica."
        )

    return {
        "entry": entry,
        "question": question,
        "n_results": n_results,
        "level": level,
        "suspicious": bool(payload.suspicious),
        "common": common,
        "df": df,
        "index_note": index_note,
    }


def _chat_history(entry: Dict[str, Any]) -> List[Dict[str, str]]:
    """Turnos de conversación guardados en RAM para este análisis (0 si no hay)."""
    return entry.get("chat_history") or []


def _remember_turn(entry: Dict[str, Any], question: str, answer: str) -> None:
    """Guarda un turno (pregunta + respuesta) para la memoria multi-turno.

    La memoria es deliberadamente corta: solo los últimos CHAT_HISTORY_TURNS
    turnos y cada respuesta acortada a QA_HISTORY_ANSWER_CHARS, porque la
    ventana de contexto del modelo es finita. Vive en el propio análisis y se
    borra con DELETE .../chat o al borrar el análisis (spec A: desde el
    arranque los turnos también se persisten en summary.json, así que
    sobreviven a un reinicio del backend).
    """
    if not answer or not question:
        return
    history = entry.setdefault("chat_history", [])
    # Señal implícita de feedback: si la consulta anterior fue del LLM y este
    # nuevo turno vuelve sobre el mismo tema, la respuesta previa fue útil ⇒
    # ejemplo positivo para el few-shot. Se salta la primera consulta (no hay
    # turno anterior) y las de agregación (respuesta del motor, no del modelo).
    prev = history[-1] if history else None
    if prev and not is_aggregate_question(question) and not is_aggregate_question(prev["question"]):
        try:
            feedback_store.remember_implicit(
                prev["question"], prev["answer"],
                analysis_id=str(entry.get("analysis_id") or ""),
                next_question=question,
            )
        except Exception:  # noqa: BLE001 – el feedback nunca rompe la conversación
            logger.debug("feedback implícito descartado", exc_info=True)
    history.append({"question": question[:400], "answer": answer[:QA_HISTORY_ANSWER_CHARS]})
    del history[:-CHAT_HISTORY_TURNS]
    # Spec A: el turno se refleja en disco (summary.json) para que la
    # conversación sobreviva a un reinicio del backend.
    analysis_id = str(entry.get("analysis_id") or "")
    if analysis_id:
        analysis_persist.guardar_estado(analysis_id, entry)


@app.post("/api/analysis/{analysis_id}/ask")
async def ask_question(analysis_id: str, payload: AskRequest):
    """Consulta en lenguaje natural sobre los eventos importados (RAG).

    Recuperación híbrida (semántica + léxica) y respuesta anclada a los
    eventos recuperados: cada cifra y cada Log_ID procede de los registros,
    nunca del modelo. Las consultas de agregación (conteos, distribución,
    hosts dominantes) se responden con cifras exactas del resumen del dataset.

    La conversación tiene memoria multi-turno (últimos turnos en RAM por
    análisis), así que se puede preguntar «¿y esos mismos con IP externa?».
    """
    prep = await _ask_prep(analysis_id, payload)
    entry, question, common = prep["entry"], prep["question"], prep["common"]

    # --- Cortes de intención: saludo o inyección, sin recuperación ni modelo
    rapida = quick_answer(question)
    if rapida is not None:
        _remember_turn(entry, question, rapida["answer"])
        return {**common, "retrieved": 0, **rapida}

    # --- Ruta de fichas de ataque: cifras exactas de la ficha, SIN recuperación
    # (retrieved = 0): se corta antes del RAG igual que los agregados.
    if prep["df"] is not None and routes_pattern(question):
        answer = await answer_question(
            question, [], entry["summary"], reference=None, df=prep["df"],
        )
        _remember_turn(entry, question, str(answer.get("answer") or ""))
        return {**common, "retrieved": 0, **answer}

    # --- Consulta de agregación: cifras exactas, sin recuperación -----------
    # Solo si además habla del archivo: «¿cuántos habitantes tiene España?»
    # contiene «cuantos» pero no dice nada de los registros.
    if routes_aggregate(question, entry["summary"]):
        answer = await answer_question(question, [], entry["summary"], reference=None)
        _remember_turn(entry, question, str(answer.get("answer") or ""))
        return {**common, "retrieved": 0, **answer}

    # --- Listado por nivel: Log_ID exactos del dataframe, SIN recuperación
    # (retrieved = 0): se corta antes del RAG igual que los agregados.
    if routes_list_level(question, prep["df"]):
        answer = await answer_question(
            question, [], entry["summary"], reference=None, df=prep["df"],
        )
        _remember_turn(entry, question, str(answer.get("answer") or ""))
        return {**common, "retrieved": 0, **answer}

    # Recuperación + reordenación fuera del event loop: el cross-encoder del
    # rerank tarda ~1 s y bloquearía el resto de peticiones del proceso.
    hits = await asyncio.to_thread(
        _hybrid_retrieve,
        analysis_id, question, prep["df"], n_results=prep["n_results"],
        level=prep["level"], suspicious=prep["suspicious"],
    )

    # El asistente responde SOLO con la evidencia del dataset cargado: ni
    # glosario de Event IDs ni técnicas ATT&CK (conocimiento ajeno al archivo).
    # Antes aquí se construía `kb_store.build_reference` y el prompt del chat
    # lo inyectaba como «Referencia técnica»; el análisis ya no lo pide.
    answer = await answer_question(
        question, hits, entry["summary"], reference=None,
        index_note=prep["index_note"], history=_chat_history(entry),
    )
    _remember_turn(entry, question, str(answer.get("answer") or ""))

    return {**common, "retrieved": len(hits), **answer}


@app.post("/api/analysis/{analysis_id}/ask/stream")
async def ask_question_stream(analysis_id: str, payload: AskRequest):
    """Versión en streaming de /ask (SSE con eventos con nombre).

    Emite, en este orden: `meta` (evidencias ya disponibles,
    mientras el modelo empieza a escribir), 0..n eventos `token` y un cierre
    `final` (respuesta del LLM validada contra los Log_ID recuperados) o
    `fallback` (la respuesta determinista sustituye a la del modelo si éste se
    cayó a mitad o citó Log_ID inexistentes). Como en /ask, SIEMPRE termina
    con una respuesta utilizable; cada evento lleva los mismos campos que la
    respuesta completa, más el tipo en la clave `kind`.
    """
    t0 = time.perf_counter()
    prep = await _ask_prep(analysis_id, payload)
    t_prep = time.perf_counter() - t0
    entry, question, common = prep["entry"], prep["question"], prep["common"]

    async def frames():
        def _sse(kind: str, data: Dict[str, Any]) -> Dict[str, str]:
            # `default=str` tolera escalares numpy/pandas del dataset sin
            # tumbar el stream (json.dumps solo serializa tipos nativos).
            return {
                "event": kind,
                "data": json.dumps({"kind": kind, **data}, ensure_ascii=False, default=str),
            }

        # Cortes de intención (saludo / inyección): respuesta inmediata, sin
        # recuperación ni modelo, igual que en /ask.
        rapida = quick_answer(question)
        if rapida is not None:
            _remember_turn(entry, question, rapida["answer"])
            yield _sse("final", {**common, "retrieved": 0, **rapida})
            return

        # Ruta de fichas de ataque: una sola respuesta final con las cifras
        # exactas de la ficha, sin recuperación (mismo corte que /ask).
        if prep["df"] is not None and routes_pattern(question):
            answer = await answer_question(
                question, [], entry["summary"], reference=None, df=prep["df"],
            )
            _remember_turn(entry, question, str(answer.get("answer") or ""))
            yield _sse("final", {**common, "retrieved": 0, **answer})
            return

        # Agregaciones: una sola respuesta con cifras exactas, sin streaming.
        # Solo si la consulta además habla del archivo (mismo criterio que /ask).
        if routes_aggregate(question, entry["summary"]):
            answer = await answer_question(question, [], entry["summary"], reference=None)
            _remember_turn(entry, question, str(answer.get("answer") or ""))
            yield _sse("final", {**common, "retrieved": 0, **answer})
            return

        # Listado por nivel: una sola respuesta final con los Log_ID exactos,
        # sin recuperación (mismo corte que /ask).
        if routes_list_level(question, prep["df"]):
            answer = await answer_question(
                question, [], entry["summary"], reference=None, df=prep["df"],
            )
            _remember_turn(entry, question, str(answer.get("answer") or ""))
            yield _sse("final", {**common, "retrieved": 0, **answer})
            return

        t1 = time.perf_counter()
        hits = await asyncio.to_thread(
            _hybrid_retrieve,
            analysis_id, question, prep["df"], n_results=prep["n_results"],
            level=prep["level"], suspicious=prep["suspicious"],
        )
        t2 = time.perf_counter()
        # Solo evidencia del dataset: sin referencia del marco (KB), igual que /ask.
        timed = False

        async for event in stream_question(
            question, hits, entry["summary"], reference=None,
            index_note=prep["index_note"], history=_chat_history(entry),
        ):
            kind = str(event.get("type") or "")
            body = {k: v for k, v in event.items() if k != "type"}
            if kind == "meta":
                if not timed:
                    # Desglose de la fase «invisible» (hasta que aparecen las
                    # evidencias): prep = validación+índice, recuperación =
                    # RAG+léxica+rerank.
                    timed = True
                    logger.info(
                        "timing /ask/stream: prep=%.2fs recuperación=%.2fs "
                        "→ meta=%.2fs",
                        t_prep, t2 - t1, time.perf_counter() - t0,
                    )
                yield _sse("meta", {**common, "retrieved": len(hits), "engine": "streaming", **body})
            elif kind == "token":
                yield _sse("token", {"text": body.get("text", "")})
            else:  # final | fallback: la respuesta emitida es la definitiva
                _remember_turn(entry, question, str(body.get("answer") or ""))
                yield _sse(kind, {**common, "retrieved": len(hits), **body})

    return EventSourceResponse(frames())


@app.post("/api/analysis/{analysis_id}/feedback")
async def submit_feedback(analysis_id: str, payload: FeedbackRequest):
    """Registra la corrección del analista sobre una respuesta del asistente.

    Es el motor del few-shot: enseña al modelo la respuesta BUENA para esa
    consulta, que se inyecta como criterio en consultas futuras afines. El
    backend sigue funcionando sin esto (el fallback determinista no cambia),
    pero con cada corrección el asistente aprende el estándar del equipo sin
    reentrenar el modelo.
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    question = (payload.question or "").strip()
    corrected = (payload.corrected_answer or "").strip()
    if not question or not corrected:
        raise HTTPException(
            status_code=422,
            detail="question y corrected_answer son obligatorios",
        )
    saved = await asyncio.to_thread(
        feedback_store.add_correction,
        question, corrected, (payload.bad_answer or "").strip(), analysis_id,
    )
    stats = await asyncio.to_thread(feedback_store.status)
    return {"ok": saved is not None, "example": saved, "stats": stats}


@app.get("/api/feedback")
async def feedback_status():
    """Conteo de la memoria de criterio para el panel «Opciones IA».

    `status()` consulta Chroma: fuera del event loop, que el índice de
    feedback esté ocupado no debe congelar la API.
    """
    return await asyncio.to_thread(feedback_store.status)


@app.delete("/api/feedback")
async def feedback_clear():
    """Vacía la memoria de criterio aprendido (ejemplos few-shot)."""
    removed = await asyncio.to_thread(feedback_store.clear)
    stats = await asyncio.to_thread(feedback_store.status)
    return {"ok": True, "removed": removed, "stats": stats}


@app.delete("/api/analysis/{analysis_id}/chat")
async def clear_chat_history(analysis_id: str):
    """Borra la memoria de conversación (turnos) de un análisis."""
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    analysis_store[analysis_id]["chat_history"] = []
    # Spec A: el vaciado se refleja en disco, si no el próximo arranque
    # resucitaría la conversación borrada.
    analysis_persist.guardar_estado(analysis_id, analysis_store[analysis_id])
    return {"ok": True}


@app.delete("/api/analyses/{analysis_id}")
async def delete_analysis(analysis_id: str):
    """Borra un análisis del historial (spec A): memoria, disco e índice RAG.

    Lo usa el botón de borrado del panel de historial. Un análisis que no
    está en el store devuelve 404 (no se pueden borrar fantasmas).
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    analysis_store.pop(analysis_id, None)
    _INDEX_TASKS.pop(analysis_id, None)
    await asyncio.to_thread(analysis_persist.borrar, analysis_id)
    try:
        await asyncio.to_thread(rag_store.delete_analysis, analysis_id)
    except Exception as exc:  # noqa: BLE001 – el borrado del índice es best effort
        logger.warning("Índice RAG no borrado para %s: %s", analysis_id[:8], exc)
    return {"ok": True}


def _observed_event_stats(df: pd.DataFrame | None) -> List[Dict[str, Any]]:
    """Recuento por Event_ID de los registros EN MEMORIA (sin tocar disco).

    Para cada Event_ID observado devuelve cuántos registros hay, cuántos están
    ya marcados como sospechosos, su reparto por nivel y los hosts implicados.
    No se deduce nada nuevo: es un agrupado de la clasificación que el motor ya
    hizo al ingerir el archivo, y es lo que sustenta la correlación ATT&CK.
    """
    if df is None or df.empty or "Event_ID" not in df.columns:
        return []

    work = df.copy()
    work["_event"] = work["Event_ID"].astype(str).str.strip()
    work = work[~work["_event"].isin(["", "nan", "None", "<NA>", "—"])]
    if work.empty:
        return []

    work["_level"] = (
        work["Severity"].map(normalize_level) if "Severity" in work.columns else "bajo"
    )
    work["_reason"] = work.apply(row_reason, axis=1)
    work["_suspicious"] = work.apply(_is_suspicious, axis=1)

    out: List[Dict[str, Any]] = []
    for event_id, grp in work.groupby("_event", sort=False):
        item: Dict[str, Any] = {
            "event_id": str(event_id),
            "count": int(len(grp)),
            "suspicious": int(grp["_suspicious"].sum()),
            "levels": dict(sorted(
                ((str(k), int(v)) for k, v in grp["_level"].value_counts().items()),
                key=lambda kv: (-kv[1], kv[0]),
            )),
        }
        if "Hostname" in grp.columns:
            item["hosts"] = sorted({str(h) for h in grp["Hostname"].dropna().unique()})[:8]
        if "Timestamp" in grp.columns:
            stamps = pd.to_datetime(grp["Timestamp"], errors="coerce").dropna()
            if len(stamps):
                item["first_seen"] = stamps.min().strftime("%Y-%m-%d %H:%M")
                item["last_seen"] = stamps.max().strftime("%Y-%m-%d %H:%M")
        out.append(item)

    out.sort(key=lambda s: (-s["count"], s["event_id"]))
    return out


@app.get("/api/analysis/{analysis_id}/attack/correlations")
async def attack_correlations(analysis_id: str) -> Dict[str, Any]:
    """Correlaciones ATT&CK de los registros de este análisis (determinista).

    Cruza los Event_ID que existen en el dataset en memoria con el glosario y
    las técnicas MITRE de la base de conocimiento, y devuelve también los
    Event_ID que no tienen entrada en el glosario (`unmapped`) en lugar de
    inventarles una correlación. Ni modelo ni similitud de por medio: es una
    referencia del marco anclada a tus datos, con los recuentos reales.

    Añade `behavior_patterns`: los cruces conductuales del MISMO motor que
    alimenta el campo «correlation» del informe (IP en varios tipos de
    evento, combinaciones IP + host + evento repetidas, IPs y usuarios
    multi-host y candidatos a fuerza bruta). El JOIN MITRE sigue siendo
    referencia del marco; los patrones son hallazgo sobre tus datos.
    """
    if analysis_id not in analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    entry = analysis_store[analysis_id]
    if entry.get("status") != "done":
        raise HTTPException(status_code=409, detail="El análisis aún no está listo")

    df = entry.get("dataframe")
    observed = await asyncio.to_thread(_observed_event_stats, df)
    correlation = await asyncio.to_thread(kb_store.correlate, observed)
    patterns = await asyncio.to_thread(compute_behavior_patterns, df)

    return {
        "analysis_id": analysis_id,
        "rows": int(len(df)) if df is not None else 0,
        "observed_events": len(observed),
        "observed_rows": sum(int(item.get("count") or 0) for item in observed),
        **correlation,
        "behavior_patterns": patterns,
    }


@app.get("/api/kb/status")
async def kb_status():
    """Estado de la base de conocimiento (técnicas ATT&CK + Event IDs).

    Responde siempre 200: si faltan dependencias opcionales devuelve
    `available: false` con el motivo y la app sigue operativa.
    `status()` consulta Chroma: va a un hilo para no congelar el event loop.
    """
    return await asyncio.to_thread(kb_store.status)


@app.get("/api/rag/status")
async def rag_status(analysis_id: str = Query("", description="Análisis concreto (opcional)")):
    """Estado del índice semántico local (motor + eventos indexados).

    Responde siempre 200: si el motor no está instalado devuelve
    `available: false` y la app sigue operativa en modo determinista.
    `get_stats()` consulta Chroma: va a un hilo para no congelar el event
    loop mientras el índice se reconstruye.
    """
    info = await asyncio.to_thread(rag_store.get_stats, analysis_id or None)
    if analysis_id and analysis_id not in analysis_store:
        info["known_analysis"] = False
    return info


# ---------------------------------------------------------------------------
# Panel «Opciones IA»: configuración del LLM (proveedor cloud compatible con
# OpenAI; sin parámetros locales de Ollama)
# ---------------------------------------------------------------------------
class AiConfigPatch(BaseModel):
    """Parche parcial de la configuración de la IA (todos los campos opcionales)."""

    enabled: bool | None = None
    api_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float | None = None
    timeout: float | None = None


def _ai_envelope(cfg: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
    """Configuración + sonda de estado para el panel (respuesta de GET y PUT).

    `model_installed`: True/False si el proveedor lista modelos y el
    configurado está (o no) entre ellos; None si aún no hay URL o la sonda no
    pudo leer el listado.
    """
    installed: bool | None = None
    names = [str(m.get("name", "")) for m in status.get("models") or []]
    if names:
        want = cfg["model"]
        installed = any(n == want or n.startswith(want + ":") for n in names)
    return {"config": cfg, "status": status, "model_installed": installed}


@app.get("/api/ai/config")
async def get_ai_config():
    """Configuración vigente del LLM + estado real del proveedor (modelos).

    Responde siempre 200: si el proveedor está caído, `status.reachable: false`
    con el motivo, y la app sigue operativa con el motor determinista.
    """
    cfg = ai_config.get()
    status = await ai_config.probe(cfg)
    return _ai_envelope(cfg, status)


@app.put("/api/ai/config")
async def put_ai_config(patch: AiConfigPatch):
    """Guarda la configuración del LLM (persistida en disco, aplica al instante).

    El proveedor recibe las opciones por request, así que no hace falta
    reiniciar nada: el próximo informe o chat ya usa los valores nuevos.
    """
    try:
        cfg = ai_config.update(patch.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    status = await ai_config.probe(cfg)
    return _ai_envelope(cfg, status)


@app.post("/api/ai/test")
async def test_ai_connection(patch: AiConfigPatch | None = None):
    """Prueba de conexión real: sonda de estado + ping al modelo midiendo latencia.

    Acepta (opcional) los valores del panel para probarlos ANTES de guardarlos.
    """
    override = patch.model_dump(exclude_none=True) if patch else {}
    return await ai_config.test_connection(override)
