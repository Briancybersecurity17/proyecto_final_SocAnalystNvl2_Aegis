"""scoring.py — funciones puras de puntuación del harness de evaluación.

Toman lo esperado (calculado con pandas por cases.py) y la respuesta real del
sistema, y devuelven el resultado. Sin I/O, sin red, sin LLM: se ejercitan
instantáneamente con pytest (tests/test_eval_scoring.py).

La verdad calculable nunca viene de texto escrito a mano: estas funciones solo
comparan, no juzgan prosa (spec §2: sin juez LLM).
"""
from __future__ import annotations

import re
import unicodedata

# Misma forma que ai_service._cited_log_ids (ver test_regex_scoring_coincide_con_ai_service):
# la respuesta cita Log_ID como «EVT-00002» y el guard del propio ai_service
# usa esta familia de tokens; si el scoring buscara otra forma, un ID citado
# pasaría desapercibido para el grounding.
LOG_ID_RE = re.compile(r"\b[A-Za-z]{2,12}[-_]\d{2,}\b")

# Números con formato español/inglés («1.234», «1,234», «1234»). El lookbehind/
# lookahead de [\d.] descarta IPs («10.10.97.1»): cada trozo de una IP está
# pegado a un punto. Un «1234» sin separador también vale.
_NUMBER_RE = re.compile(r"(?<![\d.])\d+(?:[.,]\d{3})*(?![\d.])")

# Negaciones fuertes: con cualquiera de estas la respuesta está negando algo.
_DENIAL_MARKERS = (
    "no encontre", "no hay", "no aparece", "ningun registro",
    "sin registros", "no se observa", "no encontramos", "no coincide",
    "no tengo evidencia", "no puedo confirmar",
)

# Ventana (chars) para decidir qué número es «el de la unidad» buscada.
_UNIT_WINDOW = 40


def _deacc(text: str) -> str:
    """Minúsculas sin acentos: «críticos» y «criticos» casan igual."""
    norm = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in norm if not unicodedata.combining(ch)).lower()


def cited_ids(text: str) -> list[str]:
    """Log_ID candidatos citados en una respuesta, en orden de aparición."""
    return LOG_ID_RE.findall(text or "")


def invented_ids(cited: list[str], dataset_ids: set[str]) -> list[str]:
    """De los IDs citados, los que NO existen en el dataset (esperado: 0).

    Solo cuentan los tokens de la MISMA familia (prefijo antes del guion o
    del guion bajo) que los IDs reales del dataset, igual que filtra el guard
    `_grounding_ok` de producción: la regex de extracción también casa hosts
    como «WS-018», y contarlos como Log_ID inventado daría falsos positivos.
    """
    prefixes = {token.split("-")[0].split("_")[0] for token in dataset_ids}
    return [
        token for token in cited
        if token.split("-")[0].split("_")[0] in prefixes and token not in dataset_ids
    ]


def _number_value(raw: str) -> int:
    """«1.234» / «1,234» / «1234» → 1234 (solo si el separador es de miles)."""
    cleaned = raw.replace(".", "").replace(",", "") if len(raw) > 3 else raw
    try:
        return int(cleaned)
    except ValueError:
        return 0


def _unit_positions(text: str, unidad: str) -> list[int]:
    """Posiciones de cada ocurrencia de la unidad (ya sin acentos)."""
    return [m.start() for m in re.finditer(re.escape(unidad), text)]


def number_in_answer(text: str, expected: int, unidades: str | None = None) -> bool:
    r"""¿Aparece `expected` como número real de la respuesta?

    - Frontera de token: esperar 97 no acepta «1977».
    - Formatos ES/EN: «1.234», «1,234», «1234» son el mismo 1234.
    - IPs y fechas no cuentan como números (lookaround de [\d.]).
    - Con `unidades`, el número debe ser el MÁS CERCANO a la unidad declarada:
      «La IP 10.10.97.1 originó 68 eventos» prueba 68 (no 97 ni 18), porque
      68 es lo más próximo a «eventos». Sin unidad, cualquier coincidencia vale.
    """
    body = _deacc(text)
    unidad = _deacc(unidades) if unidades else ""
    unit_pos = _unit_positions(body, unidad) if unidad else []

    candidates = [
        m for m in _NUMBER_RE.finditer(body)
        if _number_value(m.group()) == expected
    ]
    if not candidates:
        return False
    if not unit_pos:
        return True
    # Con unidad: el número debe ser el MÁS CERCANO a ella de toda la frase.
    # «La IP 10.10.97.1 originó 68 eventos desde el puerto 18» prueba 68
    # (distancia mínima a «eventos»), no 18 (que queda lejos, tras «desde»).
    def dist(match: "re.Match[str]") -> int:
        return min(abs(match.start() - p) for p in unit_pos)

    best = min(dist(m) for m in _NUMBER_RE.finditer(body))
    return any(dist(m) == best for m in candidates)


def precision_at_k(cited: list[str], expected: set[str]) -> float:
    """De lo citado, qué fracción es correcta (k = len(cited))."""
    if not cited:
        return 0.0
    hits = sum(1 for token in cited if token in expected)
    return round(hits / len(cited), 2)


def hit_at_k(cited: list[str], expected: set[str]) -> bool:
    """¿Al menos un ID relevante aparece en lo citado?"""
    return any(token in expected for token in cited)


def is_wide(expected: set[str], k: int = 8) -> bool:
    """Pregunta ancha si el esperado supera 2*k (spec §5.1): allí manda
    precisión@k; el recall sería bajo por construcción (la ventana trae k)."""
    return len(expected) > 2 * k


def denial_ok(answer: str, cited: list[str]) -> bool:
    """Refusal correcto: la respuesta NIEGA y cita cero Log_ID.

    Un «No encontré nada; revise EVT-00007» no es un refusal: está afirmando
    algo sobre un ID concreto.
    """
    if cited:
        return False
    body = _deacc(answer)
    return any(marker in body for marker in _DENIAL_MARKERS)


def macro_average(per_category: dict[str, float]) -> float:
    """Media macro: cada categoría pesa igual (spec §8), no por nº de casos."""
    if not per_category:
        return 0.0
    return round(sum(per_category.values()) / len(per_category), 2)
