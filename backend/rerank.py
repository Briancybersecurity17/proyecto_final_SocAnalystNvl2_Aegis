"""Reordenamiento de la evidencia recuperada con un cross-encoder.

La recuperación híbrida (similitud coseno + coincidencia léxica) es barata
pero grosera: mide por separado lo que se parecen la consulta y el registro.
Un cross-encoder los lee **a la vez** («consulta ‖ registro») y devuelve una
puntuación de relevancia mucho más fina, que es justo lo que hace falta
cuando la pregunta es vaga («¿qué pasó en DC-01?») y varios registros empatan
en score coseno: el primer puesto se lo lleva el más relevante de verdad y no
el que el embeddingsresult coincidiera mejor.

Decisiones de diseño:

- **`score` no se toca.** Sigue siendo la similitud coseno fusionada en
  [0, 1], que es la que interpretan el umbral ``QA_MIN_SCORE`` y la respuesta
  determinista. El logit del cross-encoder (escala arbitraria: negativa para
  lo irrelevante, positiva para lo relevante) se guarda aparte en
  `rerank_score`, solo para auditar por qué subió un registro.
- **CPU siempre.** El GTX 1060 es de 6 GB y comparte la GPU con el resto del
  trabajo del escritorio: cargarle otro modelo solo empujaría al sistema a
  quedarse sin VRAM (el cross-encoder vive en CPU, cuesta ~1 s y se precarga).
- **Carga perezosa**, nunca en el import: el modelo (~90 MB) no interesa si
  nadie pregunta. `warmup()` existe para precargarlo en un hilo al arrancar.
- **Degradación elegante.** Sin `sentence_transformers`, sin el modelo en
  caché o con cualquier error, se devuelve la lista de entrada intacta y el
  módulo queda marcado como no disponible para no reintentar en cada consulta.
- **Siempre fuera del event loop**: quien llama envuelve la llamada en
  `asyncio.to_thread` (unos 0,8–1,2 s por consulta de 12 pares en este CPU).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L6-v2"

# 256 deja cada par en ~190 ms aquí (Athlon 3000G, 2 hilos): con 160 el token
# de consulta (≈20) más ~550 caracteres de registro entran sobrados, que es
# donde está toda la información útil (Log_ID → Severity).
MAX_SEQ_LENGTH = 160
DOC_CHARS = 420       # el registro se corta por donde sigue siendo legible
POOL_MIN = 12         # candidatos mínimos que merece la pena reordenar
POOL_MAX = 20         # tope duro: el coste crece lineal con los pares
MIN_INPUTS = 3        # por debajo, reordenar no cambia nada y solo cuesta

_lock = threading.Lock()
_model: Any = None
_available = True    # se apaga tras el primer fallo (no reintentar sin parar)


def _doc_text(hit: Dict[str, Any]) -> str:
    """Texto del registro que ve el cross-encoder (truncado por donde importa)."""
    text = str(hit.get("document") or "")
    if not text:
        meta = hit.get("metadata") or {}
        text = " | ".join(f"{k}: {v}" for k, v in meta.items() if v not in (None, ""))
    return text[:DOC_CHARS]


def _get_model() -> Any:
    """Carga perezosa del cross-encoder (una sola vez, entre hilos)."""
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is None:
            from sentence_transformers import CrossEncoder  # importa tarde: pesa

            _model = CrossEncoder(MODEL_NAME, max_length=MAX_SEQ_LENGTH)
            logger.info("Reranker listo: %s", MODEL_NAME)
    return _model


def warmup() -> bool:
    """Precarga el cross-encoder en segundo plano (arranque del backend).

    No bloquea nunca: si falla, `available` queda en False y la recuperación
    sigue funcionando sin reordenar.
    """
    global _available
    if not _available:
        return False
    try:
        _get_model()
        return True
    except Exception as exc:  # noqa: BLE001 – degradar, no tumbar el arranque
        _available = False
        logger.warning("Reranker no disponible (se usará el orden original): %s", exc)
        return False


def available() -> bool:
    """True si el cross-encoder está cargado y operativo."""
    return _available and _model is not None


def apply(question: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reordena `hits` por relevancia medida con el cross-encoder.

    Devuelve SIEMPRE una lista utilizable: si no hay reranker o falla, la
    misma entrada en el mismo orden. Nunca modifica `score` (sigue siendo la
    similitud coseno fusionada) y añade `rerank_score` con el logit crudo.
    """
    global _available
    if not hits or len(hits) < MIN_INPUTS or not _available:
        return hits
    try:
        model = _get_model()
        scores = model.predict(
            [(question, _doc_text(hit)) for hit in hits],
            show_progress_bar=False,
            batch_size=64,
        )
    except Exception as exc:  # noqa: BLE001 – un solo reintento de vida
        _available = False
        logger.warning("Rerank desactivado tras un error (%s); orden original", exc)
        return hits

    ranked: List[Dict[str, Any]] = []
    for hit, raw in zip(hits, scores):
        item = dict(hit)
        item["rerank_score"] = round(float(raw), 4)
        ranked.append(item)
    ranked.sort(key=lambda item: -item["rerank_score"])
    return ranked
