"""
test_rag.py
-----------
Prueba del índice semántico local (RAG) sobre la muestra versionada en el repo.

Uso:
    python test_rag.py                     # archivo por defecto
    python test_rag.py ruta/al/archivo.xlsx

Comprueba: disponibilidad del motor, indexación, recuperación semántica con
preguntas en español sobre logs en inglés, filtros por criticidad y degradación.
"""
from __future__ import annotations

import sys
from pathlib import Path

# La consola de Windows (cp1252) no imprime emojis/símbolos: forzamos UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "backend"))

import rag_store  # noqa: E402
from main import _enrich_records  # noqa: E402  (motor determinista compartido)

# Muestra versionada en el repo: funciona en cualquier máquina sin rutas personales.
DEFAULT_FILE = str(Path(__file__).parent / "backend" / "eval" / "fixtures" / "sample.xlsx")

PREGUNTAS = [
    "hubo intentos de fuerza bruta?",
    "¿qué pasó en el servidor DC-01?",
    "eventos sospechosos desde la IP 10.10.2.15",
    "instalación de software o malware",
    "cuentas bloqueadas o lockout",
    "hubo actividad relacionada con ransomware?",
]


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FILE)
    if not path.exists():
        print(f"[FAIL] No existe el archivo: {path}")
        return 1

    import pandas as pd

    print("=" * 78)
    print("RAG — índice semántico local")
    print("=" * 78)

    info = rag_store.status()
    print(f"Motor:      {'✅ ' + str(info['model']) if info['available'] else '❌ ' + str(info.get('reason'))}")
    if not info["available"]:
        print("\n[FAIL] Motor RAG no disponible")
        return 1

    sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    df = pd.concat(sheets.values(), ignore_index=True)
    records = _enrich_records(df)
    print(f"Registros:  {len(records)} filas, {len(df.columns)} columnas")

    analysis_id = "test-rag-001"
    result = rag_store.index_events(analysis_id, records)
    print(f"Indexado:   {result}")
    if not result.get("available"):
        print("\n[FAIL] Indexación")
        return 1

    assert rag_store.has_index(analysis_id), "has_index debería ser True"
    print(f"Stats:      {rag_store.get_stats(analysis_id)}")

    # Expansión bilingüe ES→EN (dato independiente del archivo)
    expanded = rag_store.expand_query("¿hubo intentos de fuerza bruta?")
    assert "brute force" in expanded, f"expand_query no amplió la consulta: {expanded}"
    assert rag_store.expand_query("random question") == "random question"
    print(f"Expansión:  {expanded}")

    # ------------------------------------------------------------------
    # Recuperación semántica — preguntas en español sobre logs en inglés
    # ------------------------------------------------------------------
    failures = 0
    for question in PREGUNTAS:
        # igual que hace /api/analysis/{id}/ask: consulta ampliada
        hits = rag_store.query_events(
            analysis_id, rag_store.expand_query(question), n_results=3
        )
        print(f"\n❓ {question}")
        if not hits:
            print("   [FAIL] sin resultados")
            failures += 1
            continue
        for hit in hits:
            meta = hit["metadata"]
            print(
                f"   {hit['score']:.3f} | {meta.get('log_id', '?'):<12} "
                f"| nivel={meta.get('level', '?'):<8} | host={meta.get('hostname', '?'):<12} "
                f"| ev={meta.get('event_id', '?')}"
            )
        print(f"   doc: {hits[0]['document'].replace(chr(10), ' ⏎ ')[:190]}")

    # ------------------------------------------------------------------
    # Filtros por criticidad
    # ------------------------------------------------------------------
    print("\n" + "-" * 78)
    for level in ("critico", "alto", "medio", "bajo"):
        hits = rag_store.query_events(analysis_id, "autenticación fallida", n_results=2, level=level)
        levels = {h["level"] for h in hits} if hits else set()
        ok = (not hits) or levels == {level}
        print(f"filtro level={level:<8} → {len(hits)} hits, niveles={levels or '{}'} {'✅' if ok else '❌'}")
        if not ok:
            failures += 1

    # Filtro de sospechosos
    hits = rag_store.query_events(analysis_id, "ataque", n_results=5, suspicious=True)
    ok = all(h["suspicious"] for h in hits)
    print(f"filtro suspicious=True → {len(hits)} hits {'✅' if ok else '❌'}")
    if not ok:
        failures += 1

    # ------------------------------------------------------------------
    # Consulta sin índice (otro análisis) → debe devolver [] sin lanzar
    # ------------------------------------------------------------------
    empty = rag_store.query_events("no-existe", "cualquier cosa")
    print(f"\nanálisis inexistente → {empty} {'✅' if empty == [] else '❌'}")
    if empty != []:
        failures += 1

    # ------------------------------------------------------------------
    # Degradación: simular motor ausente no debe romper nada
    # ------------------------------------------------------------------
    old_col, old_init = rag_store._collection, rag_store._initialized
    rag_store._collection, rag_store._initialized = None, True
    try:
        d = rag_store.index_events("x", records[:3])
        q = rag_store.query_events("x", "test")
        s = rag_store.get_stats()
        degraded = d["available"] is False and q == [] and s["available"] is False
    finally:
        rag_store._collection, rag_store._initialized = old_col, old_init
    print(f"degradación elegante → {'✅' if degraded else '❌'} ({d.get('reason')})")
    if not degraded:
        failures += 1

    # ------------------------------------------------------------------
    # Respuesta fundamentada (ai_service.answer_question)
    # ------------------------------------------------------------------
    print("\n" + "-" * 78)
    import asyncio

    from ai_service import answer_question  # noqa: E402

    summary = {
        "rows": len(records),
        "level_counts": rag_store.get_stats(analysis_id).get("levels", {}),
    }

    async def _ask(question: str) -> dict:
        hits = rag_store.query_events(
            analysis_id, rag_store.expand_query(question), n_results=8
        )
        return await answer_question(question, hits, summary)

    # Caso con evidencia real en el archivo (Event ID 4625 = logon fallido)
    good = asyncio.run(_ask("¿hubo intentos de autenticación fallida?"))
    print(f"con evidencia  → confianza={good['confidence']} | {good['summary_line']}")
    if good["confidence"] == "baja" or not good["evidence"]:
        print("   [FAIL] debería recuperar los eventos de autenticación fallida")
        failures += 1

    # Caso sin evidencia: la respuesta debe NEGAR la conclusión, no inventarla.
    # Acepta cualquier señal honesta: warning de confianza baja, aviso de
    # fuera de tema o la negación explícita de evidencia directa.
    weak = asyncio.run(_ask("¿hubo un terremoto en la oficina?"))
    honest = (
        "baja_confianza" in weak["warnings"]
        or "fuera_de_tema" in weak["warnings"]
        or "sin_resultados" in weak["warnings"]
        or "No encontré evidencia directa" in weak["answer"]
    )
    print(f"sin evidencia  → confianza={weak['confidence']} | avisos={weak['warnings']}")
    if not honest:
        print("   [FAIL] la respuesta afirma cosas sin evidencia:")
        print("   ", weak["answer"][:300])
        failures += 1

    # Limpieza
    deleted = rag_store.delete_analysis(analysis_id)
    print(f"\nlimpieza → {deleted} eventos borrados ✅")

    print("\n" + "=" * 78)
    if failures:
        print(f"[FAIL] {failures} comprobaciones fallidas")
        return 1
    print("[OK] RAG funcionando: indexación + recuperación semántica + filtros")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
