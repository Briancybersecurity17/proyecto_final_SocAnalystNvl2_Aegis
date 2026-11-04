# -*- coding: utf-8 -*-
"""Fichas candidatas: el aprendizaje de ataques nuevos con aprobación HUMANA.

Qué hace y por qué vive aparte de `attack_cards.py`: ese módulo es el motor
que EVALÚA el catálogo vigente; este es el taller donde se forjan fichas
nuevas a partir de lo que el analista pregunta sobre sus registros. La señal
es barata y honesta: el asistente responde «este ataque todavía no está en el
catálogo» y, si hay LLM habilitado, propone un borrador de ficha con las
columnas y Event IDs REALES del dataframe. El borrador queda en
`data/attack_cards_candidates.json` con estado `pendiente`.

Nunca automático: una candidata solo entra a `knowledge/attack_cards.json`
cuando un humano la aprueba (`approve`). `reject` marca la decisión y la
candidata no se revive sola ante una nueva pregunta. Hasta entonces
`match_card` ni siquiera la conoce, así que la respuesta del chat no cambia.

La validación es DETERMINISTA (mismo criterio que las fichas del catálogo):
tipos de regla cerrados + `attack_cards.evaluate` contra el dataframe actual
(columnas y Event IDs reales), más las colisiones que romperían al motor
(id repetido, sinónimo ya cubierto, formato de id inválido). Si el borrador
no pasa, no se guarda: el LLM propone, la validación decide.

Tipos de retorno de `add_candidate` / `approve` / `reject`:

    {"ok": bool, "motivo": str, "missing": [...], "candidate": dict|None}

Motivos de `add_candidate`: guardada, actualizada, borrador_invalido,
id_invalido, id_duplicado, sinonimo_duplicado, rechazada_previa,
aprobada_previa, sin_regla, regla_desconocida, sin_columna, sin_event_ids,
regla_invalida, sin_datos.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("soc.attack_candidates")

# Patrón dual paquete/suelto (mismo criterio que ai_service): el módulo se
# carga igual desde `backend.main` (uvicorn) que suelto en sys.path (tests).
try:
    from backend import attack_cards
except ImportError:  # pragma: no cover – módulo suelto (sys.path con backend/)
    import attack_cards

BASE = Path(__file__).resolve().parent
# El path real (fuente de verdad del analista) queda expuesto para tests de
# aislamiento; el activo es `CANDIDATES_PATH` y es lo que todos leen/escriben.
CANDIDATES_PATH_REAL = BASE / "data" / "attack_cards_candidates.json"
CANDIDATES_PATH = CANDIDATES_PATH_REAL

CAMPOS_OBLIGATORIOS = ("id", "nombre", "sinonimos", "que_es", "como_se_ve",
                       "umbrales", "ejemplo")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,49}$")

ESTADOS = ("pendiente", "aprobada", "rechazada")

_lock = threading.RLock()   # reentrante: _save() llama a _load() dentro del bloqueo
_cache: Optional[List[Dict[str, Any]]] = None   # espejo del JSON en RAM


# ---------------------------------------------------------------------------
# Persistencia (JSON = fuente de verdad, patrón feedback_store)
# ---------------------------------------------------------------------------
def _load() -> List[Dict[str, Any]]:
    """Candidatas vigentes desde disco. Lista vacía si falta o está corrupto."""
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        try:
            raw = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
            items = raw.get("candidates", []) if isinstance(raw, dict) else []
            _cache = [c for c in items if isinstance(c, dict) and c.get("id")]
        except FileNotFoundError:
            _cache = []
        except Exception as exc:  # noqa: BLE001 – JSON roto nunca tumba la app
            logger.warning("attack_candidates: JSON ilegible (%s); empiezo vacío", exc)
            _cache = []
        return _cache


def _save(candidates: List[Dict[str, Any]]) -> None:
    """Escribe el JSON espejo y refresca la caché en RAM."""
    global _cache
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_PATH.write_text(
        json.dumps({"version": 1, "candidates": candidates},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _cache = candidates


def list_candidates(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Candidatas, opcionalmente filtradas por estado (None = todas)."""
    items = _load()
    if status is None:
        return list(items)
    return [c for c in items if c.get("status") == status]


def _find(candidates: List[Dict[str, Any]], candidate_id: str):
    for cand in candidates:
        if cand.get("id") == candidate_id:
            return cand
    return None


def _res(ok: bool, motivo: str, missing: Any = (), candidate: Any = None):
    return {"ok": ok, "motivo": motivo, "missing": list(missing or []),
            "candidate": candidate}


# ---------------------------------------------------------------------------
# Validación determinista del borrador
# ---------------------------------------------------------------------------
def _validar(draft: Any, df) -> Dict[str, Any]:
    """¿El borrador es una ficha evaluable sobre ESTE dataframe? Nunca lanza.

    Devuelve el dict de `attack_cards.evaluate` cuando todo cierra, o un
    motivo propio (`borrador_invalido`, `id_invalido`, `id_duplicado`,
    `sinonimo_duplicado`) cuando la ficha ni siquiera llega al motor.
    """
    if not isinstance(draft, dict):
        return _res(False, "borrador_invalido")

    faltan = [k for k in CAMPOS_OBLIGATORIOS if not draft.get(k)]
    if faltan:
        return _res(False, "borrador_invalido", faltan)

    idc = str(draft.get("id"))
    if not ID_RE.match(idc):
        return _res(False, "id_invalido", ["id"])

    # Colisiones con el catálogo vigente: un id repetido o un sinónimo ya
    # cubierto harían ambigua a match_card (empate resuelto por longitud,
    # pero dos fichas para el mismo patrón es desinformación, no señal).
    sinonimos = {str(attack_cards._norm(s))
                 for s in draft.get("sinonimos") or [] if str(s).strip()}
    for card in attack_cards.load_cards():
        if str(card.get("id")) == idc:
            return _res(False, "id_duplicado", ["id"])
        for syn in card.get("sinonimos") or []:
            nsyn = str(attack_cards._norm(syn))
            if nsyn and nsyn in sinonimos:
                return _res(False, "sinonimo_duplicado", [syn])

    # Regla: presencia y tipo cerrado lo decide evaluate (nunca eval ni
    # código dinámico); df ausente también.
    if not isinstance(draft.get("regla"), dict):
        return _res(False, "sin_regla", ["regla"])
    ev = attack_cards.evaluate(draft, df)
    # evaluate habla `applicable`; acá el contrato es `ok` — se traduce
    # manteniendo motivo/missing/total/detections a la vista del revisor.
    return {
        "ok": bool(ev.get("applicable")),
        "applicable": bool(ev.get("applicable")),
        "motivo": str(ev.get("motivo") or ""),
        "missing": list(ev.get("missing") or []),
        "total": int(ev.get("total") or 0),
        "detections": list(ev.get("detections") or []),
    }


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def add_candidate(draft: Any, question: str, df) -> Dict[str, Any]:
    """Valida el borrador y lo guarda (o actualiza) como pendiente.

    Nunca escribe en el catálogo: el estado queda `pendiente` hasta que un
    humano llame a `approve`. Si ya había una pendiente con el mismo id se
    ACTUALIZA (la nueva evaluación manda); si el analista la rechazó, no se
    revive.
    """
    res = _validar(draft, df)
    if not res.get("ok"):
        return res

    card = dict(draft)
    with _lock:
        candidates = _load()
        prev = _find(candidates, card["id"])
        now = datetime.now().isoformat(timespec="seconds")
        record = {
            "id": card["id"],
            "status": "pendiente",
            "question": str(question or ""),
            "card": card,
            "evaluacion": {
                "applicable": bool(res.get("applicable")),
                "motivo": str(res.get("motivo")),
                "missing": list(res.get("missing") or []),
                "total": int(res.get("total") or 0),
                "detections": len(res.get("detections") or []),
            },
            "creada": now,
            "actualizada": now,
        }
        if prev is not None:
            estado = prev.get("status")
            if estado == "rechazada":
                return _res(False, "rechazada_previa", (), prev)
            if estado == "aprobada":
                return _res(False, "aprobada_previa", (), prev)
            record["creada"] = prev.get("creada", now)
            candidates[candidates.index(prev)] = record
            _save(candidates)
            return _res(True, "actualizada", (), record)

        candidates.append(record)
        _save(candidates)
        return _res(True, "guardada", (), record)


def approve(candidate_id: str) -> Dict[str, Any]:
    """Aprueba la candidata: su ficha entra al catálogo y la caché se
    invalida (la próxima pregunta ya la puede matchear). Idempotente."""
    with _lock:
        candidates = _load()
        cand = _find(candidates, str(candidate_id))
        if cand is None:
            return _res(False, "no_existe")
        if cand.get("status") == "aprobada":
            return _res(True, "ya_aprobada", (), cand)
        if cand.get("status") == "rechazada":
            return _res(False, "rechazada_previa", (), cand)

        # Append al catálogo (leer-modificar-escribir con el mismo lock).
        try:
            data = json.loads(attack_cards.CATALOGO.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 – catálogo roto: no aprobar
            logger.warning("attack_candidates: catálogo ilegible: %s", exc)
            return _res(False, "catalogo_ilegible")
        cards = data.get("cards", []) if isinstance(data, dict) else []
        if not any(str(c.get("id")) == cand["id"] for c in cards
                   if isinstance(c, dict)):
            cards.append(cand.get("card") or {})
            data["cards"] = cards
            attack_cards.CATALOGO.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        # Invalidación DIRECTA: load_cards() cachea en _CACHE y el chat debe
        # ver la ficha aprobada sin reiniciar el backend.
        attack_cards._CACHE = None

        cand["status"] = "aprobada"
        cand["aprobada"] = datetime.now().isoformat(timespec="seconds")
        _save(candidates)
        return _res(True, "aprobada", (), cand)


def reject(candidate_id: str) -> Dict[str, Any]:
    """Rechaza la candidata: queda marcada y fuera del catálogo para siempre
    (una nueva pregunta con el mismo borrador no la revive)."""
    with _lock:
        candidates = _load()
        cand = _find(candidates, str(candidate_id))
        if cand is None:
            return _res(False, "no_existe")
        if cand.get("status") == "aprobada":
            return _res(False, "ya_aprobada", (), cand)
        if cand.get("status") == "rechazada":
            return _res(True, "ya_rechazada", (), cand)
        cand["status"] = "rechazada"
        cand["rechazada"] = datetime.now().isoformat(timespec="seconds")
        _save(candidates)
        return _res(True, "rechazada", (), cand)
