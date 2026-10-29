"""Mide latencia y tokens del LLM con el payload REAL de ai_service.

Uso:  python backend/scripts/bench_llm.py ["pregunta"]
Construye el contexto igual que /ask (RAG + KB sobre el análisis activo) y mide
contra Ollama: prompt_eval (prefill) y generation. Imprime tokens y tiempos.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

import ai_config  # noqa: E402
import ai_service  # noqa: E402
import kb_store  # noqa: E402
import rag_store  # noqa: E402


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "¿Hubo intentos de fuerza bruta?"
    analysis_id = os.getenv("BENCH_ANALYSIS_ID")
    if not analysis_id:
        print("Falta BENCH_ANALYSIS_ID"); return

    hits = rag_store.query_events(analysis_id, question, n_results=8)
    reference = kb_store.build_reference(question, [
        {"event_id": h.get("event_id"), "level": h.get("level")} for h in hits
    ])
    ctx = ai_service.build_qa_context(question, hits, summary={}, reference=reference)
    messages, used = ai_service._qa_messages(ctx, history=None)

    prompt_chars = sum(len(m["content"]) for m in messages)
    print(f"pregunta: {question}")
    print(f"mensajes: {len(messages)} | chars prompt: {prompt_chars}")

    cfg = ai_config.get()
    payload = {**ai_config._payload(cfg, messages, cfg["temperature"]), "stream": False}
    payload["options"]["num_predict"] = 400

    async with httpx.AsyncClient(timeout=120) as client:
        for run in (1, 2):  # 1 = modelo en frío/cargando, 2 = caliente
            t0 = time.perf_counter()
            r = await client.post(cfg["api_url"], json=payload)
            total = time.perf_counter() - t0
            d = r.json()
            print(f"run{run}: total={total*1000:.0f}ms "
                  f"prefill={d.get('prompt_eval_duration', 0)/1e6:.0f}ms({d.get('prompt_eval_count')} tok) "
                  f"gen={d.get('eval_duration', 0)/1e6:.0f}ms({d.get('eval_count')} tok, "
                  f"{d.get('eval_count', 1) and d.get('eval_count', 0)/(max(d.get('eval_duration', 1), 1)/1e9):.1f} tok/s)")


asyncio.run(main())
