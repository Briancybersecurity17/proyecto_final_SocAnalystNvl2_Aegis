"""severity.py — clasificación de severidad del archivo → nuestros 4 niveles.

Módulo NEUTRAL: solo stdlib, sin FastAPI, pandas ni ChromaDB, para que
`eval/cases.py` pueda calcular la verdad sin arrastrar la app entera (spec
§3: «cases.py y scoring.py no dependen de FastAPI, Ollama ni ChromaDB»).
`main.normalize_level` re-exporta desde aquí, así que la verdad del eval y la
clasificación del producto son el MISMO código — no un duplicado que puede
desincronizarse.

El orden del mapeo refleja la política de la organización: warning se trata
como crítico (no como medio).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

SEVERITY_TO_LEVEL: Dict[str, str] = {
    "critical": "critico",
    "critico": "critico",
    "high": "alto",
    "alto": "alto",
    "medium": "medio",
    "medio": "medio",
    "warning": "critico",
    "low": "bajo",
    "bajo": "bajo",
    "informational": "bajo",
    "informative": "bajo",
    "info": "bajo",
}
LEVEL_ORDER = ["critico", "alto", "medio", "bajo"]
LEVEL_LABELS = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "bajo": "Bajo"}

# Palabras clave para mapear valores desconocidos (otros datasets) de forma
# robusta, sin depender de coincidencia exacta. Se revisan de más grave a más
# leve; el primero que gana se aplica. Default: "bajo" (sin evidencia de riesgo).
SEVERITY_KEYWORDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("critico", ("critical", "crit", "warn", "err", "severe", "fatal", "emergency", "alert")),
    ("alto", ("high", "major", "elevated")),
    ("medio", ("medium", "moderate", "average")),
    ("bajo", ("low", "inform", "info", "trivial", "minor")),
]


def normalize_level(severity: Any) -> str:
    """Map a raw severity value to one of our four risk levels.

    Coincidencia exacta primero (catálogo conocido), luego coincidencia parcial
    (contiene) para ser robustos con otros datasets, y por defecto "bajo"
    (sin evidencia de riesgo destacada).
    """
    raw = str(severity).strip().lower()
    if not raw or raw in ("nan", "none", "null", ""):
        return "bajo"

    # 1. Coincidencia exacta (catálogo conocido)
    exact = SEVERITY_TO_LEVEL.get(raw)
    if exact:
        return exact

    # 2. Coincidencia parcial por palabras clave (otros datasets)
    for level, keywords in SEVERITY_KEYWORDS:
        if any(k in raw for k in keywords):
            return level

    return "bajo"
