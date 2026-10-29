"""Tests puros del runner del harness: informe, comparación y guard.

Sin app, sin red, sin LLM: solo las funciones de orquestación que se pueden
verificar sin subir el Excel (la corrida real es la validación de Task 7).
"""
import json

import pytest

from eval import runner
from eval.runner import build_report, compare_reports, motor_guard

# Ids del snapshot few-shot versionado: el store aislado arranca con él
# (ruling del issue 5), nunca vacío ni con la memoria real.
_SEED_IDS = {e["id"] for e in
             json.loads(runner.SEED_PATH.read_text(encoding="utf-8"))}


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


def test_motor_guard_sin_casos_elegibles_no_aprueba():
    # Guard fail-closed: si NINGÚN caso de evidencia puntuó (todos con
    # excepción), el guard no puede verificar que el modelo se usó — con
    # True vacío, una corrida «llm» con 0 uso real del LLM se publicaría
    # como del modelo (spec §7: mejor fallar ruidosamente que publicar
    # sin verificar). --forzar sigue siendo la salida explícita.
    rotos = [{"categoria": "filter_set", "ok": None, "engine": None,
              "mode": None, "error": "boom"}] * 12
    assert motor_guard(rotos, forzar=False) is False
    assert motor_guard(rotos, forzar=True) is True
    # Con un solo elegible determinista (100 %) también falla.
    assert motor_guard(rotos + [{"categoria": "filter_set", "ok": True,
                                 "engine": "deterministic",
                                 "mode": "evidence"}], forzar=False) is False


def test_motor_guard_con_llm_en_uso_pasa():
    # Con "ok" presente los 10 son elegibles: 3/10 en determinista (30 %)
    # no supera el umbral → pasa. (Sin "ok" ninguno era elegible y, con el
    # antiguo True vacío, este test pasaba sin probar nada.)
    resultados = [{"categoria": "filter_set", "ok": True, "engine": "llm",
                   "mode": "evidence"}] * 7
    resultados += [{"categoria": "filter_set", "ok": True,
                    "engine": "deterministic", "mode": "evidence"}] * 3
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


# --- Impresión: lo que el docstring promete debe verse en la salida -----------
def _comparacion(presente_en_actual: bool):
    actual = _informe([("filter_set", False)])
    baseline = _informe([("filter_set", True)])
    if not presente_en_actual:
        actual["por_categoria"] = {}
        actual["global"] = 0.0
    if presente_en_actual:
        baseline["por_categoria"] = {}
        baseline["global"] = 0.0
    return compare_reports(actual, baseline)


def test_print_comparison_marca_la_categoria_solo_en_baseline(capsys):
    # compare_reports documenta (en presente) «se muestra «— → 0»» y calcula
    # el flag `presente` para eso, pero print_comparison lo ignoraba: la
    # categoría ausente en la corrida actual se imprimía como un 0 medido
    # cualquiera. La promesa del docstring debe verse en la salida.
    cmp = _comparacion(presente_en_actual=False)
    assert cmp["por_categoria"]["filter_set"]["presente"] is False
    runner.print_comparison(cmp)
    out = capsys.readouterr().out
    assert "—" in out and "0.0" in out, \
        f"la categoría solo-en-baseline no se marcó con «—»:\n{out}"


def test_print_report_imprime_el_conteo_por_engine(capsys):
    # Spec §8: «Motor — cuántos casos usaron LLM vs determinista» está en los
    # diagnósticos de salida, pero print_report no lo imprimía: una corrida
    # «llm» se leía sin poder ver cuántos casos respondió el modelo.
    resultados = [{"categoria": "evidence_qa", "ok": True, "engine": "llm",
                   "mode": "evidence"},
                  {"categoria": "evidence_qa", "ok": True,
                   "engine": "deterministic", "mode": "evidence"},
                  {"categoria": "retrieval", "ok": True, "engine": "retrieval",
                   "mode": "evidence"},
                  {"categoria": "filter_set", "ok": None, "error": "boom",
                   "id": "fs-roto"}]
    rep = build_report(resultados=resultados,
                       grounding={"rechazo_pct": 0.0, "inventados": 0},
                       fewshot={"cobertura_pct": 0.0}, motor="llm",
                       hash_muestra="abc", modelo="qwen2.5:3b",
                       num_ctx=4096, temperatura=0.2)
    runner.print_report(rep)
    out = capsys.readouterr().out
    assert "1 llm" in out and "1 determinista" in out and "1 retrieval" in out, \
        f"el conteo por engine no se imprime:\n{out}"


# --- Cableado de los guards en run() ------------------------------------------
# Los guards ya estaban implementados, pero NADA verificaba que run() los
# invocara en el momento correcto: si alguien quitara la llamada, la suite
# entera seguiría verde y las corridas publicarían sin verificar el motor.

def _pasada(resultados):
    return {"resultados": resultados, "grounding": {"rechazo_pct": 0.0,
                                                    "inventados": 0},
            "fewshot": {"cobertura_pct": 0.0}, "rutados_a_agregacion": []}


def test_run_aborta_cuando_en_llm_no_se_uso(monkeypatch, tmp_path):
    # motor=llm con TODOS los casos de evidencia en el determinista (100% >
    # 30%) → SystemExit ruidoso, no un informe silencioso.
    pasada_llm_falla = [{"categoria": "filter_set", "ok": True,
                         "engine": "deterministic", "mode": "evidence"}] * 10
    monkeypatch.setattr(runner, "_probe_ollama", lambda: None)
    monkeypatch.setattr(runner, "_run_pasada", lambda ids: _pasada(pasada_llm_falla))
    with pytest.raises(SystemExit, match="GUARD"):
        runner.run(motor="llm", tmp=tmp_path)
    # --forzar es la salida explícita: la corrida completa devuelve informe.
    informe = runner.run(motor="llm", forzar=True, tmp=tmp_path)
    assert informe["motor"] == "llm"


def test_run_aborta_si_en_determinista_responde_el_llm(monkeypatch, tmp_path):
    # El backstop inverso: pasada «determinista» con engine=llm significa que
    # LLM_OFF no alcanzó a la instancia real → la línea base mediría al modelo.
    pasada_contaminada = [{"categoria": "filter_set", "ok": True,
                           "engine": "llm", "mode": "evidence"}] * 3
    monkeypatch.setattr(runner, "_run_pasada",
                        lambda ids: _pasada(pasada_contaminada))
    with pytest.raises(SystemExit, match="determinista"):
        runner.run(motor="determinista", tmp=tmp_path)


def test_run_pasa_con_los_guards_en_verde(monkeypatch, tmp_path):
    # Camino feliz: la pasada determinista responde sin engine=llm (backstop
    # en verde) y la pasada llm deja ≤30% de evidencia en el determinista
    # (motor_guard en verde) → devuelve el informe sin abortar. El fake
    # distingue las pasadas por ORDEN (los tests no dependen de la config
    # real de la máquina ni de Ollama).
    det = [{"categoria": "evidence_qa", "ok": True, "engine": "deterministic",
            "mode": "evidence"}] * 10
    llm = [{"categoria": "evidence_qa", "ok": True, "engine": "llm",
            "mode": "evidence"}] * 7
    llm += [{"categoria": "evidence_qa", "ok": True, "engine": "deterministic",
             "mode": "evidence"}] * 3
    orden = {"n": 0}

    def _fake(ids):
        orden["n"] += 1
        return _pasada(det if orden["n"] == 1 else llm)

    monkeypatch.setattr(runner, "_probe_ollama", lambda: None)
    monkeypatch.setattr(runner, "_run_pasada", _fake)
    informe = runner.run(motor="ambos", tmp=tmp_path)
    assert isinstance(informe, list) and len(informe) == 2
    assert [i["motor"] for i in informe] == ["determinista", "llm"]


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


@pytest.fixture
def doble_carga():
    """Carga la forma `backend.<nombre>` (entorno de `uvicorn backend.main:app`).

    La suite corre desde `backend/` con solo esa raíz en sys.path, así que sin
    este empujón solo existiría la instancia top-level y el test no probaría
    nada. Si el paquete no es importable se omite (el runner igual debe
    parchear lo que haya).

    Limpia tras de sí (minor 11): quita de sys.path la raíz que agregó y
    descarga de sys.modules los módulos que ESTE helper cargó — sin eso, el
    estado de doble carga se filtra al resto de la suite y los tests quedan
    orden-dependientes. Los módulos que ya existían no se tocan (no son suyos).
    """
    import contextlib
    import importlib
    import sys
    from pathlib import Path

    estado = {"raiz": None, "nuevos": set()}

    def _forzar(nombre: str) -> None:
        raiz = str(Path(__file__).resolve().parents[2])
        if raiz not in sys.path:
            sys.path.insert(0, raiz)
            estado["raiz"] = raiz
        antes = set(sys.modules)
        try:
            importlib.import_module(f"backend.{nombre}")
        except ImportError:
            pass
        estado["nuevos"] |= set(sys.modules) - antes

    yield _forzar

    for n in estado["nuevos"]:
        sys.modules.pop(n, None)
    if estado["raiz"] is not None:
        with contextlib.suppress(ValueError):
            sys.path.remove(estado["raiz"])


def test_llm_off_alcanza_las_dos_instancias_de_ai_config(doble_carga):
    # La app en producción corre como `uvicorn backend.main:app` (raíz del
    # proyecto en sys.path): allí `main.ai_config` es la instancia
    # `backend.ai_config`, NO la top-level. Parchear solo la top-level deja
    # el LLM encendido y la «línea base» publicaría puntuación del modelo.
    import ai_config

    doble_carga("ai_config")
    mods = _instancias("ai_config")
    if len(mods) < 2:
        pytest.skip("paquete backend no importable en este entorno")
    # Lo que se exige es RESTAURACIÓN (el get original vuelto a poner), no
    # leer ai_config.json real: «enabled is not False» pasaba trivialmente
    # con la config real de la máquina y además se rompe en una máquina con
    # enabled=false (minor 6). La snapshot es independiente del entorno.
    antes = {mod: mod.get() for mod in mods}
    with runner.LLM_OFF():
        for mod in mods:
            assert mod.get()["enabled"] is False, f"{mod.__name__} quedó encendido"
    for mod in mods:
        assert mod.get() == antes[mod], f"{mod.__name__} no restauró su get()"


def test_aislamiento_redirige_las_dos_instancias_de_feedback(tmp_path, doble_carga):
    # Mismo motivo que el test anterior: si solo se redirige la instancia
    # top-level, la corrida ESCRIBE en el feedback.json real del analista y
    # contamina su memoria few-shot (spec §4.1, la garantía nº 1).
    import feedback_store

    doble_carga("feedback_store")
    mods = _instancias("feedback_store")
    if len(mods) < 2:
        pytest.skip("paquete backend no importable en este entorno")
    reales = {mod: mod.FEEDBACK_PATH for mod in mods}
    with runner._isolated_store(tmp_path):
        for mod in mods:
            assert mod.FEEDBACK_PATH == tmp_path / "feedback.json", \
                f"{mod.__name__} sigue apuntando al JSON real"
            assert {e["id"] for e in mod._load()} == _SEED_IDS
    for mod, real in reales.items():
        assert mod.FEEDBACK_PATH == real


def test_aislamiento_no_lee_ni_escribe_la_memoria_real(tmp_path):
    import feedback_store

    real = feedback_store.FEEDBACK_PATH
    # El store aislado arranca con el SNAPSHOT VERSIONADO (ruling issue 5),
    # nunca con la memoria real del analista: la siembra es lo que permite
    # que §6 discrimine sin romper el aislamiento.
    with runner._isolated_store(tmp_path):
        assert feedback_store.FEEDBACK_PATH != real
        assert {e["id"] for e in feedback_store._load()} == _SEED_IDS
        feedback_store.add_correction("¿Qué pasó en DC-01?", "Criterio de prueba.")
        assert feedback_store.status()["total"] == len(_SEED_IDS) + 1
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
