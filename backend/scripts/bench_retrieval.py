"""Perfil de la fase previa a la primera letra (recuperación + rerank + KB).

Descompone el tiempo que la app gasta ANTES de mandar el prompt al LLM, que
es la parte que no depende del modelo:

    recuperación híbrida (RAG + léxica)  →  reordenación cross-encoder
                                          →  referencia KB  →  prompt

Uso:
    python backend/scripts/bench_retrieval.py <analysis_id> <excel> "pregunta"
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # raíz del repo
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd  # noqa: E402

import kb_store  # noqa: E402
import rag_store  # noqa: E402,F401
import rerank  # noqa: E402
from main import _hybrid_retrieve  # noqa: E402

QUESTIONS = [
    "¿Qué pasó en el host DC-01?",
    "¿Hubo intentos de fuerza bruta contra cuentas de usuario?",
    "¿Qué hosts concentran los eventos críticos?",
    "¿Qué técnicas MITRE ATT&CK se ven en estos registros?",
]


def main() -> None:
    analysis_id = sys.argv[1]
    df = pd.read_excel(sys.argv[2])
    sheet = sys.argv[3] if len(sys.argv) > 3 else None

    print(f"{len(df)} filas · rerank disponible: {rerank.available()}")

    # Calentar (primera llamada paga la carga del cross-encoder y del índice)
    _hybrid_retrieve(analysis_id, "calentamiento", df)

    questions = QUESTIONS if sheet is None else [sheet]
    for question in questions:
        t0 = time.perf_counter()
        hits = _hybrid_retrieve(analysis_id, question, df)
        t1 = time.perf_counter()
        reference = kb_store.build_reference(question, hits)
        t2 = time.perf_counter()
        with_rr = sum(1 for h in hits if "rerank_score" in h)

        # Repetir sin rerank para aislar su coste real
        rerank._available = False
        _hybrid_retrieve(analysis_id, "calentamiento", df)
        t3 = time.perf_counter()
        _hybrid_retrieve(analysis_id, question, df)
        t4 = time.perf_counter()
        rerank._available = True

        print(
            f"\n«{question}»\n"
            f"  recuperación + rerank : {t1 - t0:5.2f}s  ({len(hits)} evidencias, "
            f"{with_rr} con rerank_score)\n"
            f"  referencia KB         : {t2 - t1:5.2f}s  "
            f"({len(reference.get('event_ids') or [])} eventos, "
            f"{len(reference.get('techniques') or [])} técnicas)\n"
            f"  TOTAL                 : {t2 - t0:5.2f}s\n"
            f"  … sin rerank          : {t4 - t3:5.2f}s   → coste del cross-encoder: "
            f"{(t1 - t0) - (t4 - t3):+.2f}s"
        )
        _ = t2


if __name__ == "__main__":
    main()
