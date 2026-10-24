import uuid
import asyncio
from typing import Dict, Any

from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import httpx

app = FastAPI()

# Allow the Vite dev server to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] ,  # In production restrict this!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In‑memory store – replace with a DB for production
analysis_store: Dict[str, Dict[str, Any]] = {}

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
    summary = {
        "rows": rows,
        "columns": cols,
        "risk": risk,
        "severity_counts": severity_counts,
        "unique_hosts": unique_hosts,
        "events_per_day": events_per_day,
        "top_hosts": host_counts,
    }
    return summary


async def process_analysis(analysis_id: str, file_bytes: bytes):
    try:
        # Mark as processing
        analysis_store[analysis_id]["status"] = "processing"
        # Simulate a long‑running AI job (you could stream progress here)
        await asyncio.sleep(2)  # pretend we are doing work
        # Read Excel (first sheet only for demo)
        df = pd.read_excel(file_bytes, engine="openpyxl")
        # Call the AI service (replace with real HTTP call)
        result = call_ai_service(df)
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
