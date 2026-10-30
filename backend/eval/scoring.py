"""scoring.py — funciones puras de puntuación del harness de evaluación.

Toman lo esperado (calculado con pandas por cases.py) y la respuesta real del
sistema, y devuelven el resultado. Sin I/O, sin red, sin LLM: se ejercitan
instantáneamente con pytest (tests/test_eval_scoring.py y
tests/test_eval_report_scoring.py).

La verdad calculable nunca viene de texto escrito a mano: estas funciones solo
comparan, no juzgan prosa (spec §2: sin juez LLM).

Las funciones de la categoría `report` (informe ejecutivo) detectan los
mismos fallos que el validador de `ai_service` pero con una implementación
INDEPENDIENTE a propósito: si ambos detectan lo mismo por caminos distintos,
se cruzan y se corrigen; si solo lo detectara uno, la medida mintiría.
"""
from __future__ import annotations

import re
import unicodedata

# Misma forma que ai_service._cited_log_ids (ver test_regex_scoring_coincide_con_ai_service):
# la respuesta cita Log_ID como «EVT-00002» y el guard del propio ai_service
# usa esta familia de tokens; si el scoring buscara otra forma, un ID citado
# pasaría desapercibido para el grounding.
LOG_ID_RE = re.compile(r"\b[A-Za-z]{2,12}[-_]\d{2,}\b")

# Números con formato español/inglés («1.234», «1,234», «1234»). El lookbehind
# de [\d./-] descarta lo pegado a un punto o a un guion: los trozos de una IP
# («10.10.97.1») y los de una fecha («2026-01-10») — un «2026» solto en una
# fecha NO es un conteo. El lookahead solo rechaza puntuación SEGUIDA de otro
# dígito («10.10», «31/12»): el punto de cierre de frase («…nivel Bajo: 603.»)
# sí cuenta, o cualquier cifra al final de oración sería invisible para
# level_count_near y number_in_answer.
_NUMBER_RE = re.compile(r"(?<![\d./-])\d+(?:[.,]\d{3})*(?!\d|[./-]\d)")

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
    """Log_ID candidatos citados en una respuesta, en orden de aparición.

    Cada ID cuenta UNA sola vez: repetir «EVT-1, EVT-1» no aporta tres
    evidencias sino una, y sin deduplicar inflaría la precisión@k.
    """
    seen: set[str] = set()
    out: list[str] = []
    for token in LOG_ID_RE.findall(text or ""):
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


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
    - IPs y fechas no cuentan como números (el lookaround descarta lo pegado
      a un punto, un guion o una barra: «10.10.97.1», «2026-01-10»,
      «31/12/2025»).
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
    """De lo citado, qué fracción es correcta (k = IDs distintos citados).

    Los IDs repetidos cuentan una sola vez: «EVT-1, EVT-1» es UNA evidencia
    correcta, no dos — sin deduplicar, repetir un ID inflaría (o hundiría)
    la precisión y el umbral ancho de 0.5 dejaría de significar algo.
    """
    citados = list(dict.fromkeys(cited))
    if not citados:
        return 0.0
    hits = sum(1 for token in citados if token in expected)
    return round(hits / len(citados), 2)


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


# ---------------------------------------------------------------------------
# Categoría `report` — scoring puro del informe ejecutivo (tarea 6)
# ---------------------------------------------------------------------------
# Pareja «Event ID … X»: el id justo tras las palabras clave. Con la forma
# «Event IDs dominantes: 4625 (102)» no casa (tras «IDs» viene una palabra),
# así que solo se evalúan afirmaciones reales de conteo por id.
_EVENT_ID_PAIR_RE = re.compile(r"\bevent[\s_]*id[s]?\s*(\d{2,})\b")

# Contrato estructural del informe: las 6 claves de `field_sources`. Son
# nombres de campo, no cifras de datos, por eso se fijan aquí.
REPORT_FIELDS = (
    "executive_summary",
    "methodology",
    "findings",
    "correlation",
    "conclusions",
    "action_plan",
)


def _level_pattern(label: str) -> str:
    """«Crítico» → `\\bcritic[oa]s?\\b`: género y número libres («Alto»/«Alta»),
    con frontera de palabra para no casar «criticismo» ni «alternativa»."""
    word = _deacc(label)
    stem = word[:-1] if word.endswith(("o", "a")) else word
    return rf"\b{re.escape(stem)}[oa]s?\b"


def level_count_near(text: str, label: str, count: int, window: int = _UNIT_WINDOW) -> bool:
    r"""¿Aparece `count` pegado a una mención del nivel `label`?

    Cubre «N … nivel Crítico» (la cifra antes de la etiqueta) y también
    «nivel Crítico: N» / «Crítico: N», la forma del fallback determinista,
    dentro de `window` chars.

    Necesaria porque `number_in_answer(unidades=...)` no sirve para la
    distribución: exige que el número sea el MÁS CERCANO de todo el texto a
    la unidad y «nivel» aparece muchas veces en un informe. Aquí basta con
    que LA cifra del nivel esté en la ventana de su mención.
    """
    body = _deacc(text)
    numbers = list(_NUMBER_RE.finditer(body))
    for label_match in re.finditer(_level_pattern(label), body):
        for n in numbers:
            antes = n.end() <= label_match.start() and label_match.start() - n.end() <= window
            despues = n.start() >= label_match.end() and n.start() - label_match.end() <= window
            if (antes or despues) and _number_value(n.group()) == count:
                return True
    return False


def eventid_pairs_bad(text: str, counts: dict[str, int],
                      window: int = _UNIT_WINDOW) -> list[str]:
    r"""Parejas «N … Event ID X» cuya cifra no es el conteo real de X.

    Segunda implementación INDEPENDIENTE de la regla B del validador de
    `ai_service` (el eval la cruza con ella): para cada «Event ID X», la
    cifra atribuida es la más cercana justo ANTES del id dentro de `window`
    chars. Sin cifra adyacente no hay afirmación que evaluar («Event ID
    conocidos de Windows Security» no declara un conteo), y los ids de OTRA
    pareja («… Event ID 4625 y 111 de Event ID 4624») no valen como cifra.
    X ausente del df también es motivo: su conteo no es verificable.
    Devuelve motivos legibles; [] = todas las parejas cuadran.
    """
    body = _deacc(text)
    id_matches = list(_EVENT_ID_PAIR_RE.finditer(body))
    # Spans que son el X de una pareja: no cuentan como cifra atribuida.
    etiquetas = {(m.start(1), m.end(1)) for m in id_matches}
    numbers = list(_NUMBER_RE.finditer(body))
    motivos: list[str] = []
    for m in id_matches:
        x = m.group(1)
        pos = m.start(1)
        previos = [n for n in numbers
                   if n.end() <= pos and pos - n.end() <= window
                   and (n.start(), n.end()) not in etiquetas]
        if not previos:
            continue  # sin cifra pegada: no es una afirmación de conteo
        cifra = max(previos, key=lambda n: n.end())
        valor = _number_value(cifra.group())
        if x not in counts:
            motivos.append(f"«{valor} … Event ID {x}»: el id no está en el df")
        elif valor != int(counts[x]):
            motivos.append(f"«{valor} … Event ID {x}» ≠ {int(counts[x])}")
    return motivos


def ip_cited(text: str, ips: list[str]) -> bool:
    """¿El texto cita alguna de las IPs reales del df?"""
    body = str(text or "")
    return any(ip and str(ip) in body for ip in ips)


def required_tokens(text: str, tokens: list[str]) -> list[str]:
    """De `tokens`, los que NO aparecen en el texto (mayúsculas/tildes irrelevantes)."""
    body = _deacc(text)
    return [token for token in tokens if _deacc(token) not in body]


def sentences_repeated(text: str) -> bool:
    """¿Hay una frase de ≥5 palabras normalizada repetida ≥2 veces?

    Normalización propia (minúsculas, sin puntuación, espacios colapsados):
    una enumeración corta («Nivel crítico. Nivel crítico.») no es
    redundancia. Implementación independiente de la regla D del validador.
    """
    counts: dict[str, int] = {}
    for part in re.split(r"[.;!?\n]+", str(text or "")):
        norm = " ".join(re.sub(r"[^\w\s]", " ", part.lower()).split())
        if norm and len(norm.split()) >= 5:
            counts[norm] = counts.get(norm, 0) + 1
    return any(times >= 2 for times in counts.values())


def field_sources_ok(payload: dict) -> bool:
    """¿`field_sources` trae las 6 claves del contrato con valor «llm»/«local»?"""
    if not isinstance(payload, dict):
        return False
    sources = payload.get("field_sources")
    if not isinstance(sources, dict):
        return False
    return all(
        isinstance(sources.get(campo), str) and sources[campo] in ("llm", "local")
        for campo in REPORT_FIELDS
    )
