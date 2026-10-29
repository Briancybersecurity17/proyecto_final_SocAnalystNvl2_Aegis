"""Bench A/B de Ollama: prompt mínimo vs real, num_ctx 4096 vs 2048.

Mide la latencia pura del LLM sin pasar por el backend.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:3b"

BIG_PROMPT = (
    "Responde la consulta usando SOLO estos eventos recuperados.\n"
    "Cita los Log_ID entre comillas simples. Si no hay evidencia suficiente, dilo.\n\n"
    "CONSULTA:\n¿Hubo intentos de fuerza bruta?\n\nEVENTOS RECUPERADOS:\n"
    + json.dumps([
        {"log_id": str(i), "timestamp": "2026-01-10T09:30:00", "host": "WS-018",
         "event_id": "4625", "user": "jsmith", "source_ip": "10.10.2.15",
         "destination": "WS-018", "severity": "High", "level": "alto",
         "suspicious": True, "description": "An account failed to log on with an invalid account",
         "result": "Failure", "relevance": 0.65,
         "document": "[REGISTRO] Log_ID: 1 | Timestamp: 2026-01-10T09:30:00 | Hostname: WS-018 | Event_ID: 4625 | User: jsmith | Source_IP: 10.10.2.15 | Destination: WS-018 | Log_Source: Windows | Result: Failure | Severity: High\n[DESC] An account failed to log on with an invalid account\n[ANÁLISIS] Nivel: Alto — severidad «High» · resultado «Failure» · intentos de autenticación fallidos"}
        for i in range(1, 9)
    ], ensure_ascii=False)
)

CASES = [
    ("min  ctx4096", [{"role": "user", "content": "Di: OK"}], 4096),
    ("big  ctx4096", [{"role": "user", "content": BIG_PROMPT}], 4096),
    ("big  ctx2048", [{"role": "user", "content": BIG_PROMPT}], 2048),
    ("big  ctx2048", [{"role": "user", "content": BIG_PROMPT}], 2048),  # repetido = caliente
]


def run(label: str, messages, ctx: int) -> None:
    payload = {
        "model": MODEL, "messages": messages, "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.15, "num_ctx": ctx, "num_predict": 48},
    }
    t0 = time.perf_counter()
    with httpx.Client(timeout=180) as client:
        d = client.post(URL, json=payload).json()
    total = (time.perf_counter() - t0) * 1000
    prefill = d.get("prompt_eval_duration", 0) / 1e6
    evc = d.get("eval_count", 0)
    evd = d.get("eval_duration", 0) / 1e6
    print(f"{label:14s} total={total:7.0f}ms prefill={prefill:7.0f}ms({d.get('prompt_eval_count', 0):5d} tok) "
          f"gen={evd:6.0f}ms({evc:3d} tok) {evc / max(evd / 1000, 0.001):5.1f} tok/s")


if __name__ == "__main__":
    for label, msgs, ctx in CASES:
        run(label, msgs, ctx)
