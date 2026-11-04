# -*- coding: utf-8 -*-
"""Ataque: el motor de reglas de las fichas de ataque del catálogo.

Qué es y por qué vive aparte de `behavior_correlations.py`: acá los umbrales
vienen escritos en cada ficha del catálogo (`knowledge/attack_cards.json`,
campos `regla` y `umbrales`), mientras que `behavior_correlations` deja los
umbrales fijos para todas sus correlaciones (MIN_FAILURES, MIN_SUCCESSES...).
No duplicamos lógica: `evaluate` solo evalúa la regla de CADA ficha contra el
DataFrame actual; `behavior_correlations.py` queda intacto.

Tipos de regla cerrados (cuatro, sin eval ni código dinámico):

  group_threshold   agrupa por una columna (o varias) y exige un mínimo.
                    `conteo` filtra qué filas se cuentan por igualdad
                    («*» = todas); `contar_distintos` cuenta valores
                    distintos en vez de filas (hosts por IP); `distintos`
                    exige además N valores distintos de otra columna
                    (spraying: usuarios); `segundo_conteo`/`min_segundo`
                    exigen un segundo conteo (fuerza bruta: ≥1 éxito).
                    Los rótulos `nombre_*` son las claves de `metricas`.
  event_presence     basta con que exista el Event ID de la ficha (desde
                    `card.event_ids`) y supere `min` ocurrencias.
  event_burst        N ocurrencias del `event_id` dentro de la ventana.
  row_filter         filas cuya `columna` contiene el texto `contiene`
                    (case-insensitive) y supera `min`.

Si a la regla le faltan columnas o el archivo no trae los Event IDs que
necesita, la regla NO se aplica: `applicable=False` con el motivo
(`sin_columna` / `sin_event_ids`) y el detalle en `missing`. Nunca inventamos
filas y nunca lanza: el chat prefiere «no se pudo evaluar» a una excepción
en mitad de la respuesta.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger("soc.attack_cards")

CATALOGO = Path(__file__).resolve().parent / "knowledge" / "attack_cards.json"
REGLAS_CONOCIDAS = ("group_threshold", "event_presence", "event_burst", "row_filter")
MOTIVOS_OK = (
    "ok", "sin_regla", "sin_datos", "sin_columna",
    "sin_event_ids", "regla_invalida", "regla_desconocida", "error",
)
VENTANA_MINUTOS = 60
TOP_DETECTIONS = 5
MAX_LOG_IDS = 20
_CACHE: Optional[List[Dict[str, Any]]] = None


# ------------------------------------------------------------------ catálogo
def load_cards() -> List[Dict[str, Any]]:
    """Lee el catálogo de fichas (se cachea: el JSON no cambia en caliente)."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        data = json.loads(CATALOGO.read_text(encoding="utf-8"))
        cards = data.get("cards", [])
    except Exception as exc:      # pragma: no cover - JSON legado ya probado
        logger.warning("attack_cards: no se pudo leer el catálogo: %s", exc)
        cards = []
    _CACHE = cards
    return cards


def _norm(text: str) -> str:
    """Minúsculas, sin tildes y sin signos: para comparar sinónimos."""
    text = str(text or "").lower()
    text = (text.replace("á", "a").replace("é", "e").replace("í", "i")
                .replace("ó", "o").replace("ú", "u").replace("ñ", "n"))
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def match_card(question: str) -> Optional[Dict[str, Any]]:
    """La ficha cuyo sinónimo aparece en la pregunta.

    Sinónimos en minúsculas sin tildes, con límites de palabra y plural
    tolerado («barridos» → barrido). Ante empate gana el sinónimo más largo:
    así «fuerza bruta distribuida» elige spraying y «fuerza bruta rdp» elige
    la ficha de RDP, no la genérica de fuerza bruta.
    """
    norm_q = _norm(question)
    if not norm_q:
        return None
    best: Optional[Dict[str, Any]] = None
    best_len = 0
    for card in load_cards():
        for syn in card.get("sinonimos") or []:
            nsyn = _norm(syn)
            if not nsyn or len(nsyn) <= best_len:
                continue
            if re.search(rf"(?<!\w){re.escape(nsyn)}s?(?!\w)", norm_q):
                best, best_len = card, len(nsyn)
    return best


def mentions_generic_attack(question: str) -> bool:
    """¿La pregunta nombra el tema de ataques en general, sin ninguna ficha
    concreta? Para responder honestamente con el catálogo en vez de caer
    al RAG o a un agregado numérico que no contesta lo preguntado."""
    if match_card(question):
        return False
    norm_q = _norm(question)
    generico = (
        "ataque", "ataques", "ciberataque", "incidente", "incidentes",
        "malware", "ransomware", "phishing", "seguridad", "compromiso",
        "brecha", "intrusion",
    )
    return any(p in norm_q for p in generico)


# ------------------------------------------------------------------ helpers
def _norm_val(value: Any) -> str:
    """«4625.0» (Excel numérico) → «4625»: los Event ID se comparan como texto."""
    text = str(value)
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _norm_series(series) -> pd.Series:
    return series.map(_norm_val)


def _event_ids(card: Dict[str, Any]) -> List[str]:
    """Event IDs de la ficha como texto, sin duplicados y sin sufijo «.0»."""
    raw = card.get("event_ids")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for value in raw:
        text = _norm_val(value)
        if text and text not in out:
            out.append(text)
    return out


def _base(motivo: str, missing: Any = ()) -> Dict[str, Any]:
    return {
        "applicable": False,
        "motivo": motivo,
        "missing": list(missing or []),
        "total": 0,
        "detections": [],
    }


def _ok(total: int, detections: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "applicable": True,
        "motivo": "ok",
        "missing": [],
        "total": int(total),
        "detections": detections,
    }


def _sample(df, mask) -> List[str]:
    """Log_IDs de ejemplo de las filas marcadas (orden del archivo)."""
    if "Log_ID" not in df.columns:
        return []
    return [str(v) for v in df.loc[mask, "Log_ID"].head(MAX_LOG_IDS).tolist()]


def _required_columns(regla: Dict[str, Any], tipo: str) -> List[str]:
    """Columnas que la regla necesita para poder evaluarse."""
    cols: List[str] = []

    def add(col: Any) -> None:
        if isinstance(col, str) and col and col not in cols:
            cols.append(col)

    if tipo in ("event_presence", "event_burst"):
        add("Event_ID")
        return cols
    if tipo == "row_filter":
        add(regla.get("columna"))
        return cols
    # group_threshold
    col = regla.get("columna")
    if isinstance(col, list):
        for c in col:
            add(c)
    else:
        add(col)
    for key in ("conteo", "segundo_conteo"):
        filtro = regla.get(key)
        if isinstance(filtro, dict):
            for k, v in filtro.items():
                if v != "*":
                    add(k)
    add(regla.get("distintos"))
    add(regla.get("contar_distintos"))
    return cols


def _required_events(card: Dict[str, Any], regla: Dict[str, Any], tipo: str) -> List[str]:
    """Event IDs sin los que la regla no tiene datos en el archivo.

    Para `event_presence` salen de la ficha (esa regla vive del `event_ids`);
    para `group_threshold` de los filtros `conteo`/`segundo_conteo`; el resto
    de reglas no exige eventos concretos (row_filter trabaja sobre texto).
    """
    if tipo == "event_presence":
        return _event_ids(card)
    if tipo == "event_burst":
        eid = _norm_val(regla.get("event_id") or "").strip()
        return [eid] if eid else []
    if tipo == "group_threshold":
        out: List[str] = []
        for key in ("conteo", "segundo_conteo"):
            filtro = regla.get(key)
            if isinstance(filtro, dict):
                value = filtro.get("Event_ID")
                if isinstance(value, str) and value and value != "*":
                    ev = _norm_val(value)
                    if ev not in out:
                        out.append(ev)
        return out
    return []


def _ventana(df, ts_col: str, minutos: int = VENTANA_MINUTOS):
    """Rango [desde, hasta] del DataFrame a partir de su marca más reciente.

    Sin fecha legible no hay ventana: devolvemos None (la regla cuenta todo),
    no inventamos orden temporal."""
    if ts_col not in df.columns or len(df) == 0:
        return None
    try:
        parsed = pd.to_datetime(df[ts_col], errors="coerce")
        fin = parsed.max()
        if pd.isna(fin):
            return None
        fin = fin.to_pydatetime() if hasattr(fin, "to_pydatetime") else fin
    except Exception:
        return None
    return fin - timedelta(minutes=minutos), fin


# ------------------------------------------------------------------ reglas
def _presence(card: Dict[str, Any], df, regla: Dict[str, Any]) -> Dict[str, Any]:
    eids = _event_ids(card)
    if not eids:
        return _base("regla_invalida", ["event_ids"])
    minimo = regla.get("min", 1)
    if not isinstance(minimo, (int, float)) or minimo <= 0:
        return _base("regla_invalida", ["min"])
    norm = _norm_series(df["Event_ID"])
    detections: List[Dict[str, Any]] = []
    for eid in eids:
        mask = norm == eid
        n = int(mask.sum())
        if n >= minimo:
            detections.append({
                "evento": eid,
                "cantidad": n,
                "log_ids": _sample(df, mask),
            })
    return _ok(len(detections), detections)


def _burst(df, regla: Dict[str, Any]) -> Dict[str, Any]:
    eid = _norm_val(regla.get("event_id") or "").strip()
    if not eid:
        return _base("regla_invalida", ["event_id"])
    minimo = regla.get("min")
    if not isinstance(minimo, (int, float)) or minimo <= 0:
        return _base("regla_invalida", ["min"])
    mask = _norm_series(df["Event_ID"]) == eid
    ts_col = regla.get("columna_ts") or "Timestamp"
    ventana = _ventana(df, ts_col, int(regla.get("minutos") or VENTANA_MINUTOS))
    if ventana is not None and ts_col in df.columns:
        try:
            ts = pd.to_datetime(df[ts_col], errors="coerce")
            desde, hasta = ventana
            mask = mask & (ts >= desde) & (ts <= hasta)
        except Exception:          # pragma: no cover - fecha ilegal ya filtrada
            pass
    n = int(mask.sum())
    if n >= minimo:
        return _ok(1, [{"evento": eid, "cantidad": n, "log_ids": _sample(df, mask)}])
    return _ok(0, [])


def _group(df, regla: Dict[str, Any]) -> Dict[str, Any]:
    col = regla.get("columna")
    cols = list(col) if isinstance(col, list) else ([col] if col else [])
    minimo = regla.get("min")
    if (not cols or any(not isinstance(c, str) for c in cols)
            or not isinstance(minimo, (int, float)) or minimo <= 0):
        return _base("regla_invalida", ["columna", "min"])

    distintos = regla.get("distintos")
    min_dist = regla.get("min_distintos")
    if distintos and (not isinstance(min_dist, (int, float)) or min_dist <= 0):
        return _base("regla_invalida", ["min_distintos"])

    # Filas que entran en el conteo principal («*» o ausente = todas).
    sub = df
    conteo = regla.get("conteo")
    if isinstance(conteo, dict):
        for k, v in conteo.items():
            if v == "*":
                continue
            if k not in df.columns:
                return _base("sin_columna", [k])
            sub = sub[_norm_series(sub[k]) == str(v)]
    if sub.empty:
        return _ok(0, [])

    if distintos and distintos not in sub.columns:
        return _base("sin_columna", [distintos])

    # Segundo conteo (p. ej. éxitos 4624): filas que cumplen la segunda regla.
    c2 = None
    segundo = regla.get("segundo_conteo")
    min_seg = regla.get("min_segundo")
    if isinstance(segundo, dict) and segundo:
        if not isinstance(min_seg, (int, float)) or min_seg <= 0:
            return _base("regla_invalida", ["min_segundo"])
        sub2 = df
        for k, v in segundo.items():
            if v == "*":
                continue
            if k not in df.columns:
                return _base("sin_columna", [k])
            sub2 = sub2[_norm_series(sub2[k]) == str(v)]
        c2 = None if sub2.empty else sub2.groupby(cols).size()

    contar = regla.get("contar_distintos")
    if contar:
        if contar not in sub.columns:
            return _base("sin_columna", [contar])
        counts = sub.groupby(cols)[contar].nunique()
    else:
        counts = sub.groupby(cols).size()

    nd = None
    if distintos:
        nd = sub.groupby(cols)[distintos].nunique()

    elegidos = []
    for idx in counts.index:
        if counts[idx] < minimo:
            continue
        if nd is not None and (idx not in nd.index or nd[idx] < min_dist):
            continue
        if c2 is not None and (idx not in c2.index or c2[idx] < min_seg):
            continue
        elegidos.append(idx)
    if not elegidos:
        return _ok(0, [])

    elegidos.sort(key=lambda i: int(counts[i]), reverse=True)
    detections: List[Dict[str, Any]] = []
    for idx in elegidos[:TOP_DETECTIONS]:
        valores_list = list(idx) if isinstance(idx, tuple) else [idx]
        valores = {c: _norm_val(v) for c, v in zip(cols, valores_list)}
        metricas: Dict[str, Any] = {
            regla.get("nombre_conteo") or "eventos": int(counts[idx]),
        }
        if nd is not None:
            metricas[regla.get("nombre_distintos") or "distintos"] = int(nd[idx])
        if c2 is not None:
            metricas[regla.get("nombre_segundo") or "segundo"] = int(c2[idx])
        det: Dict[str, Any] = {
            "valores": valores,
            "cantidad": int(counts[idx]),
            "metricas": metricas,
        }
        if len(cols) == 1:
            det["grupo"] = _norm_val(valores_list[0])
        # Log_IDs de TODO el grupo (incluye el segundo conteo): el analista
        # ve fallos y éxitos del mismo grupo en el orden del archivo.
        if "Log_ID" in df.columns:
            mask = None
            for c, v in valores.items():
                m = _norm_series(df[c]) == v
                mask = m if mask is None else (mask & m)
            det["log_ids"] = _sample(df, mask)
        else:
            det["log_ids"] = []
        detections.append(det)
    return _ok(len(elegidos), detections)


def _row_filter(df, regla: Dict[str, Any]) -> Dict[str, Any]:
    col = regla.get("columna")
    contiene = regla.get("contiene")
    minimo = regla.get("min")
    if not isinstance(col, str) or not isinstance(contiene, str) or not contiene:
        return _base("regla_invalida", ["columna", "contiene"])
    if not isinstance(minimo, (int, float)) or minimo <= 0:
        return _base("regla_invalida", ["min"])
    if col not in df.columns:      # por si evaluate cambia de orden algún día
        return _base("sin_columna", [col])
    try:
        mask = df[col].astype(str).str.contains(
            contiene, case=False, regex=False, na=False
        )
    except Exception as exc:       # pragma: no cover - columna ya verificada
        logger.warning("attack_cards: row_filter falló: %s", exc)
        return _base("regla_invalida", [col])
    n = int(mask.sum())
    if n >= minimo:
        return _ok(n, [{"cantidad": n, "log_ids": _sample(df, mask)}])
    return _ok(0, [])


def evaluate(card: Dict[str, Any], df) -> Dict[str, Any]:
    """Evalúa la regla de la ficha contra el DataFrame actual.

    Devuelve siempre un dict con `applicable` (bool), `motivo` (str),
    `missing` (columnas/eventos que faltan), `total` y `detections`. Si falta
    la columna o el archivo no trae los Event IDs, `applicable=False` con el
    motivo; si la regla no está o no se entiende, también. Nunca lanza.
    """
    try:
        if not isinstance(card, dict):
            return _base("sin_regla")
        regla = card.get("regla")
        if not isinstance(regla, dict):
            return _base("sin_regla")
        if df is None or len(df) == 0:
            return _base("sin_datos")
        tipo = str(regla.get("tipo") or "").strip().lower()
        if tipo not in REGLAS_CONOCIDAS:
            return _base("regla_desconocida")

        # 1) Columnas que la regla necesita: sin ellas no se evalúa nada.
        faltan = [c for c in _required_columns(regla, tipo) if c not in df.columns]
        if faltan:
            return _base("sin_columna", faltan)

        # 2) Event IDs de la regla presentes en el archivo: si no hay NINGUNO
        #    de los que la regla cuenta, el archivo no trae esos datos.
        eids = _required_events(card, regla, tipo)
        if eids and "Event_ID" in df.columns:
            presentes = set(_norm_series(df["Event_ID"]).unique())
            ausentes = [e for e in eids if e not in presentes]
            if ausentes and len(ausentes) == len(eids):
                return _base("sin_event_ids", ausentes)

        # 3) Evaluar la regla (los cuatro tipos cerrados).
        if tipo == "group_threshold":
            return _group(df, regla)
        if tipo == "event_presence":
            return _presence(card, df, regla)
        if tipo == "event_burst":
            return _burst(df, regla)
        return _row_filter(df, regla)
    except Exception as exc:      # pragma: no cover - nunca debe pasar
        logger.warning(
            "attack_cards: evaluate falló (%s): %s",
            card.get("id") if isinstance(card, dict) else "?", exc,
        )
        return _base("error")