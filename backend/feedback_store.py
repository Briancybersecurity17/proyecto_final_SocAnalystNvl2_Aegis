"""
feedback_store.py
-----------------
Memoria de criterio del analista: la capa few-shot que **potencia el criterio
del LLM sin reentrenarlo**.

Idea (tomada del patrón de `sistema-delitos-argentina` y adaptada a SOC):
cada vez que el analista valida o corrige una respuesta del asistente, ese
par «consulta → respuesta correcta» se guarda como ejemplo. En las próximas
consultas se recuperan los ejemplos semánticamente afines y se inyectan en el
prompt ANTES de la evidencia, para que el modelo redacte con el estándar del
equipo y no con el suyo genérico.

Dos vías de captura (ambas asíncronas y nunca bloqueantes):

  1. IMPLÍCITA — `remember_implicit()`: el analista hizo otra pregunta sobre
     el mismo tema tras recibir una respuesta del LLM ⇒ esa respuesta fue
     útil ⇒ ejemplo positivo. Cero fricción para el usuario.
  2. EXPLÍCITA — `add_correction()`: el botón «Corregir» de la UI, donde el
     analista pega o escribe la respuesta correcta ⇒ ejemplo negativo (la
     respuesta del modelo NO era la buena) + la versión correcta. Es el dato
     más valioso porque trae el criterio humano literal.

Diseño de datos: los ejemplos se guardan en ChromaDB (búsqueda semántica,
reutilizando el cliente de `rag_store` y su modelo de embeddings) Y en un JSON
espejo (`data/feedback.json`) que es legible por humanos: se puede inspeccionar,
editar a mano o borrar sin tocar la base. El JSON es la fuente de verdad de
persistence; Chroma es el índice de recuperación derivado de él.

Rigor analítico: un ejemplo few-shot es **criterio de redacción**, no una
afirmación sobre los registros. Por eso viaja etiquetado y separado de la
evidencia en el prompt, igual que la referencia ATT&CK. Nunca se inyecta un
ejemplo cuya respuesta citara Log_ID que ya no existan.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # paquete (uvicorn backend.main:app) o módulo suelto (tests)
    from backend import rag_store
except ImportError:  # pragma: no cover
    import rag_store

logger = logging.getLogger(__name__)

BASE = Path(__file__).parent
FEEDBACK_PATH = BASE / "data" / "feedback.json"

FEEDBACK_COLLECTION = "soc_feedback"

# Presupuesto de ventana: con num_ctx=2048 la evidencia ya ocupa ~1200 tokens.
# 2 ejemplos × ~140 tokens dejan margen sin desbordar la ventana ni encarecer
# el prefill. Medido: pasar de 2 a 4 ejemplos empuja el prompt a ~2400 tokens
# y, peor, el modelo empieza a copiar el estilo del ejemplo en vez de redactar.
MAX_EXAMPLES = 2
# Tamaño máximo del texto de un ejemplo guardado (chars). Acota el JSON y el
# prompt a la vez.
EXAMPLE_CHARS = 700
MIN_QUESTION_CHARS = 8      # consultas más cortas no son un ejemplo útil
# Tras ~50 ejemplos los menos similares dejan de aportar: se recorta el JSON.
MAX_STORED = 50

# kind: "positive" = respuesta del LLM validada (implícita o explícita);
#       "negative" = respuesta del LLM corregida por el analista.
_KINDS = ("positive", "negative")

_lock = threading.RLock()   # reentrante: _upsert() llama a _load() dentro del bloqueo
_cache: Optional[List[Dict[str, Any]]] = None   # espejo del JSON en RAM


# ---------------------------------------------------------------------------
# Persistencia (JSON = fuente de verdad)
# ---------------------------------------------------------------------------
def _load() -> List[Dict[str, Any]]:
    """Ejemplos vigentes desde disco. Lista vacía si falta o está corrupto."""
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        try:
            raw = json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
            _cache = [e for e in raw if isinstance(e, dict) and e.get("question")]
        except FileNotFoundError:
            _cache = []
        except Exception as exc:  # noqa: BLE001 – JSON roto nunca tumba la app
            logger.warning("feedback.json ilegible (%s); empiezo vacío", exc)
            _cache = []
        return _cache


def _save(examples: List[Dict[str, Any]]) -> None:
    """Escribe el JSON espejo y refresca la caché en RAM."""
    global _cache
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEEDBACK_PATH.write_text(
        json.dumps(examples, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _cache = examples


def _id_for(question: str, kind: str) -> str:
    """Id estable por (consulta, tipo): repetir la misma corrección la
    actualiza en lugar de duplicarla."""
    key = re.sub(r"\s+", " ", (question or "").strip().lower())[:180]
    return f"fb-{kind}-{abs(hash(key)) % (10 ** 12)}"


def _clean_text(value: Any, limit: int = EXAMPLE_CHARS) -> str:
    """Colapsa espacios y acota a `limit` chars cortando en frontera de
    palabra o frase.

    Un ejemplo que muere a media palabra («…Event ID 514 (1») se enseña como
    patrón al modelo: si lo inyectamos así, aprende a truncar en seco. Por eso
    el corte busca el último punto, espacio o coma dentro del 60 % del tope.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(". "), cut.rfind(" "), cut.rfind(","))
    if boundary >= int(limit * 0.6):
        # +1 para conservar el punto si el corte cae sobre «. »; el rstrip
        # posterior se deshace de espacios y comas colgantes.
        cut = cut[:boundary + 1]
    return cut.rstrip(" ,;:-")


# ---------------------------------------------------------------------------
# Índice Chroma (derivado del JSON)
# ---------------------------------------------------------------------------
def _get_collection():
    """Colección de ejemplos, con el mismo cliente/EMBEDDINGS que el RAG."""
    client = rag_store.get_client()
    if client is None:
        return None
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

        ef = SentenceTransformerEmbeddingFunction(
            model_name=rag_store.EMBEDDING_MODEL,
            device="cpu",
            normalize_embeddings=True,
        )
        return client.get_or_create_collection(
            name=FEEDBACK_COLLECTION,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as exc:  # noqa: BLE001 – sin Chroma el few-shot se apaga
        logger.debug("feedback_store: sin índice semántico (%s)", exc)
        return None


def _reindex(examples: List[Dict[str, Any]]) -> None:
    """Sincroniza Chroma con el JSON: lo que sobra se borra, lo nuevo se upsertea."""
    col = _get_collection()
    if col is None:
        return
    try:
        wanted = [e["id"] for e in examples]
        existing = set(col.get(include=[])["ids"] or [])
        stale = sorted(existing - set(wanted))
        if stale:
            col.delete(ids=stale)
        if examples:
            col.upsert(
                ids=[e["id"] for e in examples],
                documents=[e["document"] for e in examples],
                metadatas=[
                    {"example_id": e["id"], "kind": e["kind"],
                     "question": _clean_text(e["question"], 300),
                     "created_at": e.get("created_at", 0)}
                    for e in examples
                ],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("feedback_store: no pude reindexar (%s)", exc)


def _document_for(question: str, answer: str) -> str:
    """Texto embebido: la consulta manda, la respuesta aporta el estilo."""
    return f"CONSULTA: {_clean_text(question, 300)}\nRESPUESTA: {_clean_text(answer)}"


# ---------------------------------------------------------------------------
# Captura de feedback
# ---------------------------------------------------------------------------
def _upsert(question: str, answer: str, kind: str,
            source: str, analysis_id: str = "") -> Optional[Dict[str, Any]]:
    """Inserta/actualiza un ejemplo y lo mantiene acotado. None si no hay nada
    que guardar (consulta demasiado corta o respuesta vacía)."""
    q = _clean_text(question, 300)
    a = _clean_text(answer)
    if len(q) < MIN_QUESTION_CHARS or not a:
        return None
    if kind not in _KINDS:
        return None

    example = {
        "id": _id_for(q, kind),
        "kind": kind,
        "question": q,
        "answer": a,
        "source": source,          # "implicit" | "explicit"
        "analysis_id": analysis_id,
        "created_at": int(time.time()),
    }
    example["document"] = _document_for(q, a)

    with _lock:
        examples = [e for e in _load() if e["id"] != example["id"]]
        examples.append(example)
        # Los negativos (correcciones humanas) pesan más: no se recortan hasta
        # el final, así una racha de consultas positivas no las borra.
        examples.sort(key=lambda e: (e["kind"] != "negative", -e.get("created_at", 0)))
        examples = examples[:MAX_STORED]
        _save(examples)

    _reindex(examples)
    logger.info("feedback: ejemplo %s guardado (%s) — %d en total",
                example["id"], kind, len(examples))
    return example


def add_correction(question: str, corrected_answer: str,
                   bad_answer: str = "", analysis_id: str = "") -> Optional[Dict[str, Any]]:
    """Corrección explícita del analista: enseña la respuesta BUENA.

    Guarda la versión corregida como negative (el modelo falló aquí) con la
    respuesta buena como referencia. Si además llega la respuesta mala, se
    archiva en `bad_answer` para poder auditar qué cometió el modelo.
    """
    if not _clean_text(corrected_answer):
        return None
    with _lock:
        # Al corregir, la respuesta previa deja de ser ejemplo positivo.
        # Se compara contra la pregunta YA acotada (que es como se guarda) para
        # que una consulta de más de 300 chars tampoco quede como positivo.
        target = _norm(_clean_text(question, 300))
        examples = [e for e in _load()
                    if not (e["kind"] == "positive"
                            and _norm(e["question"]) == target)]
        _save(examples)
    ex = _upsert(question, corrected_answer, "negative", "explicit", analysis_id)
    if ex is None or not _clean_text(bad_answer):
        return ex
    # Archiva la respuesta descartada en el MISMO dict que se devuelve: si no,
    # el llamador recibiría el ejemplo sin `bad_answer` aunque esté en disco.
    with _lock:
        updated = [({**e, "bad_answer": _clean_text(bad_answer)}
                    if e["id"] == ex["id"] else e) for e in _load()]
        _save(updated)
        return next((e for e in updated if e["id"] == ex["id"]), ex)


def remember_implicit(question: str, answer: str, analysis_id: str = "",
                      next_question: str = "") -> Optional[Dict[str, Any]]:
    """Señal implícita: el analista siguió preguntando tras una respuesta LLM.

    Si `next_question` comparte tercio con la consulta anterior, la respuesta
    se consideró útil ⇒ ejemplo positivo. Sin `next_question` no hay señal y
    no se guarda nada (no adivinamos).

    Dos salvaguardas contra auto-refuerzo (un ejemplo que se valida a sí mismo
    en bucle): la respuesta a validar no puede ser literalmente la de un
    ejemplo ya guardado, y la consulta no puede ser la propia respuesta
    (cubre el patrón «A → B → A», donde A y B se validan mutuamente).
    """
    if not next_question:
        return None
    if _overlap(question, next_question) < 0.34:
        return None     # el tema cambió: la respuesta no enganchó
    if _overlap(answer, next_question) >= 0.60:
        return None     # el «segundo turno» es la propia respuesta: bucle
    for ex in _load():
        if ex["kind"] == "positive" and _overlap(ex["answer"], answer) >= 0.90:
            return None     # ya es un ejemplo guardado: no se revalida
    return _upsert(question, answer, "positive", "implicit", analysis_id)


def _norm(text: str) -> str:
    return re.sub(r"[^\w ]", "", _clean_text(text).lower()).strip()


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[\wáéíóúñ#.-]+", _clean_text(text).lower())
            if len(t) > 3}


def _overlap(a: str, b: str) -> float:
    """Fracción de términos de `a` que reaparecen en `b` (0..1)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


# ---------------------------------------------------------------------------
# Recuperación para el prompt
# ---------------------------------------------------------------------------
def similar(question: str, limit: int = MAX_EXAMPLES) -> List[Dict[str, Any]]:
    """Ejemplos más afines a la consulta, negativos primero.

    Orden de preferencia: 1) correcciones humanas afines (el criterio del
    equipo, lo más valioso); 2) respuestas validadas afines. Los negativos
    van primero a propósito: un «NO hagas esto» bien orientado corrige el
    modelo más rápido que un «haz esto» genérico.
    """
    examples = _load()
    if not examples:
        return []

    col = _get_collection()
    if col is None:
        return _lexical(question, examples, limit)

    try:
        if col.count() == 0:
            return _lexical(question, examples, limit)
        res = col.query(
            query_texts=[rag_store.expand_query(question)[:2000]],
            n_results=min(col.count(), max(limit * 3, 6)),
            include=["metadatas", "distances"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("feedback_store: consulta fallida (%s); uso léxica", exc)
        return _lexical(question, examples, limit)

    by_id = {e["id"]: e for e in examples}
    hits: List[Dict[str, Any]] = []
    for meta, dist in zip((res.get("metadatas") or [[]])[0],
                          (res.get("distances") or [[]])[0]):
        ex_id = str((meta or {}).get("example_id") or "")
        example = by_id.get(ex_id)
        if example is None:
            continue   # índice desfasado con el JSON: se ignora el hit
        hits.append({**example, "score": round(max(0.0, 1.0 - float(dist)), 4)})

    return _pick(hits, limit)


def _lexical(question: str, examples: List[Dict[str, Any]],
             limit: int) -> List[Dict[str, Any]]:
    """Fallback sin embeddings: solape de términos (mismo umbral que el
    detector de señal implícita)."""
    hits = []
    for e in examples:
        score = _overlap(question, e["question"])
        if score >= 0.30:
            hits.append({**e, "score": round(score, 4)})
    return _pick(hits, limit)


def _pick(hits: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Deduplica por consulta y selecciona: negativos primero, luego score."""
    seen = set()
    uniq = []
    for h in sorted(hits, key=lambda e: (e["kind"] != "negative",
                                         -float(e.get("score", 0.0)))):
        key = _norm(h["question"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
        if len(uniq) >= limit:
            break
    return uniq


def prompt_block(question: str, limit: int = MAX_EXAMPLES) -> str:
    """Bloque few-shot listo para inyectar en el prompt del LLM.

    Vacío si no hay ejemplos: la app funciona igual que antes (cero regresión).
    Los ejemplos van etiquetados como criterio del analista, separados de la
    evidencia, para que el modelo no los confunda con un hallazgo.

    Los DESCARTADOS y los que FUNCIONARON van en secciones propias (negativos
    primero): agrupados son más legibles para el modelo que una lista
    intercalada, y reflejan la distinción que ya hace el almacén. Cada ejemplo
    empieza por «- Consulta «» — ai_service._feedback_note cuenta ese prefijo
    para informar de cuántos ejemplos se inyectaron (rag_examples_used).
    """
    examples = similar(question, limit)
    if not examples:
        return ""

    lines = [
        "CRITERIO DEL ANALISTA SOBRE CONSULTAS SIMILARES (material de referencia "
        "para redactar, NO un hallazgo de los registros):",
    ]
    negatives = [ex for ex in examples if ex["kind"] == "negative"]
    positives = [ex for ex in examples if ex["kind"] == "positive"]
    if negatives:
        lines.append("— Lo que el analista DESCARTÓ (no repitas este criterio):")
        for ex in negatives:
            lines.append(
                f"- Consulta «{ex['question']}» → el analista DESCARTÓ esta "
                f"respuesta y la corrigió así: {ex['answer']}"
            )
    if positives:
        lines.append("— Lo que al analista LE FUNCIONÓ:")
        for ex in positives:
            lines.append(
                f"- Consulta «{ex['question']}» → respuesta que funcionó en esta "
                f"conversación: {ex['answer']}"
            )
    lines.append(
        "Imita el criterio y el estilo de estas respuestas (extensión, qué se "
        "cita y cómo se cierra), sin copiar sus datos ni sus Log_ID."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# API para la UI / diagnóstico
# ---------------------------------------------------------------------------
def status() -> Dict[str, Any]:
    """Conteo por tipo para el panel de Opciones IA. Nunca lanza."""
    examples = _load()
    positives = sum(1 for e in examples if e["kind"] == "positive")
    return {
        "available": True,
        "total": len(examples),
        "positive": positives,
        "negative": len(examples) - positives,
        "collection": FEEDBACK_COLLECTION,
        "indexed": _get_collection() is not None,
        "path": str(FEEDBACK_PATH),
    }


def clear() -> int:
    """Borra todos los ejemplos (y su índice). Devuelve cuántos había."""
    global _cache
    with _lock:
        count = len(_cache or [])
        _save([])
    col = _get_collection()
    if col is not None:
        try:
            col.delete(where={})
        except Exception:  # noqa: BLE001 – borrar todo es best-effort
            pass
    logger.info("feedback: %d ejemplos borrados", count)
    return count


def warmup() -> bool:
    """Precarga el JSON y el índice en el arranque del backend."""
    try:
        _load()
        _reindex(_load())
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("feedback_store: precarga fallida (%s)", exc)
        return False
