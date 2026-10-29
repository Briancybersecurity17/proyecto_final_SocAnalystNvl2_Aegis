"""Test de proceso FRESCO: el aislamiento del runner con el orden real del CLI.

Los tests compartidos no pueden cazar esta clase de bug: `conftest.py` importa
`main` al arrancar la sesión, así que `feedback_store` SIEMPRE está en
`sys.modules` cuando `_isolated_store` escanea. En el proceso canónico del CLI
(`python backend\\scripts\\eval_rag.py`) la entrada a `_isolated_store` ocurre
antes de que `main` se importe (eso pasa dentro de `_run_pasada`), y un escaneo
pasivo no encuentra nada que parchear: el aislamiento queda en no-op silencioso
y la corrida lee el feedback.json REAL del analista (spec §4.1, garantía nº 1).

Este script corre en un intérprete limpio y reproduce ese orden exacto.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SEED = BACKEND / "eval" / "fixtures" / "fewshot_seed.json"

# Orden idéntico al de `_una` en runner.py: eval.runner + ai_config cargados,
# `main` NO. Luego se entra en _isolated_store y recién ahí se importa main
# (como hace _run_pasada). Al salir se comprueba que feedback_store quedó
# redirigido DURANTE todo el bloque, incluida la carga tardía de main.
_SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, {backend!r})
    from eval import runner

    # Estado canónico del CLI ANTES de entrar: sin main, sin feedback_store.
    assert "main" not in sys.modules, "este test exige proceso fresco"
    assert "feedback_store" not in sys.modules, "feedback_store ya cargado"
    assert "backend.feedback_store" not in sys.modules, "backend.feedback_store ya cargado"

    import ai_config  # el CLI lo importa al arrancar

    from eval.cases import FIXTURE
    tmp = Path({tmp!r})
    real_antes = None

    with runner._isolated_store(tmp):
        # Dentro del bloque, main se carga TARDÍAMENTE (como _run_pasada):
        # su feedback_store debe HEREDAR la redirección, no ignorarla.
        import main
        import feedback_store

        instancias = [m for name, m in sys.modules.items()
                      if name in ("feedback_store", "backend.feedback_store")]
        assert instancias, "no hay ninguna instancia de feedback_store"
        for mod in instancias:
            assert mod.FEEDBACK_PATH == tmp / "feedback.json", (
                f"{{mod.__name__}} apunta a {{mod.FEEDBACK_PATH}}: el aislamiento "
                "NO alcanzó a esta instancia"
            )
        # La memoria real NO se lee dentro del bloque: el store arranca con
        # el SNAPSHOT VERSIONADO (ruling issue 5), nunca vacío ni real.
        import json
        seed_ids = {{e["id"] for e in json.loads(Path({seed!r}).read_text(encoding="utf-8"))}}
        cargados = {{e["id"] for e in feedback_store._load()}}
        assert cargados == seed_ids, "el store aislado no cargó SOLO el seed"
        # Y main, que es quien la usaría, ve la ruta aislada.
        assert main.feedback_store.FEEDBACK_PATH == tmp / "feedback.json"
        real_antes = feedback_store.FEEDBACK_PATH

    # Al salir, restaurado al original.
    for name in ("feedback_store", "backend.feedback_store"):
        mod = sys.modules.get(name)
        if mod is not None:
            assert mod.FEEDBACK_PATH != tmp / "feedback.json", "no restauró"
    print("AISLAMIENTO_OK")
    """
)


def test_aislamiento_en_proceso_fresco_con_orden_del_cli(tmp_path):
    """El escaneo de _patched_modules debe GARANTIZAR la carga antes de parchear:
    si el módulo aún no existe en sys.modules, un import tardío (main dentro del
    bloque) crearia una instancia que ignora la redirección."""
    code = _SCRIPT.format(backend=str(BACKEND), tmp=str(tmp_path), seed=str(SEED))
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(BACKEND), timeout=300,
    )
    assert proc.returncode == 0, (
        f"el proceso fresco falló (regresión de aislamiento)\n"
        f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    )
    assert "AISLAMIENTO_OK" in proc.stdout
