"""Bench del reranker ms-marco sobre candidatos realistas de recuperación."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sentence_transformers import CrossEncoder  # noqa: E402

DOC = (
    "[REGISTRO] Log_ID: EVT-0042 | Timestamp: 2026-01-10T09:30:12 | Hostname: WS-018 | "
    "Event_ID: 4625 | User: jsmith | Source_IP: 10.10.2.15 | Destination: WS-018 | "
    "Log_Source: Windows Security | Result: Failure | Severity: High\n"
    "[DESC] An account failed to log on with an invalid account name or password.\n"
    "[ANÁLISIS] Nivel: Alto — severidad «High» · resultado «Failure» · "
    "intentos de autenticación fallidos repetidos desde un mismo origen"
)

def main() -> None:
    t0 = time.perf_counter()
    model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2", max_length=256)
    print(f"carga: {time.perf_counter() - t0:.2f}s")

    question = "¿Hubo intentos de fuerza bruta contra cuentas de usuario?"
    for n in (12, 20):
        pairs = [(question, DOC + f" variante {i}") for i in range(n)]
        best = None
        for _ in range(2):  # run1 incluye cálculo de grafo
            t0 = time.perf_counter()
            scores = model.predict(pairs, show_progress_bar=False, batch_size=16)
            dt = time.perf_counter() - t0
            best = dt
            print(f"{n:2d} pares (max_len={model.max_length}): {dt*1000:6.0f}ms "
                  f"({dt / n * 1000:.1f} ms/par) scores[0]={float(scores[0]):.3f}")
        del best
    # También con documentos truncados a 400 chars (la alternativa si va lento)
    short = DOC[:400]
    pairs = [(question, short) for _ in range(20)]
    t0 = time.perf_counter()
    model.predict(pairs, show_progress_bar=False, batch_size=16)
    print(f"20 pares truncados a 400 chars: {(time.perf_counter() - t0)*1000:.0f}ms")


if __name__ == "__main__":
    main()
