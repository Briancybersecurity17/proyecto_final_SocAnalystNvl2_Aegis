"""Red de regresión: el event loop no puede bloquearse esperando a Chroma.

Motivo real (1-nov): mientras el índice se reconstruía (~2.5 min embebiendo
los 715 eventos del Excel del analista), Chroma retenía su candado y los
handlers que le llamaban síncronamente dentro del bucle congelaban TODA la
API — hasta peticiones sin relación con RAG (GET /api/analyses) caducaban en
el cliente. La app no se caía: el loop esperaba al candado. Py-spy lo mostró
en el proceso congelado.

Cada caso simula ese candado (la función objetivo espera a un
`threading.Event` antes de responder) y mide si el bucle sigue latiendo
mientras la petición lo espera:

- Bucle libre: la petición en curso duerme en un hilo → `sleep(0.15)` del
  escenario dura ~0.15 s → verde.
- Bucle congelado (bug): el candado tarda 5 s en caducar y el `sleep` solo
  se despierta después → latencia ~5 s → rojo.

El umbral 2 s separa clarísimamente ambos mundos.
"""
import asyncio
import importlib
import threading
import time

import httpx
import pytest

from main import app

# Pregunta de agregación: responde con cifras del resumen en memoria, sin
# recuperación ni LLM (camino cubierto por test_endpoints), así los tests no
# dependen de red ni de la configuración real del proveedor.
_Q_AGGREGADA = {"question": "¿Cuántos eventos hay en total?"}


def _medir_bloqueo(monkeypatch, modulo, atributo, peticion):
    """Ejecuta `peticion` con `modulo.atributo` bloqueado y devuelve
    (latencia_del_bucle, respuesta).

    `latencia` es lo que tardó el bucle en reanudar tras lanzar la petición:
    si la llamada se ejecuta dentro del bucle, esa espera es igual a la del
    candado (5 s); si va a un hilo, es la del `sleep` propio (0.15 s).
    """
    original = getattr(modulo, atributo)
    gate = threading.Event()
    llamado = threading.Event()

    def bloqueado(*args, **kwargs):
        llamado.set()
        gate.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(modulo, atributo, bloqueado)

    async def escenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            tarea = asyncio.create_task(peticion(ac))
            t0 = time.monotonic()
            await asyncio.sleep(0.15)
            latencia = time.monotonic() - t0
            gate.set()
            res = await tarea
            return latencia, res

    try:
        latencia, res = asyncio.run(escenario())
    finally:
        gate.set()

    assert llamado.is_set(), (
        f"{modulo.__name__}.{atributo} no llegó a llamarse: el test ya no "
        "protege ese camino"
    )
    return latencia, res


def _afirma_bucle_libre(latencia: float) -> None:
    assert latencia < 2, (
        f"el event loop estuvo {latencia:.1f}s bloqueado esperando al candado "
        "de Chroma: la llamada se ejecuta dentro del bucle en vez de en un hilo"
    )


@pytest.mark.parametrize(
    "modulo_nombre, atributo",
    [
        ("rag_store", "get_stats"),      # common["rag"] de _ask_prep
        ("kb_store", "status"),          # common["kb"] de _ask_prep
        ("rag_store", "is_available"),   # _ensure_semantic_index
        ("rag_store", "has_index"),      # _ensure_semantic_index
    ],
)
def test_ask_no_congela_el_loop_por_llamadas_a_chroma(
    monkeypatch, analysis_id, rag_aislado, tmp_path, modulo_nombre, atributo
):
    """Cada llamada a Chroma dentro de _ask_prep corre fuera del bucle."""
    from main import ai_config

    # Config aislada en tmp: el LLM se apaga (sin red) sin tocar el
    # ai_config.json real ni la app viva, y se restaura el valor previo.
    previo = bool(ai_config.get().get("enabled", False))
    monkeypatch.setattr(ai_config, "CONFIG_PATH", tmp_path / "ai_config.json")
    try:
        ai_config.update({"enabled": False})
        modulo = importlib.import_module(modulo_nombre)
        latencia, res = _medir_bloqueo(
            monkeypatch,
            modulo,
            atributo,
            lambda ac: ac.post(
                f"/api/analysis/{analysis_id}/ask", json=_Q_AGGREGADA
            ),
        )
        _afirma_bucle_libre(latencia)
        assert res.status_code == 200, res.text
    finally:
        ai_config.update({"enabled": previo})


def test_rag_status_no_congela_el_loop(monkeypatch, rag_aislado):
    """GET /api/rag/status llama a get_stats: mismo candado, mismo riesgo."""
    import rag_store

    latencia, res = _medir_bloqueo(
        monkeypatch, rag_store, "get_stats", lambda ac: ac.get("/api/rag/status")
    )
    _afirma_bucle_libre(latencia)
    assert res.status_code == 200, res.text


def test_feedback_status_no_congela_el_loop(monkeypatch, rag_aislado, tmp_path):
    """GET /api/feedback construye su respuesta con feedback_store.status()."""
    import feedback_store

    # Lectura aislada: si el test se corre en solitario no debe depender del
    # feedback.json real del analista.
    monkeypatch.setattr(feedback_store, "FEEDBACK_PATH", tmp_path / "fb.json")
    monkeypatch.setattr(feedback_store, "_cache", None)

    latencia, res = _medir_bloqueo(
        monkeypatch, feedback_store, "status", lambda ac: ac.get("/api/feedback")
    )
    _afirma_bucle_libre(latencia)
    assert res.status_code == 200, res.text


def test_feedback_clear_no_congela_el_loop(monkeypatch, rag_aislado, tmp_path):
    """DELETE /api/feedback borra el índice con clear(): también es Chroma."""
    import feedback_store

    # Aislamiento doble: JSON en tmp (clear escribe) y colección en el
    # Chroma temporal que ya redirige rag_aislado.
    monkeypatch.setattr(feedback_store, "FEEDBACK_PATH", tmp_path / "fb.json")
    monkeypatch.setattr(feedback_store, "_cache", None)

    latencia, res = _medir_bloqueo(
        monkeypatch, feedback_store, "clear", lambda ac: ac.delete("/api/feedback")
    )
    _afirma_bucle_libre(latencia)
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
