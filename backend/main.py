import uuid
import asyncio
from io import BytesIO
from typing import Dict, Any

from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import httpx

app = FastAPI()

# Allow the Vite dev server to call the API
# NOTE: explicit origins – "*" + credentials is an invalid/unsafe combination.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5300", "http://127.0.0.1:5300"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In‑memory store – replace with a DB for production
analysis_store: Dict[str, Dict[str, Any]] = {}

# Columns the analysis expects (used for validation & friendly errors)
EXPECTED_COLUMNS = [
    "Log_ID", "Timestamp", "Hostname", "Event_ID", "User", "Source_IP",
    "Destination", "Log_Source", "Event_Description", "Result", "Severity",
]

class AnalysisResult(BaseModel):
    analysis_id: str
    status: str  # pending | processing | done | error
    summary: Dict[str, Any] | None = None
    error: str | None = None

def call_ai_service(dataframe: pd.DataFrame) -> Dict[str, Any]:
    """Placeholder for the real AI/RAG call.
    Replace this with a request to your `sistema‑delitos‑argentina` endpoint.
    """
    # Basic statistics
    rows = len(dataframe)
    cols = list(dataframe.columns)
    # Severity distribution (assumes a column named 'Severity')
    severity_counts = {}
    if "Severity" in dataframe.columns:
        severity_counts = dataframe["Severity"].value_counts().to_dict()
    # Unique hosts (assumes a column named 'Hostname')
    unique_hosts = dataframe["Hostname"].nunique() if "Hostname" in dataframe.columns else None
    # Events per day (assumes a column named 'Timestamp' with datetime values)
    events_per_day = {}
    if "Timestamp" in dataframe.columns:
        # Ensure datetime
        df = dataframe.copy()
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
        events_per_day = df["Timestamp"].dt.date.value_counts().sort_index().astype(int).to_dict()
        # Convert date keys to ISO strings for JSON friendliness
        events_per_day = {str(k): v for k, v in events_per_day.items()}
    # Host event counts (top 5 hosts)
    host_counts = {}
    if "Hostname" in dataframe.columns:
        host_counts = dataframe["Hostname"].value_counts().head(5).to_dict()
    # Determine a simple risk level based on severity presence
    risk = "low"
    if severity_counts:
        if any(k.lower() in ["critical", "high"] for k in severity_counts.keys()):
            risk = "high"
        elif any(k.lower() in ["medium"] for k in severity_counts.keys()):
            risk = "medium"
    # Risk justification text
    risk_justification = ""
    if risk == "high":
        risk_justification = "High risk: critical or high severity events detected in the logs."
    elif risk == "medium":
        risk_justification = "Medium risk: presence of medium severity events."
    else:
        risk_justification = "Low risk: no critical or high severity events found."
    # Identify suspicious events (critical/high severity or suspicious keywords in description)
    suspicious_keywords = ["failed", "unauthorized", "malware", "exploit", "alert", "suspicious"]
    suspicious_mask = pd.Series([False] * len(dataframe))
    if "Severity" in dataframe.columns:
        severity_series = dataframe["Severity"].fillna("").astype(str)
        suspicious_mask |= severity_series.str.lower().isin(["critical", "high"])
    if "Event_Description" in dataframe.columns:
        desc_series = dataframe["Event_Description"].fillna("").astype(str).str.lower()
        pattern = "|".join(suspicious_keywords)
        suspicious_mask |= desc_series.str.contains(pattern, na=False)
    suspicious_events = int(suspicious_mask.sum())
    # Capture a few sample suspicious event details
    suspicious_details = []
    if suspicious_events > 0:
        cols_of_interest = [c for c in ["Hostname", "Event_ID", "Event_Description"] if c in dataframe.columns]
        sample = dataframe.loc[suspicious_mask, cols_of_interest].head(5)
        suspicious_details = sample.to_dict(orient="records")
    # Correlate suspicious events per host (top 5)
    suspicious_correlations = {}
    if "Hostname" in dataframe.columns:
        suspicious_correlations = dataframe.loc[suspicious_mask, "Hostname"].value_counts().head(5).to_dict()
    summary = {
        "rows": rows,
        "columns": cols,
        "risk": risk,
        "risk_justification": risk_justification,
        "severity_counts": severity_counts,
        "unique_hosts": unique_hosts,
        "events_per_day": events_per_day,
        "top_hosts": host_counts,
        "suspicious_events": suspicious_events,
        "suspicious_details": suspicious_details,
        "suspicious_correlations": suspicious_correlations,
    }
    return summary


async def process_analysis(analysis_id: str, file_bytes: bytes):
    try:
        # Mark as processing
        analysis_store[analysis_id]["status"] = "processing"
        # Simulate a long‑running AI job (you could stream progress here)
        await asyncio.sleep(1)
        # Read EVERY sheet of the workbook (BytesIO avoids the deprecated
        # "pass bytes to read_excel" behaviour)
        sheets: Dict[str, pd.DataFrame] = pd.read_excel(
            BytesIO(file_bytes), sheet_name=None, engine="openpyxl"
        )
        if not sheets:
            raise ValueError("El archivo Excel no contiene hojas.")
        # Concatenate all sheets (ignore_index) so the whole file is analysed
        df = pd.concat(sheets.values(), ignore_index=True)
        # Friendly validation – warn about missing expected columns
        missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
        analysis_store[analysis_id]["missing_columns"] = missing
        # Call the AI service (replace with real HTTP call)
        result = call_ai_service(df)
        result["sheet_names"] = list(sheets.keys())
        result["missing_columns"] = missing
        # Keep the dataframe so the UI can visualise the imported rows
        analysis_store[analysis_id]["dataframe"] = df
        analysis_store[analysis_id]["status"] = "done"
        analysis_store[analysis_id]["summary"] = result
    except Exception as exc:
        analysis_store[analysis_id]["status"] = "error"
        analysis_store[analysis_id]["error"] = str(exc)

@app.post("/api/analyze", response_model=AnalysisResult)
async def upload_excel(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx files are accepted")
    content = await file.read()
    analysis_id = str(uuid.uuid4())
    analysis_store[analysis_id] = {"status": "pending", "summary": None, "error": None}
    background_tasks.add_task(process_analysis, analysis_id, content)
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

# Optional: simple SSE endpoint for progress (requires sse-starlette)
from sse_starlette.sse import EventSourceResponse

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
