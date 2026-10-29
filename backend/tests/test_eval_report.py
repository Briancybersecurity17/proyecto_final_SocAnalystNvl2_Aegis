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


def test_llm_off_restaura_la_config_al_salir():
    import ai_config

    antes = ai_config.get()
    with runner.LLM_OFF():
        assert ai_config.get()["enabled"] is False
    assert ai_config.get() == antes


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
