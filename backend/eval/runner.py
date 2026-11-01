"""runner.py — orquestación del harness de evaluación RAG+LLM.

Sube la muestra fija, puntúa los 24 casos —más los 7 de la sección `report`
del informe ejecutivo— con la verdad calculable de `cases.py` y devuelve un
informe comparable entre corridas. Cuatro garantías (spec §4 y §9):

1. **Aislamiento**: la memoria real del analista (`feedback.json`) no se lee
   ni se escribe; `_isolated_store` la redirige a un temporal (junto a su
   colección semántica), la siembra con el snapshot versionado
   `fixtures/fewshot_seed.json` y limpia la caché en RAM antes y después.
2. **Índice listo**: espera activa a `rag_store.has_index()`; sin índice la
   recuperación sería solo léxica y la corrida engañosa → RuntimeError.
3. **Motor verificado**: `--motor determinista` apaga el LLM parcheando
   `ai_config.get` (nunca la config real); con LLM se sondea el proveedor antes de
   puntuar nada y el guard `motor_guard` aborta si el modelo no se usó.
4. **Errores visibles**: un caso que revienta se registra con `ok: None` y
   `error`, se cuenta aparte y no contamina el cómputo de aciertos.

Entrada por `TestClient` al endpoint real `/ask` (el mismo camino que el
analista), nunca llamando `answer_question()` a pelo.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from eval import scoring
from eval.cases import CASES, FIXTURE, K, REPORT_CASES, load_fixture

# % máximo de casos de evidencia que pueden responder con el determinista en
# una corrida «llm»: por encima, la puntuación mide el fallback, no el modelo.
MOTOR_GUARD_PCT = 30.0

# Snapshot few-shot versionado (ruling del issue 5, spec §6): el aislamiento
# (store temporal + DELETE /chat por caso) impide que remember_implicit
# acumule ejemplos durante la corrida, así que SIN siembra rag_examples_used
# es 0 por construcción y el diagnóstico de cobertura no discrimina nada.
SEED_PATH = Path(__file__).parent / "fixtures" / "fewshot_seed.json"

# Colección semántica TEMPORAL del feedback aislado. La real (soc_feedback)
# vive en el mismo Chroma que el RAG y NO sigue la redirección del JSON:
# con la colección real intacta, similar() recibiría hits cuyos ids no están
# en el JSON sembrado y los descartaría sin caer a léxico (el seed sería
# muerto); y un _reindex contra la colección real BORRARÍA los ejemplos
# indexados del analista. Nombre fijo (corrida única por diseño) y se borra
# al salir.
EVAL_FEEDBACK_COLLECTION = "eval-feedback"


# ---------------------------------------------------------------------------
# Aislamiento del estado real
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _patched_modules(nombre: str, attr: str, valor: Any) -> Iterator[List[Any]]:
    """Parchea `attr` en TODAS las instancias del módulo cargadas en el proceso.

    El backend importa con el patrón dual `try: from backend import X except
    ImportError: import X` (ai_service.py:42-47, main.py:61-71): si la raíz del
    proyecto está en `sys.path` —como en `uvicorn backend.main:app`— conviven
    `feedback_store` y `backend.feedback_store` como objetos DISTINTOS, y
    `main`/`ai_service` usan la del paquete. Parchear solo la top-level
    redirige un módulo que nadie lee: la corrida tocaría el feedback.json real
    del analista y el LLM seguiría encendido en la pasada «determinista».
    Devuelve las instancias parcheadas (para inspección en tests).

    Importante: se ASEGURA la carga ANTES de escanear. En el proceso canónico
    del CLI la entrada a `_isolated_store` ocurre antes de que `main` se
    importe (eso pasa dentro de `_run_pasada`); con un escaneo pasivo, ese
    import tardío crearia una instancia nueva que ignora la redirección y la
    corrida leería el feedback.json real (regresión medida — ver
    test_eval_isolation_fresh.py). Al cargar primero, el import tardío de
    `main` reutiliza el módulo ya parcheado de sys.modules.
    """
    import importlib
    import sys

    for name in (nombre, f"backend.{nombre}"):
        try:
            importlib.import_module(name)
        except ImportError:
            pass

    # Deduplica POR IDENTIDAD: con alias de un solo mundo (sys.modules
    #["backend.X"] es el mismo objeto que ["X"]) el escaneo devuelve la misma
    # instancia bajo dos nombres — parchearla dos veces duplicaría el wrapper
    # y rompería la aserción de integridad de LLM_OFF. En un mundo dual (dos
    # cargas reales) cada instancia distinta se parchea una sola vez.
    mods: List[Any] = []
    for name, m in sys.modules.items():
        if (name == nombre or name == f"backend.{nombre}") and m is not None:
            if not any(m is seen for seen in mods):
                mods.append(m)
    originals = [(mod, getattr(mod, attr)) for mod in mods]
    try:
        for mod in mods:
            setattr(mod, attr, valor(mod) if callable(valor) else valor)
        yield mods
    finally:
        for mod, original in originals:
            setattr(mod, attr, original)


def _sembrar_store(mods: List[Any], tmp: Path) -> None:
    """Copia el snapshot versionado al store temporal y lo indexa.

    El JSON sembrado es la fuente de verdad; `_reindex` sincroniza la
    colección TEMPORAL (ya parcheada) con él. Si el snapshot no existiera,
    el store queda vacío como antes (fail-open: §6 reportará 0%, no tumba
    la corrida).
    """
    seed: List[Dict[str, Any]] = []
    if SEED_PATH.exists():
        seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    destino = tmp / "feedback.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for mod in mods:
        mod._cache = None
    if seed:
        # Una sola instancia indexa: ambas apuntan a la misma colección
        # temporal y el writer único evita carreras sobre el mismo Chroma.
        with contextlib.suppress(Exception):
            mods[0]._reindex(seed)


def _limpiar_coleccion_temp(mods: List[Any]) -> None:
    """Borra la colección temporal del feedback aislado (autolimpieza).

    Corridas anteriores que crashearon dejan la colección en disco; sin
    esta limpieza se acumularían documentos de seeds viejos.
    """
    if not mods:
        return
    with contextlib.suppress(Exception):
        client = mods[0].rag_store.get_client()
        if client is not None:
            client.delete_collection(name=EVAL_FEEDBACK_COLLECTION)


@contextlib.contextmanager
def _isolated_store(tmp: Path) -> Iterator[None]:
    """Redirige el feedback del analista a un JSON temporal, lo siembra con
    el snapshot versionado y aísla también su colección semántica.

    La caché en RAM (`_cache`) se invalida antes y después: redirigir el path
    sin limpiarla haría que la corrida leyera la memoria real del analista.
    `ai_config.json` NO se toca (el motor se conmuta parcheando `get`).

    La siembra (spec §6, ruling del issue 5) es lo que permite al diagnóstico
    few-shot discriminar: con store vacío, rag_examples_used es 0 siempre.
    La colección semántica se parchea a un nombre temporal porque comparte
    cliente con el RAG (ver EVAL_FEEDBACK_COLLECTION); al salir se restaura
    y la temporal se elimina.
    """
    def _redirect(mod: Any) -> Path:
        mod._cache = None                      # invalidar ANTES de redirigir
        return tmp / "feedback.json"

    with _patched_modules("feedback_store", "FEEDBACK_PATH", _redirect) as mods:
        with _patched_modules("feedback_store", "FEEDBACK_COLLECTION",
                              EVAL_FEEDBACK_COLLECTION) as col_mods:
            for mod in col_mods:
                mod._cache = None
            _sembrar_store(mods, tmp)
            try:
                yield
            finally:
                for mod in mods:
                    mod._cache = None
                _limpiar_coleccion_temp(col_mods)


@contextlib.contextmanager
def LLM_OFF() -> Iterator[None]:
    """Apaga el LLM parcheando `ai_config.get` en el proceso (no la config real).

    `answer_question` decide el motor leyendo `ai_config.get()`: envolviendo
    el original con `enabled: False` la rama remota ni se intenta y toda la
    respuesta sale del determinista — sin escribir ai_config.json. El wrapper
    se construye POR INSTANCIA desde su propio `get` original, porque las dos
    cargas del módulo pueden tener estado independiente.
    """
    originals: Dict[Any, Any] = {}

    def _wrap(mod: Any):
        originals[mod] = mod.get
        original_get = mod.get

        def _off():
            return {**original_get(), "enabled": False}

        return _off

    with _patched_modules("ai_config", "get", _wrap) as mods:
        assert len(originals) == len(mods)
        try:
            yield
        finally:
            for mod, original_get in originals.items():
                mod.get = original_get


def _wait_for_index(ready: Callable[[], bool], timeout: float = 120.0,
                    poll: float = 0.5) -> bool:
    """Espera activa a que el índice RAG esté construido; True si está listo.

    `ready` es inyectable para testear sin ChromaDB. Si expira lanza
    RuntimeError: contestar solo con léxica sin avisar sería una corrida
    engañosa (spec §4) — mejor fallar ruidosamente que puntuar basura.
    """
    deadline = time.monotonic() + timeout
    while True:
        if ready():
            return True
        if time.monotonic() >= deadline:
            raise RuntimeError("el índice RAG no indexó a tiempo («no index»): "
                               "una corrida sin índice mediría solo léxica")
        time.sleep(poll)


def _asegurar_indice(analysis_id: str) -> None:
    """Garantiza el índice RAG antes de puntuar; RuntimeError si no aparece.

    Dos causas reales que esta función cubre (ambas medidas en este repo):

    1. `rag_store._get_collection` marca `_initialized = True` ANTES de cargar
       el modelo de embeddings: otro hilo que llegue durante la carga ve
       «inicializado» con la colección aún en None y la indexación del
       background task se rinde en silencio (`available: False`). Mismo motivo
       por el que `is_available()` es False mientras carga.
    2. El índice persiste en disco y las suites de prueba lo purgan.

    El reindex de `index_events` es idempotente; sin esta garantía la corrida
    mediría recuperación puramente léxica sin que nadie lo notara (spec §4).
    """
    from main import _enrich_records, analysis_store

    import rag_store

    try:
        _wait_for_index(lambda: rag_store.has_index(analysis_id),
                        timeout=30.0, poll=0.5)
        return
    except RuntimeError:
        pass

    # El motor puede seguir cargando el modelo: se espera a que esté listo.
    deadline = time.monotonic() + 120.0
    while not rag_store.is_available():
        if time.monotonic() >= deadline:
            raise RuntimeError("el índice RAG no indexó («no index») y el motor "
                               "semántico no está disponible: la corrida mediría "
                               "solo léxica")
        time.sleep(0.5)

    entry = analysis_store.get(analysis_id) or {}
    df = entry.get("dataframe")
    if df is None or df.empty:
        raise RuntimeError("el índice RAG no indexó («no index») y no hay datos "
                           "para reindexar: la corrida mediría solo léxica")
    rag_store.index_events(analysis_id, _enrich_records(df))
    _wait_for_index(lambda: rag_store.has_index(analysis_id),
                    timeout=120.0, poll=0.5)


# ---------------------------------------------------------------------------
# Informe y comparación (funciones puras)
# ---------------------------------------------------------------------------
def build_report(resultados: List[Dict[str, Any]], grounding: Dict[str, Any],
                 fewshot: Dict[str, Any], motor: str, hash_muestra: str,
                 modelo: str, temperatura: float,
                 rutados_a_agregacion: Optional[List[str]] = None,
                 informe_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Informe a partir de los resultados por caso (media macro por categoría).

    Un caso con `ok: None` (excepción) se cuenta en `errores` y NO entra al
    cómputo de aciertos: ni fallo de scoring, ni omisión silenciosa.
    `informe_meta` lleva la meta del POST /report único (latencia y fuente);
    los llamadores viejos que no lo pasan dejan la sección vacía.
    """
    por_categoria: Dict[str, Dict[str, Any]] = {}
    errores = 0
    for r in resultados:
        if r.get("ok") is None:
            errores += 1
            continue
        cat = por_categoria.setdefault(r["categoria"], {"casos": 0, "aciertos": 0})
        cat["casos"] += 1
        cat["aciertos"] += 1 if r["ok"] else 0
    for cat in por_categoria.values():
        cat["pct"] = round(100.0 * cat["aciertos"] / cat["casos"], 2) if cat["casos"] else 0.0

    macro = scoring.macro_average({k: v["pct"] for k, v in por_categoria.items()})
    return {
        "global": macro,
        "por_categoria": por_categoria,
        "errores": errores,
        "grounding": grounding,
        "fewshot": {**fewshot, "por_categoria": _cobertura_por_categoria(resultados)},
        "latencia": _latencia(resultados),
        "rutados_a_agregacion": rutados_a_agregacion or [],
        "informe": informe_meta or {},
        "motor": motor,
        "hash_muestra": hash_muestra,
        "modelo": modelo,
        "temperatura": temperatura,
        "casos": resultados,
    }


def _cobertura_por_categoria(resultados: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Casos con ≥1 ejemplo inyectado, desglosados por categoría.

    Usa `_elegibles_fewshot` como exclusor: un caso en la rama de agregación
    no pide criterio (el conteo sale del resumen) y `retrieval` jamás pasa por
    el LLM. El denominador global (en `_run_pasada`) usa el MISMO criterio, así
    que el % global reconcilia con estas líneas — antes divergían y
    `fallos_con_0` contaba fallos que no pudieron llevar criterio.
    """
    por_cat: Dict[str, Dict[str, int]] = {}
    for r in _elegibles_fewshot(resultados):
        cat = por_cat.setdefault(r["categoria"], {"casos": 0, "con_ejemplos": 0})
        cat["casos"] += 1
        if (r.get("rag_examples_used") or 0) > 0:
            cat["con_ejemplos"] += 1
    return {c: {"casos": v["casos"], "con_ejemplos": v["con_ejemplos"],
                "cobertura_pct": round(100.0 * v["con_ejemplos"] / v["casos"], 1)
                if v["casos"] else 0.0}
            for c, v in por_cat.items()}


def _elegibles_fewshot(resultados: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Casos que PUEDEN llevar criterio few-shot (exclusor único y compartido).

    Se excluye: casos con excepción (sin respuesta), la rama de agregación
    (el conteo sale del resumen, sin prompt few-shot), `retrieval` (puntúa
    el retriever solo, sin LLM) y la sección `report` (el informe ejecutivo
    se genera con un único POST /report sin ejemplos inyectados: contar sus
    casos hundiría el % sin discriminar nada). Toda la métrica few-shot
    —global y por categoría— debe pasar por aquí para que los denominadores
    coincidan.
    """
    return [r for r in resultados
            if r.get("ok") is not None
            and r.get("mode") != "aggregate"
            and r.get("categoria") not in ("retrieval", "report")]


def _latencia(resultados: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Media de `ms` por categoría y global (solo casos con latencia medida).

    Los 7 casos `report` comparten UN solo POST /report: sus registros
    repiten la misma `ms`, así que la media global la cuenta UNA vez (la de
    su categoría), no 7 — el promedio global no se infla por el reparto del
    informe único (hallazgo 8 de la revisión final).
    """
    latencias: Dict[str, List[int]] = {}
    for r in resultados:
        if r.get("ms") is not None:
            latencias.setdefault(r["categoria"], []).append(int(r["ms"]))
    total = [ms for cat, v in latencias.items() if cat != "report" for ms in v]
    if "report" in latencias:
        v = latencias["report"]
        total.append(round(sum(v) / len(v)))
    return {
        "media_ms": round(sum(total) / len(total)) if total else 0,
        "por_categoria": {c: round(sum(v) / len(v)) for c, v in latencias.items()},
    }


def compare_reports(actual: dict, baseline: dict) -> dict:
    """Deltas entre corridas; `hash_ok=False` es advertencia, no fallo.

    Las categorías se recorren por la UNIÓN de ambas corridas: si una existe
    solo en la línea base (todos sus casos revientan ahora), no puede perderse
    en la comparación — se muestra «— → 0» en vez de desaparecer en silencio.
    """
    por_categoria = {}
    cats = list(actual["por_categoria"]) + [
        c for c in baseline["por_categoria"] if c not in actual["por_categoria"]
    ]
    for cat in cats:
        info = actual["por_categoria"].get(cat)
        antes = baseline["por_categoria"].get(cat, {}).get("pct", 0.0)
        despues = info["pct"] if info else 0.0      # ausente = 0 aciertos
        por_categoria[cat] = {"antes": antes, "despues": despues,
                              "delta": round(despues - antes, 2),
                              "presente": info is not None}
    return {
        "hash_ok": actual["hash_muestra"] == baseline["hash_muestra"],
        "delta_global": round(actual["global"] - baseline["global"], 2),
        "por_categoria": por_categoria,
        "actual": actual["motor"], "baseline": baseline["motor"],
    }


def motor_guard(resultados: List[Dict[str, Any]], forzar: bool) -> bool:
    """¿La corrida «llm» usó el modelo de verdad? (False → abortar)

    El denominador son los casos de EVIDENCIA que el LLM puede responder:
    se excluyen los de agregación (van al determinista por diseño) y los
    `retrieval` (responden con engine="retrieval" por definición: son el
    retriever solo, sin modelo). Contar los retrieval diluiría el umbral —
    con 6 de ellos el 30 % efectivo subía a 38 % sobre los 13 casos reales.

    Un caso con excepción tampoco cuenta: no prueba nada sobre el motor. Si
    NINGÚN caso de evidencia puntuó, el guard no puede verificar que el
    modelo se usó y devuelve False (fail-closed): una corrida «llm» con cero
    uso real del LLM no se publica como del modelo sin --forzar (spec §7,
    mejor fallar ruidosamente que publicar sin verificar). Los errores se
    muestran aparte como `errores` en build_report.
    """
    if forzar:
        return True
    elegibles = [r for r in resultados
                 if r.get("mode") == "evidence" and r.get("engine") != "retrieval"
                 and r.get("ok") is not None]
    if not elegibles:
        return False
    det = sum(1 for r in elegibles if r.get("engine") == "deterministic")
    return (100.0 * det / len(elegibles)) <= MOTOR_GUARD_PCT


def determinista_guard(resultados: List[Dict[str, Any]]) -> bool:
    """Backstop de la pasada «determinista»: ¿algún caso respondió con el LLM?

    Especular (motor_guard) comprueba que el modelo SÍ se usó; esta función
    comprueba lo contrario: que el LLM NO se usó cuando se suponía apagado.
    Un `engine == "llm"` aquí significa que el parche `LLM_OFF` no alcanzó a la
    instancia que la app realmente usa —el patrón dual de import del backend
    puede dejar dos cargas del módulo— y la «línea base» estaría midiendo al
    modelo sin que nada lo señalara (spec §7: fallar ruidosamente).

    También detecta el caso MÁS PROBABLE con un 3B: si el LLM corre y su
    respuesta es rechazada por el guard de grounding, `engine` vuelve
    "deterministic" (spec §5.3) — sin este check el backstop pasaría y la
    «línea base» publicaría puntuación del modelo. Una pasada determinista
    JAMÁS puede producir `grounding_rechazado` (con el LLM apagado,
    `_remote_answer` sale antes de llamar a `chat`).
    """
    no_agregados = [r for r in resultados
                    if r.get("ok") is not None and r.get("mode") != "aggregate"]
    if any(r.get("grounding_rechazado") for r in no_agregados):
        return False
    return not any(r.get("engine") == "llm" for r in no_agregados)


# ---------------------------------------------------------------------------
# Persistencia e impresión
# ---------------------------------------------------------------------------
def save_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def load_report(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def print_report(report: dict) -> None:
    print(f"\n=== Eval RAG+LLM · motor={report['motor']} · modelo={report['modelo']} ===")
    print(f"muestra: hash {report['hash_muestra']} · "
          f"temperatura {report['temperatura']}")
    print("| categoría    | casos | aciertos |    % |")
    print("|--------------|------:|---------:|-----:|")
    for cat, info in report["por_categoria"].items():
        print(f"| {cat:<12} | {info['casos']:>5} | {info['aciertos']:>8} | {info['pct']:>4.1f} |")
    print(f"| {'GLOBAL (macro)':<12} | {'':>5} | {'':>8} | {report['global']:>4.1f} |")

    lat = report.get("latencia") or {}
    if lat:
        print(f"latencia media: {lat['media_ms']} ms "
              + " · ".join(f"{c} {v} ms" for c, v in lat["por_categoria"].items()))

    # Spec §7: la meta del POST único (fuente, latencia, modelo, error de
    # medida) debe verse en la corrida, no solo quedar en el JSON.
    informe = report.get("informe") or {}
    if informe:
        partes = []
        if informe.get("source"):
            partes.append(f"fuente {informe['source']}")
        if informe.get("ms") is not None:
            partes.append(f"{informe['ms']} ms")
        if informe.get("model"):
            partes.append(f"modelo {informe['model']}")
        if informe.get("error"):
            partes.append(f"ERROR: {informe['error']}")
        print("informe: " + " · ".join(partes))

    if report["errores"]:
        print(f"! {report['errores']} caso(s) con excepción (ver detalle en el JSON)")
    rutados = report.get("rutados_a_agregacion") or []
    if rutados:
        print(f"! {len(rutados)} caso(s) NO agregados terminaron en la rama de "
              "agregación (nunca llegaron al LLM):")
        print(f"  {', '.join(rutados)} — reformúlalos pidiendo filas "
              "(«muéstrame…») para que vayan al modelo")
    g = report["grounding"]
    print(f"grounding: {g['rechazo_pct']:.1f}% de respuestas rechazadas por el guard · "
          f"{g['inventados']} ID(s) inventados (esperado 0)")
    # Spec §8: «Motor — cuántos casos usaron LLM vs determinista». Sin esta
    # línea, una corrida «llm» se leía sin ver cuánto respondió el modelo.
    _orden_engine = ("llm", "deterministic", "retrieval")
    _etiqueta = {"llm": "llm", "deterministic": "determinista",
                 "retrieval": "retrieval"}
    conteo: Dict[str, int] = {}
    for r in report["casos"]:
        eng = r.get("engine")
        if eng:
            conteo[eng] = conteo.get(eng, 0) + 1
    if conteo:
        partes = [f"{conteo[e]} {_etiqueta.get(e, e)}"
                  for e in sorted(conteo,
                                  key=lambda x: (_orden_engine.index(x)
                                                 if x in _orden_engine else 99))]
        print(f"motor: {' · '.join(partes)}")
    f = report["fewshot"]
    print(f"few-shot:  {f['cobertura_pct']:.1f}% de los casos con ≥1 ejemplo inyectado")
    for cat, info in (f.get("por_categoria") or {}).items():
        print(f"  {cat:<12} {info['con_ejemplos']}/{info['casos']} "
              f"({info['cobertura_pct']:.1f}%)")
    if f.get("fallos_con_0") or f.get("fallos_con_1mas"):
        print(f"  fallos: {f['fallos_con_0']} con 0 ejemplos, "
              f"{f['fallos_con_1mas']} con ≥1 (¿el criterio aprendido ayudó?)")
    for r in report["casos"]:
        if r.get("ok") is None:
            print(f"  ! {r['id']}: {r.get('error')}")


def print_comparison(cmp: dict) -> None:
    print(f"\n=== Comparación ({cmp['baseline']} → {cmp['actual']}) ===")
    if not cmp["hash_ok"]:
        print("! ADVERTENCIA: la muestra difiere entre corridas (hash distinto):")
        print("  los deltas NO son directamente comparables.")
    for cat, d in cmp["por_categoria"].items():
        signo = "+" if d["delta"] >= 0 else ""
        # Categoría ausente en la corrida actual: se marca «—» (la promesa
        # documentada de compare_reports) en vez de fingir un 0 medido.
        antes = f"{d['antes']:>5.1f}" if d.get("presente", True) else "    —"
        print(f"  {cat:<12} {antes} → {d['despues']:>5.1f}  ({signo}{d['delta']:.1f})")
    signo = "+" if cmp["delta_global"] >= 0 else ""
    print(f"  {'GLOBAL':<12} delta {signo}{cmp['delta_global']:.1f}")


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------
def _hash_fixture() -> str:
    return hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:16]


def _score_case(case, expected, body: Optional[dict], cited: List[str]) -> Any:
    """Scoring por categoría sobre la respuesta (o los IDs recuperados)."""
    if case.categoria == "aggregate":
        return scoring.number_in_answer(str(body.get("answer") or ""), expected,
                                        case.unidades)
    if case.categoria == "refusal":
        return scoring.denial_ok(str(body.get("answer") or ""), cited) and not cited
    # filter_set / evidence_qa / retrieval: ancho → precisión@k, estrecho → hit@k
    if scoring.is_wide(expected):
        return scoring.precision_at_k(cited, expected) >= 0.5
    return scoring.hit_at_k(cited, expected)


def _meta_informe(informe: Any, df_informe: Any, informe_ms: int,
                  informe_error: Optional[str]
                  ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Meta y registros de los 7 casos `report` del POST único de la pasada.

    El /report devuelve lo que el LLM produjo: si el JSON no es un objeto
    (p. ej. una lista), no hay nada que puntuar y se trata como fallo de
    medida — los 7 casos quedan con excepción marcada en vez de tumbar la
    corrida con AttributeError (hallazgo 9 de la revisión final).
    """
    meta: Dict[str, Any] = {"ms": informe_ms}
    registros: List[Dict[str, Any]] = []
    if isinstance(informe, dict) and df_informe is not None:
        meta["source"] = informe.get("source")
        if informe.get("model"):
            meta["model"] = informe["model"]
        for caso in REPORT_CASES:
            registro: Dict[str, Any] = {"id": caso.id, "categoria": "report",
                                        "ms": informe_ms}
            try:
                registro["ok"] = bool(caso.verifica(informe, df_informe))
            except Exception as exc:  # noqa: BLE001 – un caso nunca tumba la corrida
                registro["ok"] = None
                registro["error"] = f"{type(exc).__name__}: {exc}"
            registros.append(registro)
    else:
        if informe is not None and not isinstance(informe, dict):
            meta["error"] = "el /report devolvió JSON que no es un objeto"
        else:
            meta["error"] = (informe_error
                             or "sin df del análisis para puntuar el informe")
        for caso in REPORT_CASES:
            registros.append({"id": caso.id, "categoria": "report", "ok": None,
                              "ms": informe_ms, "error": meta["error"]})
    return meta, registros


def _run_pasada(dataset_ids: set) -> Dict[str, Any]:
    """Una pasada completa: los 24 casos y la sección `report` del informe
    (el motor ya está conmutado por el caller: `LLM_OFF` o config real)."""
    from fastapi.testclient import TestClient

    import rag_store
    from main import analysis_store, app

    resultados: List[Dict[str, Any]] = []
    # Informe ejecutivo de la pasada (7a): UN POST tras el índice y puntúe
    # al final con los casos report. Inicializados por legibilidad; un fallo
    # antes del POST propaga la excepción y la pasada no continúa.
    informe: Optional[Dict[str, Any]] = None
    informe_error: Optional[str] = None
    informe_ms = 0
    df_informe: Any = None
    with TestClient(app) as client:
        # Subida + índice RAG construido (sin índice, solo léxica: engañoso).
        with open(FIXTURE, "rb") as fh:
            res = client.post("/api/analyze", files={"file": ("sample.xlsx", fh)})
        res.raise_for_status()
        analysis_id = res.json()["analysis_id"]
        deadline = time.monotonic() + 120.0
        while True:
            state = client.get(f"/api/analysis/{analysis_id}").json()["status"]
            if state == "done":
                break
            if state == "error" or time.monotonic() > deadline:
                raise RuntimeError(f"el análisis terminó en «{state}»: no hay índice "
                                   "RAG confiable para evaluar")
            time.sleep(0.2)
        _asegurar_indice(analysis_id)

        # El informe se puntuará contra el df REAL del análisis (mismo
        # origen que el endpoint), guardado aquí, antes del finally.
        df_informe = (analysis_store.get(analysis_id) or {}).get("dataframe")

        # 7a: generación del informe ejecutivo, UN POST por pasada con el
        # índice listo, midiendo `ms` con la misma mecánica que /ask. El
        # motor ya está conmutado fuera (LLM_OFF o config real), así que
        # esta pasada usa el vigente sin decidir nada aquí. Un fallo de
        # MEDIDA no se esconde: informe_error marca la corrida (spec §7).
        started = time.perf_counter()
        try:
            res = client.post(f"/api/analysis/{analysis_id}/report")
            res.raise_for_status()
            informe = res.json()
        except Exception as exc:  # noqa: BLE001 – la medida del informe marca la corrida
            informe_error = f"{type(exc).__name__}: {exc}"
        informe_ms = int((time.perf_counter() - started) * 1000)

        try:
            for case in CASES:
                registro: Dict[str, Any] = {"id": case.id, "categoria": case.categoria}
                started = time.perf_counter()
                try:
                    client.delete(f"/api/analysis/{analysis_id}/chat")
                    if case.categoria == "retrieval":
                        hits = rag_store.query_events(analysis_id, case.question,
                                                      n_results=K)
                        cited = [str(h.get("log_id") or "") for h in hits]
                        expected = case.expected(load_fixture())
                        registro["ok"] = bool(_score_case(case, expected, None,
                                                          cited))
                        registro.update(engine="retrieval", mode="evidence",
                                        rag_examples_used=0, grounding_rechazado=False)
                    else:
                        res = client.post(f"/api/analysis/{analysis_id}/ask",
                                          json={"question": case.question,
                                                "n_results": K})
                        body = res.json()
                        expected = case.expected(load_fixture())
                        cited = scoring.cited_ids(str(body.get("answer") or ""))
                        registro["ok"] = bool(_score_case(case, expected, body,
                                                          cited))
                        registro.update(
                            engine=body.get("engine"), mode=body.get("mode"),
                            rag_examples_used=body.get("rag_examples_used", 0),
                            grounding_rechazado="grounding_rechazado"
                            in (body.get("warnings") or []),
                        )
                    registro["inventados"] = len(scoring.invented_ids(cited, dataset_ids))
                except Exception as exc:  # noqa: BLE001 – un caso nunca tumba la corrida
                    registro["ok"] = None
                    registro["error"] = f"{type(exc).__name__}: {exc}"
                registro["ms"] = int((time.perf_counter() - started) * 1000)
                resultados.append(registro)
        finally:
            with contextlib.suppress(Exception):
                client.delete(f"/api/analysis/{analysis_id}/chat")
                rag_store.delete_analysis(analysis_id)

    # Grounding global y cobertura few-shot (spec §6-7). El denominador few-shot
    # pasa por _elegibles_fewshot: MISMO exclusor que la cobertura por categoría,
    # para que el % global reconcilie con las líneas de abajo (antes contaba
    # fallos de `retrieval`, que jamás pueden llevar criterio).
    puntuados = [r for r in resultados if r.get("ok") is not None]
    con_rechazo = sum(1 for r in puntuados if r.get("grounding_rechazado"))
    elegibles_fewshot = _elegibles_fewshot(resultados)
    con_ejemplos = sum(1 for r in elegibles_fewshot
                       if (r.get("rag_examples_used") or 0) > 0)
    # Señal de diagnóstico: un caso NO agregado que terminó en la rama de
    # agregación nunca llegó al LLM (su hit@k es False por construcción) y su
    # puntuación no mide al modelo, sino al enrutador (spec §9: visible).
    rutados_a_agregacion = [r["id"] for r in puntuados
                            if r.get("categoria") != "aggregate"
                            and r.get("mode") == "aggregate"]
    grounding = {
        "rechazo_pct": round(100.0 * con_rechazo / len(puntuados), 1) if puntuados else 0.0,
        "inventados": sum(r.get("inventados") or 0 for r in puntuados),
    }
    fewshot = {
        "cobertura_pct": round(100.0 * con_ejemplos / len(elegibles_fewshot), 1)
        if elegibles_fewshot else 0.0,
        "fallos_con_0": sum(1 for r in elegibles_fewshot
                            if not r.get("ok") and not (r.get("rag_examples_used") or 0)),
        "fallos_con_1mas": sum(1 for r in elegibles_fewshot
                               if not r.get("ok") and (r.get("rag_examples_used") or 0) > 0),
    }
    # 7b: los 7 casos `report` se puntúan con el informe del POST único
    # contra el df real del análisis. Van DESPUÉS de los agregados para que
    # grounding/fewshot sigan midiendo solo a /ask: el informe no pasa por
    # el retriever ni lleva criterio few-shot (su latencia es la del POST,
    # compartida por los 7 casos). Un fallo de medida deja los 7 con
    # excepción: la corrida sale marcada, no publica una sección vacía.
    informe_meta, registros_informe = _meta_informe(
        informe, df_informe, informe_ms, informe_error)
    resultados.extend(registros_informe)
    return {"resultados": resultados, "grounding": grounding, "fewshot": fewshot,
            "rutados_a_agregacion": rutados_a_agregacion,
            "informe_meta": informe_meta}


def _probe_llm() -> None:
    """Con motor llm/ambos: el proveedor debe responder ANTES de puntuar nada."""
    import ai_config

    info = asyncio.run(ai_config.probe())
    if info.get("reachable") is not True:
        raise SystemExit(
            f"El proveedor no responde en {ai_config.get()['api_url'] or '(sin URL)'} "
            f"({info.get('reason')}): revisá la config en el panel o usa "
            f"--motor determinista"
        )


def run(motor: str = "llm", forzar: bool = False, tmp: Optional[Path] = None) -> dict:
    """Corrida completa del harness. `motor` ∈ {"llm", "determinista", "ambos"}.

    Devuelve el informe (o el par de informes, como lista, si es «ambos»).
    Aborta con SystemExit si el guard del motor detecta que el LLM no se usó
    (una puntuación del fallback presentada como del modelo es una mentira).
    """
    import tempfile

    import ai_config

    pasadas = ["determinista", "llm"] if motor == "ambos" else [motor]
    if "llm" in pasadas:
        _probe_llm()

    dataset_ids = set(load_fixture()["Log_ID"].astype(str))
    hash_muestra = _hash_fixture()
    cfg = ai_config.get()

    def _una(p: str, workdir: Path) -> dict:
        with _isolated_store(workdir):
            if p == "determinista":
                with LLM_OFF():
                    parcial = _run_pasada(dataset_ids)
            else:
                parcial = _run_pasada(dataset_ids)
        if p == "determinista" and not determinista_guard(parcial["resultados"]):
            raise SystemExit(
                "GUARD: con motor=determinista algún caso respondió con engine "
                "«llm» — el parche no alcanzó a la instancia que la app usa y la "
                "línea base mediría al modelo. Revisa las instancias cargadas."
            )
        if p == "llm" and not motor_guard(parcial["resultados"], forzar):
            raise SystemExit(
                f"GUARD: con motor=llm no se pudo verificar que el modelo se "
                f"usó — más del {MOTOR_GUARD_PCT:.0f}% de los casos de evidencia "
                "quedó en el determinista, o NINGUNO puntuó (corrida sin "
                "elegibles). Revisa el proveedor y los errores (--forzar para ignorar)."
            )
        return build_report(
            resultados=parcial["resultados"], grounding=parcial["grounding"],
            fewshot=parcial["fewshot"], motor=p, hash_muestra=hash_muestra,
            modelo=cfg["model"],
            temperatura=cfg["temperature"],
            rutados_a_agregacion=parcial["rutados_a_agregacion"],
            informe_meta=parcial.get("informe_meta"),
        )

    if tmp is not None:
        informes = [_una(p, tmp / p) for p in pasadas]
        return informes[0] if len(informes) == 1 else informes

    informes = []
    for p in pasadas:
        with tempfile.TemporaryDirectory() as td:
            informes.append(_una(p, Path(td)))
    return informes[0] if len(informes) == 1 else informes
