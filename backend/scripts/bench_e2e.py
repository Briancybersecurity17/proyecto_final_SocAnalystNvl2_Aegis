"""Bench E2E de la demo: ingesta del Excel + latencia real del asistente.

Mide lo que verá quien grabe el vídeo, en el mismo orden en que ocurre:

  1. Subida del Excel → análisis «done» (incluye la indexación RAG).
  2. Varias consultas por `/ask/stream` con el LLM encendido, anotando
     tiempo hasta `meta` (evidencias + tarjetas MITRE), hasta el primer
     token (arranque del modelo) y hasta el cierre `final`/`fallback`.

Uso:
    python backend/scripts/bench_e2e.py [ruta/al/archivo.xlsx] [--sin-llm]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8500/api"

# Guion de la demo: una pregunta por bloque de la app, en español y tal como
# las escribiría un analista durante la presentación.
QUESTIONS = [
    "¿Qué pasó en el host DC-01?",
    "¿Hubo intentos de fuerza bruta contra cuentas de usuario?",
    "¿Qué hosts concentran los eventos críticos?",
    "¿Qué técnicas MITRE ATT&CK se ven en estos registros?",
    "¿Cuántos eventos hay en total?",
]


def upload(path: Path) -> tuple[str, float, dict]:
    t0 = time.perf_counter()
    with httpx.Client(timeout=600) as client, path.open("rb") as fh:
        res = client.post(
            f"{BASE}/analyze",
            files={"file": (path.name, fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        res.raise_for_status()
        analysis_id = res.json()["analysis_id"]
        while True:
            state = client.get(f"{BASE}/analysis/{analysis_id}").json()
            if state.get("status") in ("done", "error"):
                break
            time.sleep(0.25)
    return analysis_id, time.perf_counter() - t0, state


def ask_stream(client: httpx.Client, analysis_id: str, question: str) -> dict:
    t0 = time.perf_counter()
    marks = {"meta": None, "first_token": None, "end": None}
    engine = model = kind = None
    tokens = 0
    warnings: list[str] = []

    with client.stream(
        "POST", f"{BASE}/analysis/{analysis_id}/ask/stream",
        json={"question": question}, timeout=600,
    ) as res:
        res.raise_for_status()
        event = None
        for line in res.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
                continue
            if not line.startswith("data:"):
                continue
            data = json.loads(line.split(":", 1)[1].strip())
            now = time.perf_counter() - t0
            if event == "meta":
                marks["meta"] = now
                engine, model = data.get("engine"), data.get("model")
            elif event == "token":
                if marks["first_token"] is None:
                    marks["first_token"] = now
                tokens += len(data.get("text") or "")
            elif event in ("final", "fallback"):
                marks["end"] = now
                kind = event
                warnings = list(data.get("warnings") or [])

    return {
        "question": question,
        "meta": marks["meta"],
        "first_token": marks["first_token"],
        "total": marks["end"],
        "engine": engine,
        "model": model,
        "kind": kind,
        "chars": tokens,
        "warnings": warnings,
    }


def show(value: float | None, default: str = "   —") -> str:
    return f"{value:5.1f}s" if value is not None else default


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    with_llm = "--sin-llm" not in sys.argv
    path = Path(args[0]) if args else Path("soc_60.xlsx")
    if not path.exists():
        print(f"[error] no existe {path}")
        sys.exit(1)

    with httpx.Client(timeout=600) as client:
        health = client.get(f"{BASE}/../openapi.json")
        if health.status_code != 200:
            print("[error] el backend no responde en :8500")
            sys.exit(1)

        print(f"[ingesta] {path.name}")
        analysis_id, ingest_s, state = upload(path)
        rows = (state.get("summary") or {}).get("rows")
        print(f"          {rows} filas · {ingest_s:.1f}s hasta «done» "
              f"(análisis {analysis_id[:8]})\n")

        print("| # | meta  | 1er token | total  | motor                 | chars | cierre    |")
        print("|---|-------|-----------|--------|-----------------------|-------|-----------|")
        for i, question in enumerate(QUESTIONS, 1):
            r = ask_stream(client, analysis_id, question)
            engine = r["engine"] or "?"
            model = r["model"] or ""
            motor = f"{engine} {model}".strip()[:21]
            print(f"| {i} | {show(r['meta'])} | {show(r['first_token'])} | "
                  f"{show(r['total'])} | {motor:<21} | {r['chars']:5d} | {r['kind']:9s} |")
            if r["warnings"]:
                print(f"|   | avisos: {', '.join(r['warnings'])}")

    if with_llm:
        print("\n(meta = evidencias + tarjetas MITRE ya en pantalla; "
              "1er token = inicio de escritura visible del LLM)")


if __name__ == "__main__":
    main()
