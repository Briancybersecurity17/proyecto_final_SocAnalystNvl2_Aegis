import uuid
import asyncio
from datetime import date, datetime
from io import BytesIO
from typing import Any, Dict, List

import pandas as pd
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# El servicio de IA se importa como paquete (uvicorn backend.main:app) o como
# módulo suelto (test_backend.py añade /backend al sys.path).
try:
    from backend.ai_service import generate_report_narrative
except ImportError:  # pragma: no cover
    from ai_service import generate_report_narrative

app = FastAPI(title="Comunidad Dojo – SOC Analyst API")

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
SEVERITY_TO_LEVEL: Dict[str, str] = {
    "critical": "critico",
    "high": "alto",
    "medium": "medio",
    "warning": "medio",
    "low": "bajo",
    "informational": "bajo",
    "info": "bajo",
}
LEVEL_ORDER = ["critico", "alto", "medio", "bajo"]
LEVEL_LABELS = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "bajo": "Bajo"}

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
    """Map a raw severity value to one of our four risk levels."""
    return SEVERITY_TO_LEVEL.get(str(severity).strip().lower(), "bajo")


def row_reason(row: pd.Series) -> str:
    """Grounded justification for a single record (severity + keywords + event id)."""
    reasons: List[str] = []
    severity = str(row.get("Severity", "")).strip()
    if severity:
        reasons.append(f"severidad «{severity}»")
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
    columns = list(df.columns)
    unique_hosts = int(df["Hostname"].nunique()) if "Hostname" in df.columns else None

    # --- severity / level classification ----------------------------------
    raw_severity = df["Severity"] if "Severity" in df.columns else pd.Series([""] * total)
    df["_level"] = raw_severity.map(normalize_level) if "Severity" in df.columns else "bajo"
    severity_counts = _counts(raw_severity) if "Severity" in df.columns else {}
    level_counts = {lvl: int((df["_level"] == lvl).sum()) for lvl in LEVEL_ORDER}

    # --- timestamps --------------------------------------------------------
    events_per_day: Dict[str, int] = {}
    events_per_day_by_level: Dict[str, Dict[str, int]] = {}
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

    suspicious_correlations: Dict[str, int] = {}
    suspicious_by_ip: List[Dict[str, Any]] = []
    if suspicious_events and "Hostname" in df.columns:
        suspicious_correlations = _counts(suspicious_rows["Hostname"], n=6)
    if suspicious_events and "Source_IP" in df.columns:
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

    # --- risk level with data-grounded justification -----------------------
    highest = next((lvl for lvl in LEVEL_ORDER if level_counts[lvl] > 0), None)
    if highest is None:
        risk = "bajo"
        risk_justification = "Sin registros clasificados en el archivo analizado."
    else:
        risk = highest
        sub = df[df["_level"] == highest]
        top_events = sub["Event_ID"].value_counts().head(3)
        top_hosts_high = sub["Hostname"].value_counts().head(3)
        events_txt = ", ".join(f"{e} ({int(c)})" for e, c in top_events.items()) if len(top_events) else "n/d"
        hosts_txt = ", ".join(f"{h} ({int(c)})" for h, c in top_hosts_high.items()) if len(top_hosts_high) else "n/d"
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
            top_events = sub["Event_ID"].value_counts().head(3)
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
        "risk": risk,
        "risk_justification": risk_justification,
        "severity_counts": severity_counts,
        "level_counts": level_counts,
        "risk_levels": risk_levels,
        "unique_hosts": unique_hosts,
        "events_per_day": events_per_day,
        "events_per_day_by_level": events_per_day_by_level,
        "top_hosts": top_hosts,
        "top_hosts_by_level": top_hosts_by_level,
        "suspicious_events": suspicious_events,
        "suspicious_details": suspicious_details,
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
        missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
        summary = call_ai_service(df)
        summary["sheet_names"] = list(sheets.keys())
        summary["missing_columns"] = missing
        summary["file_name"] = file_name
        summary["generated_at"] = datetime.now().isoformat(timespec="seconds")

        # Keep the classified dataframe so the UI can visualise every record
        analysis_store[analysis_id]["dataframe"] = df
        analysis_store[analysis_id]["summary"] = summary
        analysis_store[analysis_id]["status"] = "done"
    except Exception as exc:  # noqa: BLE001 – surface any failure to the UI
        analysis_store[analysis_id]["status"] = "error"
        analysis_store[analysis_id]["error"] = str(exc)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class AnalysisResult(BaseModel):
    analysis_id: str
    status: str  # pending | processing | done | error
    summary: Dict[str, Any] | None = None
    error: str | None = None


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
    )


@app.get("/api/analysis/{analysis_id}/rows")
async def get_rows(
    analysis_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    q: str = Query("", max_length=200),
    level: str = Query("", description="critico|alto|medio|bajo"),
    suspicious: bool = Query(False),
):
    """Paginated view of the imported records (visualisation of the Excel).

    Supports a free-text search (q), a risk-level filter and a
    "suspicious only" toggle – everything is computed server-side.
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
    if len(work):
        work["_suspicious"] = work.apply(_is_suspicious, axis=1)

    if level in LEVEL_ORDER:
        work = work[work["_level"] == level]
    if suspicious:
        work = work[work["_suspicious"]]
    if q.strip():
        needle = q.strip().lower()
        mask = work.apply(
            lambda r: any(needle in str(v).lower() for v in r.values), axis=1
        )
        work = work[mask]

    total_filtered = len(work)
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
