"""
kb_store.py
-----------
Base de conocimiento del asistente: **qué significan los eventos** y **qué
técnicas del adversario reflejan**, para poder explicar un hallazgo y no sólo
enumerarlo.

Dos fuentes, ambas locales y gratuitas (sin API keys):

  1. `knowledge/mitre_attack.json` — técnicas MITRE ATT&CK generadas por
     `scripts/build_kb.py` desde el bundle STIX oficial (github.com/mitre/cti).
     Cada entrada conserva la descripción oficial EN de MITRE + una guía de
     detección en español propia del proyecto.
  2. `knowledge/event_ids.json` — glosario curado de Event IDs de Windows
     (Security/System/PowerShell) con su significado y a qué técnicas se asocian.

Rigor analítico: la base de conocimiento es **material de referencia del marco**,
nunca una afirmación sobre los registros importados. Quien la consume (API y UI)
la presenta aparte, con esa etiqueta, para que no se confunda con una evidencia.

INDEXACIÓN: los documentos se indexan en una colección propia (`soc_kb`) del
mismo cliente ChromaDB que usa `rag_store`, con el mismo modelo de embeddings.
Si no hay Chroma, `search()` degrada a búsqueda léxica sobre los propios JSON:
la referencia sigue disponible aunque faltén las dependencias opcionales.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # paquete (uvicorn backend.main:app) o módulo suelto (test_kb.py)
    from backend import rag_store
except ImportError:  # pragma: no cover
    import rag_store

logger = logging.getLogger(__name__)

BASE = Path(__file__).parent
TECHNIQUES_PATH = BASE / "knowledge" / "mitre_attack.json"
EVENT_IDS_PATH = BASE / "knowledge" / "event_ids.json"

KB_COLLECTION = "soc_kb"
MAX_EVENT_ENTRIES = 4
MAX_TECHNIQUES = 4
KB_MIN_SCORE = 0.30          # similitud mínima para proponer una técnica
SOURCE_NOTE = (
    "Referencia de marco (MITRE ATT&CK y glosario de Event IDs de Windows): "
    "explica significados y técnicas, no afirma nada sobre tus registros."
)

# ---------------------------------------------------------------------------
# Carga de los JSON (una sola vez)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_techniques: Optional[List[Dict[str, Any]]] = None
_event_ids: Optional[Dict[str, Dict[str, Any]]] = None
_indexed = False


def _load() -> None:
    global _techniques, _event_ids
    if _techniques is not None and _event_ids is not None:
        return
    with _lock:
        if _techniques is not None and _event_ids is not None:
            return
        try:
            _techniques = json.loads(TECHNIQUES_PATH.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo cargar %s: %s", TECHNIQUES_PATH.name, exc)
            _techniques = []
        try:
            raw = json.loads(EVENT_IDS_PATH.read_text(encoding="utf-8"))
            _event_ids = {str(item.get("id")): item for item in raw}
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo cargar %s: %s", EVENT_IDS_PATH.name, exc)
            _event_ids = {}


def techniques() -> List[Dict[str, Any]]:
    _load()
    return _techniques or []


def event_ids() -> Dict[str, Dict[str, Any]]:
    _load()
    return _event_ids or {}


# ---------------------------------------------------------------------------
# Documentos indexados
# ---------------------------------------------------------------------------
def _tech_text(t: Dict[str, Any]) -> str:
    eventos = ", ".join(t.get("eventos") or [])
    aliases = ", ".join(t.get("aliases") or [])
    parts = [
        f"[TÉCNICA] {t.get('id')} {t.get('name')}",
        f"Táctica: {t.get('tactic_es')} ({t.get('tactic')})",
        f"Qué es: {t.get('descripcion_es')}",
        f"Cómo se detecta: {t.get('se_detecta')}",
    ]
    if eventos:
        parts.append(f"Event IDs relacionados: {eventos}")
    if aliases:
        parts.append(f"También conocida como: {aliases}")
    return " | ".join(parts)


def _event_text(e: Dict[str, Any]) -> str:
    relacionados = ", ".join(e.get("relacionados") or [])
    parts = [
        f"[EVENTO] Event ID {e.get('id')} — {e.get('titulo')}",
        f"Qué registra: {e.get('descripcion')}",
        f"Por qué importa: {e.get('por_que_importa')}",
    ]
    if e.get("logon_types"):
        parts.append(f"LogonTypes: {e['logon_types']}")
    if relacionados:
        parts.append(f"Técnicas MITRE relacionadas: {relacionados}")
    return " | ".join(parts)


def _all_docs() -> List[Dict[str, Any]]:
    """Documentos de la KB: id estable, texto y metadata filtrable."""
    docs: List[Dict[str, Any]] = []
    for t in techniques():
        docs.append({
            "id": t["id"],
            "document": _tech_text(t),
            "metadata": {"kind": "technique", "identifier": t["id"],
                         "name": t.get("name", ""), "tactic": t.get("tactic_es", "")},
        })
    for eid, e in event_ids().items():
        docs.append({
            "id": f"EV:{eid}",
            "document": _event_text(e),
            "metadata": {"kind": "event_id", "identifier": eid, "name": e.get("titulo", "")},
        })
    return docs


# ---------------------------------------------------------------------------
# Colección Chroma (compartida con rag_store) + degradación
# ---------------------------------------------------------------------------
def _get_kb_collection():
    global _indexed
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
        col = client.get_or_create_collection(
            name=KB_COLLECTION,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
        docs = _all_docs()
        if col.count() != len(docs):   # la KB cambió (rebuild) → reindexa
            col.upsert(
                ids=[d["id"] for d in docs],
                documents=[d["document"] for d in docs],
                metadatas=[d["metadata"] for d in docs],
            )
            logger.info("KB indexada: %d entradas en «%s»", len(docs), KB_COLLECTION)
        _indexed = True
        return col
    except Exception as exc:  # noqa: BLE001 – la KB nunca debe tumbar la app
        logger.warning("Base de conocimiento no indexada (%s); uso búsqueda léxica", exc)
        return None


def is_available() -> bool:
    return _get_kb_collection() is not None


def status() -> Dict[str, Any]:
    """Estado de la KB para la UI (nunca lanza)."""
    _load()
    info: Dict[str, Any] = {
        "available": False,
        "reason": None,
        "techniques": len(techniques()),
        "event_ids": len(event_ids()),
        "source": "MITRE ATT&CK (STIX oficial) + glosario Event IDs Windows",
        "indexed": False,
    }
    col = _get_kb_collection()
    if col is None:
        info["reason"] = "sin chromadb: búsqueda léxica sobre los JSON"
        return info
    try:
        info["available"] = True
        info["indexed"] = int(col.count()) > 0
        info["documents"] = int(col.count())
    except Exception as exc:  # noqa: BLE001
        info["reason"] = str(exc)
    return info


def warmup() -> bool:
    """Precarga e indexa la KB en segundo plano (arranque del backend)."""
    try:
        return _get_kb_collection() is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Precarga de la KB fallida (se reintentará): %s", exc)
        return False


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> List[str]:
    return [t for t in re.findall(r"[\w.:#-]+", (text or "").lower()) if len(t) > 2]


def _lexical_search(query: str, kind: str, limit: int) -> List[Dict[str, Any]]:
    """Búsqueda por coincidencia de términos (fallback sin embeddings)."""
    docs = [d for d in _all_docs() if d["metadata"]["kind"] == kind]
    tokens = set(_tokenize(rag_store.expand_query(query)))
    if not tokens:
        return []
    scored = []
    for doc in docs:
        text_low = doc["document"].lower()
        score = sum(1 for tok in tokens if tok in text_low) / len(tokens)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda item: -item[0])
    return [{"score": round(score, 4), "id": doc["id"],
             "metadata": doc["metadata"], "document": doc["document"],
             "retrieval": "lexica"}
            for score, doc in scored[:limit]]


def search(query: str, kind: str = "technique", limit: int = 4) -> List[Dict[str, Any]]:
    """Busca en la KB por similitud semántica (o léxica si no hay embeddings)."""
    query = (query or "").strip()
    if not query:
        return []
    col = _get_kb_collection()
    if col is None:
        return _lexical_search(query, kind, limit)

    try:
        res = col.query(
            query_texts=[f"[{kind}] {query}"[:2000]],
            n_results=max(1, limit * 4),   # se filtra por kind después
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Consulta KB fallida (%s); uso léxica", exc)
        return _lexical_search(query, kind, limit)

    hits: List[Dict[str, Any]] = []
    for doc, meta, dist in zip(
        (res.get("documents") or [[]])[0],
        (res.get("metadatas") or [[]])[0],
        (res.get("distances") or [[]])[0],
    ):
        meta = dict(meta or {})
        if meta.get("kind") != kind:
            continue
        hits.append({
            "score": round(max(0.0, 1.0 - float(dist)), 4),
            "id": meta.get("identifier"),
            "metadata": meta,
            "document": doc,
            "retrieval": "semantica",
        })
        if len(hits) >= limit:
            break
    if not hits:
        # Sin coincidencias semánticas con suficiente filo → búsqueda léxica
        return _lexical_search(query, kind, limit)
    return hits


def lookup_event_ids(ids: List[str], limit: int = MAX_EVENT_ENTRIES) -> List[Dict[str, Any]]:
    """Entradas EXACTAS del glosario para los Event IDs observados en la evidencia."""
    _load()
    table = event_ids()
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in ids:
        key = str(raw or "").strip()
        if not key or key in seen or key in ("—", "None", "nan"):
            continue
        entry = table.get(key)
        if entry is None:
            continue
        seen.add(key)
        out.append(dict(entry))
        if len(out) >= limit:
            break
    return out


def _techniques_by_ids(ids: List[str]) -> List[Dict[str, Any]]:
    """Técnicas por ID exacto (incluye aliases: «T1562.004» → T1686)."""
    _load()
    table: Dict[str, Dict[str, Any]] = {}
    for t in techniques():
        table[t["id"]] = t
        for alias in t.get("aliases") or []:
            table[alias] = t
    out: List[Dict[str, Any]] = []
    for raw in ids:
        t = table.get(str(raw or "").strip())
        if t and all(t["id"] != existing["id"] for existing in out):
            out.append(t)
    return out


def _ids_in_text(text: str) -> List[str]:
    """Event IDs del glosario mencionados literalmente en un texto.

    Cubre los numéricos («4625») y los de fuente no numérica («HTTP_ACCESS»),
    siempre que existan en el glosario: es una coincidencia exacta, no una
    inferencia.
    """
    _load()
    table = event_ids()
    low = (text or "").lower()
    found: List[str] = []
    for key in table:
        if key.isdigit():
            if re.search(rf"(?<!\d){re.escape(key)}(?!\d)", text or ""):
                found.append(key)
        elif key.lower() in low:
            found.append(key)
    return found


def build_reference(question: str, evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Referencia técnica para responder «qué significa» sin inventar hallazgos.

    Tres vías complementarias (fusionadas y deduplicadas):
      1. Determinista sobre la EVIDENCIA: los Event_ID recuperados → su
         significado en el glosario → técnicas asociadas a esos eventos.
      2. Determinista sobre la CONSULTA: IDs citados literalmente por el
         analista («¿qué es el 1102?») → su entrada del glosario.
      3. Semántica: lo que la consulta parece preguntar → técnicas similares
         (solo si las vías 1–2 no aportaron ninguna, para no mezclar ruido).

    Las vías 1 y 2 son coincidencias exactas sobre datos propios de la KB; la 3
    solo aporta contexto de marco y va etiquetada como `match: consulta`.
    """
    _load()

    observed: List[str] = []
    for item in evidence:
        ev = str(item.get("event_id") or "").strip()
        if ev and ev not in observed:
            observed.append(ev)

    event_entries: List[Dict[str, Any]] = []
    for entry in lookup_event_ids(observed):
        event_entries.append({**entry, "match": "evidencia"})

    # IDs que el analista citó en la propia pregunta (coincidencia exacta)
    for key in _ids_in_text(question):
        if all(existing.get("id") != key for existing in event_entries):
            entry = event_ids().get(key)
            if entry:
                event_entries.append({**entry, "match": "consulta"})
        if len(event_entries) >= MAX_EVENT_ENTRIES:
            break
    event_entries = event_entries[:MAX_EVENT_ENTRIES]

    tech_ids: List[str] = []
    for entry in event_entries:
        for tid in entry.get("relacionados") or []:
            if tid not in tech_ids:
                tech_ids.append(tid)
    matched_by_events = {t["id"] for t in _techniques_by_ids(tech_ids)}

    techniques_out: List[Dict[str, Any]] = []
    for t in _techniques_by_ids(tech_ids)[:MAX_TECHNIQUES]:
        techniques_out.append({**_tech_view(t), "match": "eventos"})

    # Vía semántica sobre la consulta: solo cuando la evidencia NO aportó
    # técnicas. Si los eventos ya enlazan con técnicas (p. ej. 4625 → T1110),
    # la similitud coseno sobre la pregunta puede colar técnicas ajenas
    # (ruido de umbral bajo) y la referencia dejaría de estar anclada a los datos.
    semantic_hits = [] if techniques_out else search(
        question, kind="technique", limit=MAX_TECHNIQUES
    )
    for hit in semantic_hits:
        if float(hit["score"]) < KB_MIN_SCORE:
            continue
        t = next((x for x in techniques() if x["id"] == hit["id"]), None)
        if t is None or t["id"] in matched_by_events:
            continue
        matched_by_events.add(t["id"])
        techniques_out.append({**_tech_view(t), "match": "consulta", "score": hit["score"]})
        if len(techniques_out) >= MAX_TECHNIQUES:
            break

    return {
        "available": bool(event_entries or techniques_out),
        "note": SOURCE_NOTE,
        "event_ids": event_entries,
        "techniques": techniques_out,
        "observed_event_ids": observed[:MAX_EVENT_ENTRIES],
    }


def _tech_view(t: Dict[str, Any]) -> Dict[str, Any]:
    """Vista reducida y segura de una técnica para la API/UI."""
    return {
        "id": t["id"],
        "name": t.get("name"),
        "tactic": t.get("tactic"),
        "tactic_es": t.get("tactic_es"),
        "url": t.get("url"),
        "descripcion_es": t.get("descripcion_es"),
        "se_detecta": t.get("se_detecta"),
        "eventos": t.get("eventos") or [],
        "aliases": t.get("aliases") or [],
        "description_en": (t.get("description") or "")[:240],
    }
