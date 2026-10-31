"""
rag_store.py
------------
Índice semántico local (RAG) sobre los eventos del SIEM importados desde Excel.

Motor elegido: **local semántico** (ChromaDB + sentence-transformers multilingüe).
- 100 % local y gratuito: sin API keys, sin envío de datos a terceros.
- Modelo `paraphrase-multilingual-MiniLM-L12-v2` → traduce entre las preguntas en
  español del analista y las descripciones en inglés de los logs.
- Persistente en `backend/data/rag/` (sobrevive a reinicios del backend).

Rigor analítico: cada documento indexado contiene SOLO lo que ya está en el
registro (campos del Excel) más la justificación determinista `row_reason`
calculada en `main.py` a partir de severidad + keywords + Event_ID. No se
inventa ningún contenido, así que lo que el RAG recupere es siempre trazable.

DISEÑO DEGRADABLE (patrón heredado de sistema-delitos-argentina/rag_store.py):
si `chromadb` o `sentence-transformers` no están instalados o fallan, todas las
funciones devuelven `available=False` y la aplicación sigue funcionando con el
motor determinista actual. Nunca rompe la app ni la demo.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Datos RAG persistidos en backend/data/rag/
RAG_DATA_PATH = Path(__file__).parent / "data" / "rag"

# Colección única; cada evento lleva `analysis_id` en metadata para filtrar.
COLLECTION_NAME = "soc_events"

# Modelo multilingüe (ES↔EN). Ya presente en la caché local de HF.
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# Orden de los campos del Excel dentro del documento (si existen en la fila).
# Process_CommandLine va después de Event_Description a propósito: es la
# prueba más directa de QUÉ ejecutó un proceso (powershell.exe -enc …) y sin
# ella el asistente respondía «no encontré procesos PowerShell» teniendo el
# evento 4688/4104 delante (revisión funcional #4).
CONTEXT_FIELDS = [
    "Log_ID", "Timestamp", "Hostname", "Event_ID", "User", "Source_IP",
    "Destination", "Log_Source", "Result", "Severity", "Event_Description",
    "Process_CommandLine",
]

# Claves enriquecidas que calcula main.py antes de indexar.
LEVEL_KEY = "_level"
REASON_KEY = "_reason"
SUSPICIOUS_KEY = "_suspicious"

# Límite de documentos por consulta (acotado a propósito: evidencia, no dump).
MAX_RESULTS = 20

# ---------------------------------------------------------------------------
# Expansión bilingüe de la consulta (ES -> términos técnicos EN)
# ---------------------------------------------------------------------------
# Los logs del SIEM están en inglés ("An account failed to log on") mientras
# que el analista pregunta en español ("¿hubo fuerza bruta?"). Sin esto, la
# recuperación semántica conecta mal porque el modelo ve dos vocabularios
# distintos. Ampliamos SOLO la consulta (nunca los datos): se añaden sinónimos
# técnicos, sin afirmar nada sobre los registros.
QUERY_LEXICON: List[tuple[str, str]] = [
    ("fuerza bruta", "brute force failed logon attempts"),
    ("brute force", "brute force failed logon attempts"),
    ("login fallido", "failed logon"),
    ("logín fallido", "failed logon"),
    ("acceso fallido", "failed logon"),
    ("autenticacion fallida", "failed logon authentication failure"),
    ("autenticación fallida", "failed logon authentication failure"),
    ("autenticaciones fallidas", "failed logon authentication failure"),
    ("credencial", "credentials password logon"),
    ("contraseña", "password logon"),
    ("password", "password bad password"),
    ("cuenta bloqueada", "account locked out lockout"),
    ("cuentas bloqueadas", "account locked out lockout"),
    ("bloqueo", "account locked out lockout"),
    ("bloqueada", "account locked out lockout"),
    ("lockout", "account locked out lockout"),
    ("malware", "malware malicious code"),
    ("virus", "malware malicious code"),
    ("ransomware", "ransomware ransom malicious"),
    ("backdoor", "backdoor new service installed"),
    ("troyano", "trojan malware"),
    ("sospechoso", "suspicious alert"),
    ("sospechosa", "suspicious alert"),
    ("sospechosos", "suspicious alert"),
    ("sospechas", "suspicious alert"),
    ("inusual", "unusual anomaly"),
    ("anomalo", "anomalous anomaly"),
    ("anómalo", "anomalous anomaly"),
    ("acceso denegado", "access denied"),
    ("denegado", "access denied"),
    ("no autorizado", "unauthorized access"),
    ("no autorizada", "unauthorized access"),
    ("intrusion", "intrusion intrusion detection"),
    ("intrusión", "intrusion intrusion detection"),
    ("escaneo", "scan reconnaissance"),
    ("reconocimiento", "reconnaissance scan"),
    ("servicio nuevo", "new service installed"),
    ("instalacion de servicio", "new service installed"),
    ("instalación de servicio", "new service installed"),
    ("proceso nuevo", "new process created"),
    ("proceso creado", "new process created"),
    # PowerShell: el archivo real trae 4688 «powershell.exe» y 4104 «Script
    # Block Logging»; sin esta puente la pregunta en español recuperaba mal
    # los eventos de ejecución de scripts (revisión funcional #4).
    ("powershell", "powershell script block powershell.exe"),
    ("script block", "powershell script block logging"),
    ("bloque de script", "powershell script block logging"),
    ("codificado", "powershell -enc encoded command"),
    ("encoded", "powershell -enc encoded command"),
    ("privilegios", "special privileges assigned"),
    ("permisos especiales", "special privileges assigned"),
    ("cuenta nueva", "new account created"),
    ("creacion de cuenta", "new account created"),
    ("creación de cuenta", "new account created"),
    ("grupo", "member added to group"),
    ("limpieza de logs", "audit log cleared"),
    ("registro reiniciado", "audit log cleared"),
    ("event id 1102", "audit log cleared 1102"),
    ("exfiltracion", "data exfiltration"),
    ("exfiltración", "data exfiltration"),
    ("fuga de datos", "data exfiltration"),
    ("compromiso", "compromised unauthorized access"),
    ("comprometido", "compromised unauthorized access"),
    ("rdp", "LogonType=10 RDP"),
    ("vpn", "VPN connection"),
    ("proxy", "proxy web access"),
    ("administrador", "administrator admin privileges"),
    ("servicio de dominio", "domain controller"),
    ("controlador de dominio", "domain controller"),
    ("dominio", "domain controller"),
    # Criticidad: los registros usan Severity en inglés, la consulta en español
    ("critico", "critical severity"),
    ("critica", "critical severity"),
    ("criticos", "critical severity critical events"),
    ("criticas", "critical severity critical events"),
    ("eventos criticos", "critical severity critical events"),
    ("nivel critico", "critical severity critical events"),
    ("nivel alto", "high severity high events"),
    ("nivel medio", "medium severity medium events"),
    ("nivel bajo", "low severity low events"),
]


def expand_query(question: str) -> str:
    """Añade equivalentes técnicos en inglés a una pregunta en español.

    Devuelve la pregunta original ampliada. Si no hay sinónimos que aplicar,
    la devuelve intacta. Nunca altera los datos indexados.
    """
    text = (question or "").strip()
    if not text:
        return text
    low = text.lower()
    added: List[str] = []
    for source, target in QUERY_LEXICON:
        if source in low and target not in added:
            added.append(target)
    if not added:
        return text
    return f"{text} {' '.join(added)}"

# ---------------------------------------------------------------------------
# Estado lazy
# ---------------------------------------------------------------------------
_collection = None
_client = None
_initialized = False
_init_error: Optional[str] = None
_initializing = False  # primer arranque del motor (carga del modelo) en curso
_lock = threading.Lock()


def get_client():
    """Cliente ChromaDB compartido (path persistente) o None si no está disponible.

    Lo reutiliza `kb_store` para la base de conocimiento: una sola instancia de
    cliente por proceso evita el aviso «a chroma instance already exists».
    """
    _get_collection()
    return _client


def _get_collection():
    """Inicialización lazy de ChromaDB. Retorna None si no está disponible."""
    global _collection, _client, _initialized, _init_error, _initializing
    if _initialized:
        return _collection
    with _lock:
        if _initialized:
            return _collection
        _initialized = True
        _initializing = True  # el modelo aún se está cargando: no es un error

        try:
            import chromadb
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            RAG_DATA_PATH.mkdir(parents=True, exist_ok=True)

            ef = SentenceTransformerEmbeddingFunction(
                model_name=EMBEDDING_MODEL,
                device="cpu",
                normalize_embeddings=True,  # distancia coseno
            )
            _client = chromadb.PersistentClient(path=str(RAG_DATA_PATH))
            _collection = _client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            _init_error = None
            logger.info(
                "RAG listo — colección «%s» con %d eventos (modelo %s)",
                COLLECTION_NAME, _collection.count(), EMBEDDING_MODEL,
            )
        except ImportError as exc:
            _init_error = f"dependencia no instalada: {exc}"
            logger.warning("RAG desactivado — %s", _init_error)
            _collection = None
            _client = None
        except Exception as exc:  # noqa: BLE001 – nunca debe tumbar la app
            _init_error = str(exc)
            logger.warning("Error inicializando RAG store: %s", exc)
            _collection = None
            _client = None
        finally:
            _initializing = False

    return _collection


def is_available() -> bool:
    """True si el índice semántico está operativo en esta máquina."""
    return _get_collection() is not None


def status() -> Dict[str, Any]:
    """Estado del motor para mostrar en la UI (nunca lanza excepción)."""
    col = _get_collection()
    if col is None:
        # Durante la primera carga del modelo el motor aún no está listo, pero
        # no está «roto»: se distingue para no mostrar un motivo engañoso.
        reason = "inicializando el motor semántico…" if _initializing else (
            _init_error or "chromadb no instalado"
        )
        return {
            "available": False,
            "reason": reason,
            "model": EMBEDDING_MODEL,
            "total": 0,
        }
    try:
        return {
            "available": True,
            "reason": None,
            "model": EMBEDDING_MODEL,
            "total": int(col.count()),
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc), "model": EMBEDDING_MODEL, "total": 0}


def warmup() -> bool:
    """Carga el modelo de embeddings en segundo plano (arranque del backend).

    Evita que la primera consulta del asistente pague el coste de carga.
    Si algo falla (p. ej. sin red la primera vez), se deshace el estado
    marcado para que la siguiente consulta real reintente.
    """
    global _initialized, _collection, _init_error
    try:
        col = _get_collection()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Precarga RAG fallida (se reintentará): %s", exc)
        _initialized, _collection = False, None
        return False
    if col is None:
        # No es un error definitivo: permitir reintento en la primera consulta.
        _initialized, _collection = False, None
        logger.info("RAG no precargado (%s) — se reintentará al usarlo", _init_error)
        return False
    logger.info("RAG precargado y listo (%d eventos)", col.count())
    return True


# ---------------------------------------------------------------------------
# Construcción del documento
# ---------------------------------------------------------------------------
def _clean(value: Any) -> str:
    """Normaliza un valor del Excel a string plano (NaN/None → vacío)."""
    if value is None:
        return ""
    try:
        if isinstance(value, float) and value != value:  # NaN
            return ""
    except Exception:  # pragma: no cover
        pass
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "null", "nat") else text


def build_document(row: Dict[str, Any]) -> str:
    """
    Documento indexado por evento. Tres bloques:

      [REGISTRO]  campos crudos del Excel (inglés original)
      [DESC]      descripción del evento
      [ANÁLISIS]  justificación determinista en español (nivel + razón)

    El bloque [ANÁLISIS] es el puente que permite que una pregunta en español
    ("hubo fuerza bruta?") recupere un log en inglés ("brute force").
    """
    parts: List[str] = []

    fields = []
    for name in CONTEXT_FIELDS:
        if name == "Event_Description":
            continue
        value = _clean(row.get(name))
        if value:
            fields.append(f"{name}: {value}")
    if fields:
        parts.append("[REGISTRO] " + " | ".join(fields))

    description = _clean(row.get("Event_Description"))
    if description:
        parts.append(f"[DESC] {description}")

    level = _clean(row.get(LEVEL_KEY))
    reason = _clean(row.get(REASON_KEY))
    if level or reason:
        level_label = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "bajo": "Bajo"}.get(level, level)
        parts.append(f"[ANÁLISIS] Nivel: {level_label or 'n/a'} — {reason or 'sin justificación'}")

    return "\n".join(parts) or "[REGISTRO] registro vacío"


def build_metadata(row: Dict[str, Any], analysis_id: str, position: int) -> Dict[str, Any]:
    """Metadata filtrable (Chroma admite solo str/int/float/bool)."""
    meta: Dict[str, Any] = {
        "analysis_id": analysis_id,
        "position": position,
        "level": _clean(row.get(LEVEL_KEY)) or "bajo",
        "suspicious": 1 if row.get(SUSPICIOUS_KEY) else 0,
    }
    for key, out in (
        ("Log_ID", "log_id"),
        ("Hostname", "hostname"),
        ("Source_IP", "source_ip"),
        ("Destination", "destination"),
        ("Event_ID", "event_id"),
        ("User", "user"),
        ("Severity", "severity"),
        ("Timestamp", "timestamp"),
        ("Log_Source", "log_source"),
        ("Result", "result"),
        ("Event_Description", "description"),
        # Línea de comando del proceso: la evidencia directa de QUÉ se
        # ejecutó (powershell.exe -enc …). Sin ella en metadata, el prompt
        # del LLM (que se arma desde aquí, no desde el documento) nunca la
        # veía y el asistente negaba la existencia de procesos PowerShell.
        ("Process_CommandLine", "command_line"),
    ):
        value = _clean(row.get(key))
        if value and out == "result":
            # Result leído como float (celdas vacías): «404.0» → «404»
            if value.endswith(".0") and value[:-3].lstrip("-").isdigit():
                value = value[:-3]
        if value:
            meta[out] = value[:300]
    return meta


# ---------------------------------------------------------------------------
# Indexación
# ---------------------------------------------------------------------------
def index_events(analysis_id: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Indexa (o reindexa) todos los registros de un análisis.

    `records` debe traer ya calculadas las claves `_level`, `_reason` y
    `_suspicious` (las genera main.py con row_reason/_is_suspicious).

    Retorna un dict con `available`, `indexed`, `total` y, si falla, `reason`.
    Nunca lanza: el llamador es una tarea en segundo plano.
    """
    col = _get_collection()
    if col is None:
        return {"available": False, "indexed": 0, "total": len(records), "reason": _init_error}

    if not records:
        return {"available": True, "indexed": 0, "total": 0, "reason": None}

    try:
        # Reindexado idempotente: primero limpiamos lo que había de este análisis.
        col.delete(where={"analysis_id": analysis_id})

        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []

        for position, row in enumerate(records):
            log_id = _clean(row.get("Log_ID"))
            ids.append(f"{analysis_id}:{position}:{log_id or 'na'}")
            documents.append(build_document(row))
            metadatas.append(build_metadata(row, analysis_id, position))

        # Chroma recomienda lotes; 512 es seguro para textos cortos.
        batch = 512
        for start in range(0, len(ids), batch):
            col.add(
                ids=ids[start:start + batch],
                documents=documents[start:start + batch],
                metadatas=metadatas[start:start + batch],
            )

        indexed = len(ids)
        logger.info("RAG indexó %d eventos del análisis %s", indexed, analysis_id[:8])
        return {"available": True, "indexed": indexed, "total": indexed, "reason": None}

    except Exception as exc:  # noqa: BLE001
        logger.warning("Error indexando eventos en RAG: %s", exc)
        return {"available": False, "indexed": 0, "total": len(records), "reason": str(exc)}


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------
def _build_where(
    analysis_id: str,
    level: Optional[str] = None,
    suspicious: Optional[bool] = None,
) -> Dict[str, Any]:
    conditions: List[Dict[str, Any]] = [{"analysis_id": analysis_id}]
    if level:
        conditions.append({"level": level})
    if suspicious is not None:
        conditions.append({"suspicious": 1 if suspicious else 0})
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def query_events(
    analysis_id: str,
    question: str,
    n_results: int = 8,
    level: Optional[str] = None,
    suspicious: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """
    Recupera los eventos más similares semánticamente a `question`.

    Devuelve una lista ordenada por relevancia, cada elemento con:
      {score, distance, document, metadata, log_id, level, ...}
    Lista vacía si el motor no está disponible o no hay índice para ese
    análisis — el llamador decide cómo responder (fallback determinista).
    """
    col = _get_collection()
    if col is None:
        return []

    question = (question or "").strip()
    if not question:
        return []

    try:
        total = col.count()
        if total == 0:
            return []
        n = max(1, min(int(n_results), MAX_RESULTS, total))

        res = col.query(
            query_texts=[question[:2000]],
            n_results=n,
            where=_build_where(analysis_id, level, suspicious),
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error consultando RAG: %s", exc)
        return []

    hits: List[Dict[str, Any]] = []
    documents = (res.get("documents") or [[]])[0]
    metadatas = (res.get("metadatas") or [[]])[0]
    distances = (res.get("distances") or [[]])[0]

    for doc, meta, dist in zip(documents, metadatas, distances):
        meta = dict(meta or {})
        distance = float(dist)
        hits.append({
            # cosine distance → afinidad 0..1 (1 = idéntico)
            "score": round(max(0.0, 1.0 - distance), 4),
            "distance": round(distance, 4),
            "document": doc,
            "metadata": meta,
            "log_id": meta.get("log_id"),
            "level": meta.get("level", "bajo"),
            "hostname": meta.get("hostname"),
            "source_ip": meta.get("source_ip"),
            "event_id": meta.get("event_id"),
            "suspicious": bool(meta.get("suspicious")),
        })

    return hits


def has_index(analysis_id: str) -> bool:
    """True si el análisis ya tiene eventos indexados."""
    col = _get_collection()
    if col is None:
        return False
    try:
        res = col.get(where={"analysis_id": analysis_id}, limit=1, include=[])
        return bool(res.get("ids"))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Mantenimiento
# ---------------------------------------------------------------------------
def delete_analysis(analysis_id: str) -> int:
    """Elimina los eventos de un análisis del índice. Devuelve cuántos borró."""
    col = _get_collection()
    if col is None:
        return 0
    try:
        existing = col.get(where={"analysis_id": analysis_id}, include=[])
        ids = existing.get("ids") or []
        if ids:
            col.delete(ids=ids)
        return len(ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error borrando análisis del RAG: %s", exc)
        return 0


def purge_except(keep_ids: List[str], max_analyses: int = 5) -> int:
    """
    Conserva solo los últimos `max_analyses` análisis en el índice para que
    `backend/data/rag/` no crezca sin límite. Devuelve los borrados.
    """
    col = _get_collection()
    if col is None:
        return 0
    try:
        existing = col.get(include=["metadatas"])
        metas = existing.get("metadatas") or []
        ids = existing.get("ids") or []
        seen: List[str] = []
        for meta in metas:
            aid = (meta or {}).get("analysis_id")
            if aid and aid not in seen:
                seen.append(aid)
        stale = [aid for aid in seen if aid not in keep_ids]
        removed = 0
        for aid in stale:
            doomed = [i for i, m in zip(ids, metas) if (m or {}).get("analysis_id") == aid]
            if doomed:
                col.delete(ids=doomed)
                removed += len(doomed)
        if removed:
            logger.info("RAG purge: %d eventos de análisis antiguos", removed)
        return removed
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error en purge del RAG: %s", exc)
        return 0


def get_stats(analysis_id: Optional[str] = None) -> Dict[str, Any]:
    """Estadísticas del índice para la UI."""
    info = status()
    if not info["available"]:
        return info
    col = _get_collection()
    try:
        if analysis_id:
            res = col.get(where={"analysis_id": analysis_id}, include=["metadatas"])
            metas = res.get("metadatas") or []
            info["events"] = len(metas)
            info["levels"] = {}
            info["suspicious"] = 0
            for meta in metas:
                lvl = (meta or {}).get("level", "bajo")
                info["levels"][lvl] = info["levels"].get(lvl, 0) + 1
                if (meta or {}).get("suspicious"):
                    info["suspicious"] += 1
        else:
            info["events"] = int(col.count())
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
    return info
