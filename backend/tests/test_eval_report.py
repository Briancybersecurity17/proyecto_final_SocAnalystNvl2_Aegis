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


def test_compare_no_pierde_categorias_que_desaparecieron():
    """Si una categoría existe SOLO en la línea base (todos sus casos
    revientan ahora), no puede perderse en la comparación: se muestra
    «— → 0» en vez de desaparecer en silencio y dejar un delta engañoso."""
    antes = _informe([("filter_set", True), ("refusal", True)])
    despues = _informe([("filter_set", True)])       # refusal desapareció

    cmp = compare_reports(despues, antes)

    assert "refusal" in cmp["por_categoria"]
    assert cmp["por_categoria"]["refusal"] == {"antes": 100.0, "despues": 0.0,
                                               "delta": -100.0, "presente": False}


def test_compare_con_hash_distinto_advierte():
    a, b = _informe([("filter_set", True)]), _informe([("filter_set", False)])
    b["hash_muestra"] = "otro"
    assert compare_reports(a, b)["hash_ok"] is False


def test_motor_guard_falla_si_el_llm_no_se_uso():
    # Registros como los produce _run_pasada: cada uno lleva "ok".
    resultados = [{"categoria": "filter_set", "ok": True,
                   "engine": "deterministic", "mode": "evidence"}] * 10
    resultados += [{"categoria": "aggregate", "ok": True,
                    "engine": "deterministic", "mode": "aggregate"}] * 5
    assert motor_guard(resultados, forzar=False) is False   # 100% > 30%
    assert motor_guard(resultados, forzar=True) is True     # --forzar


def test_motor_guard_ignora_los_errores():
    # Un caso con excepción no prueba nada sobre el motor: si todos los de
    # evidencia revientan, el guard no juzga (no aborta) y build_report los
    # muestra aparte como `errores`.
    rotos = [{"categoria": "filter_set", "ok": None, "engine": None,
              "mode": None, "error": "boom"}] * 12
    assert motor_guard(rotos, forzar=False) is True
    # Con un solo elegible determinista (100 %) sí debe fallar.
    assert motor_guard(rotos + [{"categoria": "filter_set", "ok": True,
                                 "engine": "deterministic",
                                 "mode": "evidence"}], forzar=False) is False


def test_motor_guard_con_llm_en_uso_pasa():
    resultados = [{"categoria": "filter_set", "engine": "llm", "mode": "evidence"}] * 7
    resultados += [{"categoria": "filter_set", "engine": "deterministic", "mode": "evidence"}] * 3
    assert motor_guard(resultados, forzar=False) is True    # 30 % no supera 30 %


def test_motor_guard_excluye_retrieval_puro_del_denominador():
    # Los 6 casos `retrieval` responden con engine="retrieval" por diseño (sin
    # LLM): contarlos en el denominador diluye el 30 % — con 6 de ellos el
    # umbral efectivo subía a 38 % sobre los 13 casos de evidencia reales.
    # Aquí 4 de 7 elegibles quedaron en el determinista (57 %) → debe fallar.
    elegibles = ([{"categoria": "filter_set", "ok": True, "engine": "llm",
                   "mode": "evidence"}] * 3
                 + [{"categoria": "filter_set", "ok": True,
                     "engine": "deterministic", "mode": "evidence"}] * 4)
    retrieval = [{"categoria": "retrieval", "ok": True, "engine": "retrieval",
                  "mode": "evidence"}] * 6
    assert motor_guard(elegibles + retrieval, forzar=False) is False


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


# --- _score_case: el corazón del scoring por categoría -----------------------
# Es una función pura (caso, esperado, body, cited) y es donde viven el umbral
# 0.5 de las preguntas anchas, la bifurcación ancho/estrecho, la refusal y el
# agregado. Un bug aquí envenenaría CADA número del informe sin que ningún otro
# test fallara, así que se cubre con una tabla explícita.

def _caso(categoria: str, unidades: str | None = None):
    class _C:
        pass

    c = _C()
    c.categoria, c.unidades = categoria, unidades
    return c


def test_build_report_calcula_latencia_y_cobertura_por_categoria():
    # `print_report` y el JSON deben llevar la media de `ms` por categoría y
    # la cobertura few-shot desglosada: la cobertura global esconde si el
    # criterio aprendido llega solo a un tipo de consulta (spec §6).
    resultados = [
        {"categoria": "filter_set", "ok": True, "ms": 100, "mode": "evidence",
         "rag_examples_used": 2},
        {"categoria": "filter_set", "ok": False, "ms": 300, "mode": "evidence",
         "rag_examples_used": 0},
        {"categoria": "aggregate", "ok": True, "ms": 50, "mode": "aggregate",
         "rag_examples_used": 0},
        {"categoria": "retrieval", "ok": True, "ms": 10, "mode": "evidence",
         "rag_examples_used": 0},
    ]
    rep = build_report(resultados=resultados,
                       grounding={"rechazo_pct": 0.0, "inventados": 0},
                       fewshot={"cobertura_pct": 33.3}, motor="llm",
                       hash_muestra="abc", modelo="qwen2.5:3b",
                       num_ctx=4096, temperatura=0.2)
    assert rep["latencia"]["por_categoria"] == {"filter_set": 200, "aggregate": 50,
                                                "retrieval": 10}
    assert rep["latencia"]["media_ms"] == 115          # (100+300+50+10)/4
    # La cobertura por categoría excluye aggregate y retrieval: solo las 2
    # de filter_set pueden llevar criterio.
    assert rep["fewshot"]["por_categoria"] == {
        "filter_set": {"casos": 2, "con_ejemplos": 1, "cobertura_pct": 50.0}}


def test_determinista_guard_detecta_rechazo_de_grounding():
    """Falso negativo del backstop: si el parche no alcanza a la instancia que
    la app usa, el LLM corre y su respuesta puede ser RECHAZADA por el guard
    de grounding → engine vuelve "deterministic" (spec §5.3) y el guard pasaba,
    publicando la «línea base» midiendo al modelo. Con qwen2.5:3b ese escenario
    es el probable, no el residual. Una pasada determinista JAMÁS puede producir
    grounding_rechazado (con el LLM apagado, _remote_answer sale antes de chat).
    """
    con_llm_encendido = [{"categoria": "filter_set", "ok": True,
                          "engine": "deterministic", "mode": "evidence",
                          "grounding_rechazado": True}]
    assert runner.determinista_guard(con_llm_encendido) is False
    # Una pasada sana (sin rechazos) sigue pasando.
    sano = [{"categoria": "filter_set", "ok": True, "engine": "deterministic",
             "mode": "evidence", "grounding_rechazado": False}]
    assert runner.determinista_guard(sano) is True


def test_fewshot_global_excluye_retrieval():
    """El denominador global few-shot debe excluir `retrieval` (nunca pasan por
    el LLM), usando el MISMO exclusor que la cobertura por categoría: si no,
    `fallos_con_0` cuenta fallos que no pudieron llevar criterio y el % global
    no reconcilia con las líneas por categoría de debajo.

    Se ejercita _elegibles_fewshot (el exclusor compartido) porque es donde
    vive el criterio; `_run_pasada` y `_cobertura_por_categoria` lo consumen.
    """
    from eval.runner import _elegibles_fewshot

    resultados = [
        {"categoria": "filter_set", "ok": True, "mode": "evidence",
         "rag_examples_used": 2},
        {"categoria": "filter_set", "ok": False, "mode": "evidence",
         "rag_examples_used": 0},
        # retrieval: jamás lleva ejemplos y falla → NO debe entrar al cubo 0.
        {"categoria": "retrieval", "ok": False, "mode": "evidence",
         "rag_examples_used": 0},
        # agregado y excepción: tampoco.
        {"categoria": "aggregate", "ok": True, "mode": "aggregate",
         "rag_examples_used": 0},
        {"categoria": "evidence_qa", "ok": None, "mode": None},
    ]
    elegibles = _elegibles_fewshot(resultados)
    assert [r["categoria"] for r in elegibles] == ["filter_set", "filter_set"]
    assert sum(1 for r in elegibles if not r["ok"]) == 1   # fallos_con_0 == 1
    assert sum(1 for r in elegibles if (r.get("rag_examples_used") or 0) > 0) == 1


def test_score_case_agregado_exige_el_numero_correcto():
    c = _caso("aggregate", unidades="eventos")
    assert runner._score_case(c, 97, {"answer": "Hay 97 eventos críticos."}, []) is True
    # Un número equivocado NO cuenta, ni aunque la prosa suene bien.
    assert runner._score_case(c, 97, {"answer": "Hay 92 eventos críticos."}, []) is False
    # Con unidad declarada, el número debe ser el más cercano a ella: la IP
    # «10.10.97.1» no debe colarse como si fuera el 97 esperado.
    assert runner._score_case(c, 68, {"answer": "La IP 10.10.97.1 originó 68 eventos."},
                              []) is True


def test_score_case_refusal_exige_negar_sin_citar_ids():
    c = _caso("refusal")
    assert runner._score_case(c, True, {"answer": "No encontre registros."}, []) is True
    # «No encontré nada; revise EVT-00007» NO es un refusal: afirma algo sobre
    # un ID concreto, así que el caso falla.
    assert runner._score_case(c, True, {"answer": "No encontre nada; revise EVT-00007."},
                              ["EVT-00007"]) is False
    # Una respuesta que afirma sin negar tampoco es un refusal.
    assert runner._score_case(c, True, {"answer": "Vi 12 registros."}, []) is False


def test_score_case_estrecho_pasa_con_un_solo_hit():
    # Pregunta estrecha (esperado <= 2*k): basta con citar UN ID relevante.
    esperado = {"EVT-00001", "EVT-00002"}
    c = _caso("filter_set")
    assert runner._score_case(c, esperado, {"answer": "x"}, ["EVT-00001"]) is True
    # Un único ID inventado no acierta.
    assert runner._score_case(c, esperado, {"answer": "x"}, ["EVT-99999"]) is False


def test_score_case_ancho_exige_precision_minima():
    # Pregunta ancha (esperado > 2*k): manda la precisión@k con umbral 0.5 —
    # el recall sería bajo por construcción (la ventana trae k).
    esperado = {f"EVT-{i:05d}" for i in range(20)}      # 20 > 2*8 → ancha
    c = _caso("filter_set")
    # 3 de 5 correctos = 0.6 ≥ 0.5 → acierto.
    assert runner._score_case(c, esperado, {"answer": "x"},
                              ["EVT-00001", "EVT-00002", "EVT-00003",
                               "EVT-99998", "EVT-99999"]) is True
    # 2 de 5 correctos = 0.4 < 0.5 → fallo (umbral, no «alguno sirve»).
    assert runner._score_case(c, esperado, {"answer": "x"},
                              ["EVT-00001", "EVT-00002",
                               "EVT-99997", "EVT-99998", "EVT-99999"]) is False


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
