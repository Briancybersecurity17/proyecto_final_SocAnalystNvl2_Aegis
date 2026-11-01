"""CLI del harness de evaluación RAG+LLM.

Puntúa el asistente contra la muestra fija versionada (eval/fixtures/sample.xlsx)
con verdad calculable por pandas — sin juez LLM, sin respuestas doradas.

Uso (desde la raíz del proyecto):
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\eval_rag.py --motor determinista
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\eval_rag.py --motor ambos --guardar
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\eval_rag.py --motor llm --contra backend\\eval\\runs\\baseline.json

--motor determinista es la línea base barata (no toca el proveedor); «ambos» hace
dos pasadas y muestra el delta entre motores; --contra compara contra una
corrida guardada (advertencia si la muestra difiere).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from eval import runner  # noqa: E402

RUNS_DIR = Path(__file__).resolve().parents[1] / "eval" / "runs"


def _default_run_path() -> Path:
    return RUNS_DIR / f"run-{time.strftime('%Y%m%d-%H%M%S')}.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--motor", choices=("llm", "determinista", "ambos"),
                        default="llm", help="motor a evaluar (default: llm)")
    parser.add_argument("--guardar", nargs="?", const=str(_default_run_path()),
                        default=None, metavar="RUTA",
                        help="guarda el informe (JSON) en RUTA o en runs/ por defecto")
    parser.add_argument("--contra", metavar="RUTA",
                        help="compara contra una corrida guardada previa")
    parser.add_argument("--forzar", action="store_true",
                        help="ignora el guard del motor (si el LLM no se usó)")
    args = parser.parse_args()

    informes = runner.run(motor=args.motor, forzar=args.forzar)
    if not isinstance(informes, list):
        informes = [informes]

    for informe in informes:
        runner.print_report(informe)

    # --motor ambos: el delta entre las dos pasadas del MISMO harness.
    if len(informes) == 2:
        runner.print_comparison(runner.compare_reports(informes[1], informes[0]))

    if args.guardar:
        destino = Path(args.guardar)
        runner.save_report(informes[-1], destino)
        print(f"\nInforme guardado en {destino}")

    if args.contra:
        runner.print_comparison(
            runner.compare_reports(informes[-1], runner.load_report(Path(args.contra)))
        )


if __name__ == "__main__":
    main()
