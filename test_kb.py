"""
test_kb.py
----------
Prueba de la base de conocimiento (MITRE ATT&CK + glosario de Event IDs).

Uso:
    python test_kb.py

Comprueba: carga de los JSON, coherencia cruzada (cada referencia existe),
indexación semántica con búsqueda léxica de respaldo, alias de técnicas
renumeradas por MITRE, construcción de la referencia anclada a la evidencia,
integración con answer_question y degradación elegante.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# La consola de Windows (cp1252) no imprime emojis: forzamos UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "backend"))

import kb_store  # noqa: E402
import rag_store  # noqa: E402


def main() -> int:
    failures = 0

    print("=" * 78)
    print("KB — base de conocimiento (MITRE ATT&CK + Event IDs)")
    print("=" * 78)

    techs = kb_store.techniques()
    events = kb_store.event_ids()
    print(f"Técnicas:   {len(techs)}")
    print(f"Event IDs:  {len(events)}")
    if len(techs) < 50 or len(events) < 30:
        print("[FAIL] la base de conocimiento está incompleta")
        return 1

    # ------------------------------------------------------------------
    # 1. Coherencia cruzada: toda referencia existe en la propia KB
    # ------------------------------------------------------------------
    print("\n" + "-" * 78)
    known = {t["id"] for t in techs}
    for t in techs:
        known.update(t.get("aliases") or [])
    bad_refs = [
        (eid, ref) for eid, e in events.items()
        for ref in (e.get("relacionados") or []) if ref not in known
    ]
    print(f"referencias evento→técnica rotas: {len(bad_refs)} {'OK' if not bad_refs else 'FAIL ' + str(bad_refs[:5])}")
    if bad_refs:
        failures += 1

    bad_events = [
        (t["id"], ev) for t in techs for ev in (t.get("eventos") or [])
        if ev not in events
    ]
    print(f"referencias técnica→evento rotas: {len(bad_events)} {'OK' if not bad_events else 'FAIL ' + str(bad_events[:5])}")
    if bad_events:
        failures += 1

    # Descripciones oficiales presentes (fuente verificable en attack.mitre.org)
    no_desc = [t["id"] for t in techs if not t.get("description") or not t.get("url")]
    print(f"técnicas sin descripción oficial/URL: {len(no_desc)} {'OK' if not no_desc else 'FAIL'}")
    if no_desc:
        failures += 1

    # ------------------------------------------------------------------
    # 2. Estado e indexación
    # ------------------------------------------------------------------
    info = kb_store.status()
    print(f"\nstatus:     available={info['available']} | indexada={info.get('documents', 0)} docs | {info['source']}")
    if info["techniques"] != len(techs) or info["event_ids"] != len(events):
        print("[FAIL] el estado no refleja los JSON")
        failures += 1

    # ------------------------------------------------------------------
    # 3. Búsqueda semántica (con léxica de respaldo)
    # ------------------------------------------------------------------
    print("\n" + "-" * 78)
    checks = [
        ("fuerza bruta contra un usuario", "T1110"),
        ("ransomware cifra archivos", "T1486"),
        ("movimiento lateral por escritorio remoto", "T1021.001"),
        ("qué significa el Event ID 4625", None),
    ]
    for query, expected in checks:
        hits = kb_store.search(query, kind="technique", limit=4)
        ids = [h["id"] for h in hits]
        top = hits[0] if hits else None
        line = f"{query!r} → {ids} (mejor {top['score'] if top else '—'})"
        if expected and expected not in ids:
            print(f"   FAIL {line}")
            failures += 1
        else:
            print(f"   OK   {line}")

    # Búsqueda en el glosario de eventos
    ev_hits = kb_store.search("inicio de sesión fallido", kind="event_id", limit=3)
    ev_ids = [h["id"] for h in ev_hits]
    ev_ok = "4625" in ev_ids
    print(f"   {'OK  ' if ev_ok else 'FAIL'} glosario: 'inicio de sesión fallido' → {ev_ids}")
    if not ev_ok:
        failures += 1

    # ------------------------------------------------------------------
    # 4. Alias de técnicas renumeradas (T1562.004 → T1686)
    # ------------------------------------------------------------------
    aliased = kb_store._techniques_by_ids(["T1562.004", "T1070.001"])
    ids = [t["id"] for t in aliased]
    print(f"\nalias:      T1562.004→{ids[0] if ids else '∅'} · T1070.001→{ids[1] if len(ids) > 1 else '∅'}")
    if "T1686" not in ids or "T1685.005" not in ids:
        print("   [FAIL] no se resolvieron los IDs revocados por MITRE")
        failures += 1

    # ------------------------------------------------------------------
    # 5. Referencia anclada a la evidencia (los eventos observados mandan)
    # ------------------------------------------------------------------
    print("\n" + "-" * 78)
    evidence = [
        {"log_id": "EVT-00174", "event_id": "4625", "level": "alto", "suspicious": True},
        {"log_id": "EVT-00175", "event_id": "4625", "level": "alto", "suspicious": True},
        {"log_id": "EVT-00999", "event_id": "NO_EXISTE", "level": "bajo", "suspicious": False},
    ]
    ref = kb_store.build_reference("¿hubo intentos de fuerza bruta?", evidence)
    ev_list = [e["id"] for e in ref["event_ids"]]
    tech_list = [t["id"] for t in ref["techniques"]]
    print(f"event_ids:  {ev_list}")
    print(f"técnicas:   {tech_list}")
    print(f"nota:       {ref['note'][:70]}…")
    if "4625" not in ev_list or "NO_EXISTE" in ev_list:
        print("   [FAIL] lookup de Event IDs (debe incluir los observados y omitir desconocidos)")
        failures += 1
    if "T1110" not in tech_list:
        print("   [FAIL] la evidencia 4625 debería enlazar con T1110 (fuerza bruta)")
        failures += 1
    if not ref.get("note"):
        print("   [FAIL] falta la etiqueta de referencia del marco")
        failures += 1

    # Evidencia vacía → la referencia puede seguir aportando contexto de consulta
    empty_ref = kb_store.build_reference("qué es el Event ID 1102", [])
    print(f"sin evidencia → eventos={[e['id'] for e in empty_ref['event_ids']]} técnicas={[t['id'] for t in empty_ref['techniques']]}")

    # ------------------------------------------------------------------
    # 6. Integración con la respuesta del asistente
    # ------------------------------------------------------------------
    from ai_service import answer_question  # noqa: E402

    async def _ask() -> dict:
        return await answer_question(
            "¿hubo intentos de fuerza bruta?", [], {"rows": 715}, reference=ref,
        )

    answer = asyncio.run(_ask())
    has_ref_block = "Referencia técnica" in answer["answer"] and "T1110" in answer["answer"]
    print(f"\nrespuesta incluye bloque de referencia: {'OK' if has_ref_block else 'FAIL'}")
    if not has_ref_block:
        print("   ", answer["answer"][:400])
        failures += 1
    if answer.get("reference") is None:
        print("   [FAIL] la respuesta no devuelve la referencia estructurada")
        failures += 1

    # ------------------------------------------------------------------
    # 7. Degradación: sin Chroma la KB sigue funcionando (búsqueda léxica)
    # ------------------------------------------------------------------
    print("\n" + "-" * 78)
    # Se parchea el módulo EXACTO que usa kb_store (puede ser `rag_store` o
    # `backend.rag_store` según cómo se haya importado).
    rag_module = kb_store.rag_store
    old_client = rag_module.get_client
    rag_module.get_client = lambda: None  # type: ignore[assignment]
    try:
        status = kb_store.status()
        degraded_hits = kb_store.search("fuerza bruta", kind="technique", limit=4)
        degraded_ref = kb_store.build_reference("fuerza bruta", evidence)
        degraded = (
            status["available"] is False
            and len(degraded_hits) > 0
            and degraded_ref["available"] is True
        )
    finally:
        rag_module.get_client = old_client  # type: ignore[assignment]
    print(f"degradación elegante → {'OK' if degraded else 'FAIL'} "
          f"({len(degraded_hits)} técnicas vía léxica, motivo: {status.get('reason')})")
    if not degraded:
        failures += 1

    print("\n" + "=" * 78)
    if failures:
        print(f"[FAIL] {failures} comprobaciones fallidas")
        return 1
    print("[OK] Base de conocimiento: ATT&CK + Event IDs, coherente y consultable")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
