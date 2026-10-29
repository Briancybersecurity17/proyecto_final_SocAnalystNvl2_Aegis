"""Tests puros del runner del harness: informe, comparación y guard.

Sin app, sin red, sin LLM: solo las funciones de orquestación que se pueden
verificar sin subir el Excel (la corrida real es la validación de Task 7).
"""
import pytest

from eval import runner
from eval.runner import build_report, compare_reports, motor_guard


def _informe(scores):
    return build_report(
        resultados=[{"categoria": c, "ok": ok} for c, ok in scores],
        grounding={"rechazo_pct": 0.0, "inventados": 0},
        fewshot={"cobertura_pct": 50.0}, motor="llm",
        hash_muestra="abc", modelo="qwen2.5:3b", num_ctx=4096, temperatura=0.2,
    )


def test_build_report_media_macro_igual_peso():
    rep = _informe([("aggregate", True), ("filter_set", False), ("refusal", True)])
    assert rep["global"] == 66.67      # (100 + 0 + 100) / 3
    assert rep["por_categoria"]["aggregate"] == {"casos": 1, "aciertos": 1, "pct": 100.0}


def test_compare_detecta_delta_y_hash():
    antes, despues = _informe([("filter_set", False)]), _informe([("filter_set", True)])
    cmp = compare_reports(despues, antes)
    assert cmp["hash_ok"] and cmp["delta_global"] > 0


def test_compare_con_hash_distinto_advierte():
    a, b = _informe([("filter_set", True)]), _informe([("filter_set", False)])
    b["hash_muestra"] = "otro"
    assert compare_reports(a, b)["hash_ok"] is False


def test_motor_guard_falla_si_el_llm_no_se_uso():
    resultados = [{"categoria": "filter_set", "engine": "deterministic", "mode": "evidence"}] * 10
    resultados += [{"categoria": "aggregate", "engine": "deterministic", "mode": "aggregate"}] * 5
    assert motor_guard(resultados, forzar=False) is False   # 100% > 30%
    assert motor_guard(resultados, forzar=True) is True     # --forzar


def test_motor_guard_con_llm_en_uso_pasa():
    resultados = [{"categoria": "filter_set", "engine": "llm", "mode": "evidence"}] * 7
    resultados += [{"categoria": "filter_set", "engine": "deterministic", "mode": "evidence"}] * 3
    assert motor_guard(resultados, forzar=False) is True    # 30 % no supera 30 %


def test_motor_guard_excluye_retrieval_puro_del_denominador():
    # Los 6 casos `retrieval` responden con engine="retrieval" por diseño (sin
    # LLM): contarlos en el denominador diluye el 30 % — con 6 de ellos el
    # umbral efectivo subía a 38 % sobre los 13 casos de evidencia reales.
    # Aquí 4 de 7 elegibles quedaron en el determinista (57 %) → debe fallar.
    evidencia_llm = [{"categoria": "filter_set", "engine": "llm", "mode": "evidence"}] * 3
    fallback = [{"categoria": "filter_set", "engine": "deterministic", "mode": "evidence"}] * 4
    retrieval = [{"categoria": "retrieval", "engine": "retrieval", "mode": "evidence"}] * 6
    assert motor_guard(evidencia_llm + fallback + retrieval, forzar=False) is False


def test_backstop_determinista_detecta_llm_encendido():
    # Si el parche de LLM_OFF no alcanza a la instancia que la app realmente
    # usa (C1), la pasada determinista responde con engine="llm": sin este
    # backstop esa corrida se publicaría como «línea base» sin que nada
    # fallara. Aquí 2 de 3 no-agregados quedaron en el LLM → debe abortar.
    resultados = [{"categoria": "filter_set", "ok": True,
                   "engine": "llm", "mode": "evidence"}] * 2
    resultados += [{"categoria": "filter_set", "ok": False,
                    "engine": "deterministic", "mode": "evidence"}]
    assert runner.determinista_guard(resultados) is False
    assert runner.determinista_guard([{"categoria": "filter_set", "ok": True,
                                       "engine": "deterministic",
                                       "mode": "evidence"}]) is True
    # Los casos con excepción no prueban nada sobre el motor: se ignoran.
    assert runner.determinista_guard([{"categoria": "filter_set", "ok": None,
                                       "engine": None, "mode": None}]) is True


def test_llm_off_restaura_la_config_al_salir():
    import ai_config

    antes = ai_config.get()
    with runner.LLM_OFF():
        assert ai_config.get()["enabled"] is False
    assert ai_config.get() == antes


def _instancias(nombre: str):
    """Todas las instancias del módulo cargadas en el proceso (sueltas y
    bajo el paquete `backend`): el backend importa con try/except dual
    (ai_service.py:42-47, main.py:61-71), así que pueden convivir dos."""
    import sys

    return [m for name, m in sys.modules.items()
            if name == nombre or name == f"backend.{nombre}"]


def _forzar_doble_carga(nombre: str):
    """Carga también la forma `backend.<nombre>` para reproducir el entorno de
    producción (`uvicorn backend.main:app`, raíz del proyecto en sys.path).

    La suite corre desde `backend/` con solo esa raíz en sys.path, así que sin
    este empujón solo existiría la instancia top-level y el test no probaría
    nada. Si el paquete no es importable se omite (el runner igual debe
    parchear lo que haya).
    """
    import importlib
    import sys
    from pathlib import Path

    raiz = str(Path(__file__).resolve().parents[2])
    if raiz not in sys.path:
        sys.path.insert(0, raiz)
    try:
        importlib.import_module(f"backend.{nombre}")
    except ImportError:
        pass


def test_llm_off_alcanza_las_dos_instancias_de_ai_config():
    # La app en producción corre como `uvicorn backend.main:app` (raíz del
    # proyecto en sys.path): allí `main.ai_config` es la instancia
    # `backend.ai_config`, NO la top-level. Parchear solo la top-level deja
    # el LLM encendido y la «línea base» publicaría puntuación del modelo.
    import ai_config

    _forzar_doble_carga("ai_config")
    mods = _instancias("ai_config")
    if len(mods) < 2:
        pytest.skip("paquete backend no importable en este entorno")
    with runner.LLM_OFF():
        for mod in mods:
            assert mod.get()["enabled"] is False, f"{mod.__name__} quedó encendido"
    for mod in mods:
        assert mod.get()["enabled"] is not False


def test_aislamiento_redirige_las_dos_instancias_de_feedback(tmp_path):
    # Mismo motivo que el test anterior: si solo se redirige la instancia
    # top-level, la corrida ESCRIBE en el feedback.json real del analista y
    # contamina su memoria few-shot (spec §4.1, la garantía nº 1).
    import feedback_store

    _forzar_doble_carga("feedback_store")
    mods = _instancias("feedback_store")
    if len(mods) < 2:
        pytest.skip("paquete backend no importable en este entorno")
    reales = {mod: mod.FEEDBACK_PATH for mod in mods}
    with runner._isolated_store(tmp_path):
        for mod in mods:
            assert mod.FEEDBACK_PATH == tmp_path / "feedback.json", \
                f"{mod.__name__} sigue apuntando al JSON real"
            assert mod._load() == []
    for mod, real in reales.items():
        assert mod.FEEDBACK_PATH == real


def test_aislamiento_no_lee_ni_escribe_la_memoria_real(tmp_path):
    import feedback_store

    real = feedback_store.FEEDBACK_PATH
    # La memoria real tiene ejemplos (o no), pero el aislamiento debe ignorarla.
    with runner._isolated_store(tmp_path):
        assert feedback_store.FEEDBACK_PATH != real
        assert feedback_store._load() == []
        feedback_store.add_correction("¿Qué pasó en DC-01?", "Criterio de prueba.")
        assert feedback_store.status()["total"] == 1
    assert feedback_store.FEEDBACK_PATH == real
    assert feedback_store._cache is None        # caché invalidada al salir


def test_espera_de_indice_con_timeout_inyectable():
    # has_index False→False→True: espera; si nunca indexa, RuntimeError claro.
    estados = iter([False, False, True])
    assert runner._wait_for_index(lambda: next(estados), timeout=5, poll=0.01) is True
    with pytest.raises(RuntimeError, match="no index"):
        runner._wait_for_index(lambda: False, timeout=0.05, poll=0.01)


def test_un_caso_que_revienta_se_cuenta_y_no_contamina_el_score():
    rep = build_report(
        resultados=[{"categoria": "refusal", "ok": True},
                    {"categoria": "refusal", "ok": None, "error": " KeyError: 'x'"}],
        grounding={"rechazo_pct": 0.0, "inventados": 0},
        fewshot={"cobertura_pct": 100.0}, motor="llm",
        hash_muestra="abc", modelo="qwen2.5:3b", num_ctx=4096, temperatura=0.2,
    )
    assert rep["errores"] == 1                   # se muestra, no se esconde
    assert rep["por_categoria"]["refusal"] == {"casos": 1, "aciertos": 1, "pct": 100.0}
