"""Mide num_ctx 1024/2048/4096 con prompt ~3000 chars (dos corridas)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

URL = "http://localhost:11434/api/chat"


def bench(ctx: int, run: int) -> None:
    body = {
        "model": "qwen2.5:3b",
        "messages": [{"role": "user", "content": "x" * 3000}],
        "stream": False,
        "keep_alive": "30m",
        "options": {"num_ctx": ctx, "num_predict": 48},
    }
    t0 = time.perf_counter()
    d = httpx.post(URL, json=body, timeout=180).json()
    total = (time.perf_counter() - t0) * 1000
    evc = d.get("eval_count", 0)
    evd = d.get("eval_duration", 0) / 1e6
    pre = d.get("prompt_eval_duration", 0) / 1e6
    pretok = d.get("prompt_eval_count", 0)
    rate = evc / max(evd / 1000, 0.001)
    print(f"ctx={ctx} run{run} total={total:6.0f}ms prefill={pre:6.0f}ms({pretok:4d} tok) "
          f"gen={evd:6.0f}ms({evc:3d} tok) {rate:5.1f} tok/s")


if __name__ == "__main__":
    # Warm-up: que el modelo esté cargado antes de medir.
    warm = {"model": "qwen2.5:3b", "messages": [{"role": "user", "content": "Di: OK"}],
            "stream": False, "keep_alive": "30m", "options": {"num_ctx": 1024, "num_predict": 8}}
    httpx.post(URL, json=warm, timeout=120)
    for ctx in (1024, 2048, 4096):
        for run in (1, 2):
            bench(ctx, run)
