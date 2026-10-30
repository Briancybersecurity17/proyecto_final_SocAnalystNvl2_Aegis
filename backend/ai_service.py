"""Asistente de IA para el análisis de datos y la redacción del informe.

Diseño (rigor analítico):
    El servicio recibe EXCLUSIVAMENTE datos ya calculados por el backend a
    partir de los registros importados (estadísticas + evidencias reales con
    Log_ID / Timestamp / Host / Event_ID). La IA redacta la narrativa, pero
    no puede aportar cifras ni hallazgos nuevos: todo queda anclado a los
    registros (grounding estricto, sin inventar conclusiones).

Dos modos de operación:
    1. LLM (opcional): la configuración vive en ``ai_config`` (panel «Opciones
       IA», persistido en disco): por defecto Ollama local con qwen2.5:3b vía
       ``/api/chat`` y opciones por request (num_ctx, temperature…); también
       acepta cualquier endpoint compatible con OpenAI (``AI_API_URL`` y cía.
       siguen aportando los defaults por entorno).
    2. Generador local determinista (fallback): si el LLM está apagado, no
       responde o la respuesta no es JSON válido, se redacta el informe con
       plantillas basadas en los mismos números. Nunca se queda sin informe.

Uso:
    from ai_service import generate_report_narrative
    narrative = generate_report_narrative(summary)
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("soc.ai")

# ---------------------------------------------------------------------------
# Configuración del LLM (panel «Opciones IA»)
# ---------------------------------------------------------------------------
# Fuente de verdad única: ai_config lee/valida el JSON en disco y arma el
# payload según el dialecto de la URL (Ollama nativo o compatible OpenAI).
# Los cambios del panel aplican al instante, sin reiniciar el backend.
try:
    from backend import ai_config
    from backend import feedback_store
except ImportError:  # pragma: no cover – módulo suelto (sys.path con backend/)
    import ai_config
    import feedback_store

LEVEL_ORDER = ["critico", "alto", "medio", "bajo"]
LEVEL_LABELS = {
    "critico": "Crítico",
    "alto": "Alto",
    "medio": "Medio",
    "bajo": "Bajo",
}
LEVEL_SEVERITY_HINT = {
    "critico": "impacto inmediato sobre la confidencialidad/integridad del entorno",
    "alto": "actividad de riesgo elevado que exige revisión prioritaria",
    "medio": "actividad que requiere monitoreo y correlación continua",
    "bajo": "actividad informativa dentro de la línea base",
}

SYSTEM_PROMPT = (
    "Eres un analista SOC de nivel 2 redactando un informe técnico en español. "
    "Trabajas SOLO con los datos que se te entregan: si un dato no está en el "
    "contexto, no lo menciones ni lo infieras. Nunca inventes hosts, IPs, "
    "fechas, cifras ni eventos que no aparezcan en los datos. Cita siempre las "
    "evidencias (Log_ID / Host / Event_ID) disponibles. Redacción formal, "
    "concisa y verificable. Respondes ÚNICAMENTE con un objeto JSON válido."
)

# Claves que la respuesta del modelo debe contener (se rellenan con el
# fallback local si faltan).
NARRATIVE_KEYS = [
    "executive_summary",
    "methodology",
    "correlation",
    "conclusions",
    "findings",
    "action_plan",
]


# ---------------------------------------------------------------------------
# Contexto: todo lo que la IA puede "ver" (solo datos calculados)
# ---------------------------------------------------------------------------
def _result_text(value: Any) -> str:
    """Texto del campo Result: «404.0» (float por celdas vacías) → «404»."""
    if value is None:
        return "—"
    if isinstance(value, float) and value != value:  # NaN
        return "—"
    text = str(value).strip()
    if re.fullmatch(r"-?\d+\.0+", text):
        text = text.split(".", 1)[0]
    return text or "—"


def _evidence(example: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza un registro de ejemplo al formato de evidencia del informe."""
    return {
        "log_id": str(example.get("Log_ID", "") or "—"),
        "timestamp": str(example.get("Timestamp", "") or "—"),
        "host": str(example.get("Hostname", "") or "—"),
        "event_id": str(example.get("Event_ID", "") or "—"),
        "user": str(example.get("User", "") or "—"),
        "source_ip": str(example.get("Source_IP", "") or "—"),
        "description": str(example.get("Event_Description", "") or "—"),
        "severity": str(example.get("Severity", "") or "—"),
        # Resultado de la operación (Success/Failure/Allowed o código HTTP)
        "result": _result_text(example.get("Result")),
        "reason": str(example.get("_reason", example.get("risk_reason", "")) or "—"),
    }


def build_context(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Extrae del resumen SOLO la información que el informe puede citar."""
    risk_levels: Dict[str, Any] = {}
    for lvl in LEVEL_ORDER:
        block = (summary.get("risk_levels") or {}).get(lvl) or {}
        risk_levels[lvl] = {
            "label": LEVEL_LABELS[lvl],
            "count": int(block.get("count", 0)),
            "percentage": float(block.get("percentage", 0.0)),
            "justification": block.get("justification", ""),
            "evidence": [_evidence(e) for e in (block.get("examples") or [])[:4]],
        }

    risk = str(summary.get("risk", "bajo"))
    return {
        "file_name": summary.get("file_name") or "archivo importado",
        "sheet_names": summary.get("sheet_names") or [],
        "missing_columns": summary.get("missing_columns") or [],
        "generated_at": summary.get("generated_at") or datetime.now().isoformat(timespec="seconds"),
        "rows": int(summary.get("rows", 0)),
        "columns": summary.get("columns") or [],
        "unique_hosts": summary.get("unique_hosts"),
        "events_per_day": summary.get("events_per_day") or {},
        "top_hosts": summary.get("top_hosts") or {},
        "risk": risk,
        "risk_label": LEVEL_LABELS.get(risk, risk),
        "risk_justification": summary.get("risk_justification", ""),
        "level_counts": summary.get("level_counts") or {},
        # Top-20 de Event_ID con su conteo global (claves como texto): lo
        # exigen la regla B («N … Event ID X») y el prompt. Sin esta entrada
        # de la whitelist nunca llegaría ni al prompt ni al validador.
        "event_id_counts": summary.get("event_id_counts", {}) or {},
        # Correspondencia auditable Severity original → nivel: la metodología
        # debe citar una clave original (regla C), solo si el mapping no
        # está vacío.
        "severity_mapping": summary.get("severity_mapping", {}) or {},
        # Éxito/fallo o código HTTP por registro (campo Result del archivo)
        "result_counts": summary.get("result_counts") or {},
        "risk_levels": risk_levels,
        "non_numeric_event_ids": summary.get("non_numeric_event_ids") or [],
        "suspicious_events": int(summary.get("suspicious_events", 0)),
        "suspicious_by_ip": summary.get("suspicious_by_ip") or [],
        "suspicious_evidence": [
            _evidence(e) for e in (summary.get("suspicious_details") or [])[:6]
        ],
        # Registros con respuesta HTTP de error (4xx/5xx) en el campo Result:
        # sirven de evidencia al hallazgo «respuestas HTTP de error».
        "result_errors": {
            "codes": (summary.get("http_errors") or {}).get("codes") or {},
            "hosts": (summary.get("http_errors") or {}).get("hosts") or {},
            "levels": (summary.get("http_errors") or {}).get("levels") or {},
            "severities": (summary.get("http_errors") or {}).get("severities") or {},
            "total": int((summary.get("http_errors") or {}).get("total") or 0),
            "examples": [
                _evidence(e) for e in (summary.get("http_errors") or {}).get("examples") or []
            ],
        },
    }


# ---------------------------------------------------------------------------
# Fallback local determinista
# ---------------------------------------------------------------------------
def _pct(count: int, total: int) -> str:
    return f"{(count / total) * 100:.1f}%" if total else "0,0%"


# Significado de referencia de los códigos HTTP citados en el informe
# (conocimiento del marco, no una afirmación sobre los registros).
_HTTP_CODE_MEANINGS = {
    "400": "solicitud mal formada (bad request)",
    "401": "autenticación requerida",
    "403": "acceso prohibido",
    "404": "recurso solicitado no existe o no fue encontrado",
    "408": "tiempo de espera de la solicitud agotado",
    "429": "demasiadas solicitudes (rate limit)",
    "500": "error interno del servidor",
    "502": "puerta de enlace inválida (bad gateway)",
    "503": "servicio no disponible",
    "504": "tiempo de espera de la puerta de enlace agotado",
}


def _http_code_meaning(code: str) -> str:
    """Referencia de significado para un código HTTP concreto."""
    known = _HTTP_CODE_MEANINGS.get(code)
    if known:
        return known
    return "error del lado del cliente (4xx)" if code.startswith("4") else "error del lado del servidor (5xx)"


def _fallback_narrative(ctx: Dict[str, Any]) -> Dict[str, Any]:
    total = ctx["rows"]
    levels = ctx["risk_levels"]
    hosts = ctx["unique_hosts"] if ctx["unique_hosts"] is not None else "n/d"
    sheets = ", ".join(ctx["sheet_names"]) if ctx["sheet_names"] else "n/d"
    highest = next((l for l in LEVEL_ORDER if levels[l]["count"] > 0), None)
    highest_label = LEVEL_LABELS[highest] if highest else "—"

    breakdown = " · ".join(
        f"{LEVEL_LABELS[l]}: {levels[l]['count']} ({levels[l]['percentage']}%)"
        for l in LEVEL_ORDER
    )

    # --- Resumen ejecutivo -------------------------------------------------
    executive = (
        f"Se analizaron {total} registros de seguridad del archivo "
        f"«{ctx['file_name']}» (hojas: {sheets}), correspondientes a {hosts} hosts "
        f"y {len(ctx['columns'])} campos. El nivel de riesgo global asignado es "
        f"«{highest_label}». Distribución por criticidad — {breakdown}. "
        f"El motor detectó {ctx['suspicious_events']} eventos sospechosos "
        f"({_pct(ctx['suspicious_events'], total)} del total). "
        f"Justificación global: {ctx['risk_justification']}"
    )

    # --- Metodología -------------------------------------------------------
    methodology = (
        "1) Importación del archivo Excel (.xlsx) con lectura de todas las hojas y "
        "validación de columnas esperadas. "
        "2) Normalización de la severidad original del archivo a la escala de cuatro "
        "niveles (Crítico/Alto/Medio/Bajo) según la política de niveles de la "
        "organización: Warning → Crítico, High → Alto, Medium → Medio e "
        "Informational/Low → Bajo. El valor original se conserva sin alterar en la "
        "tabla de datos y en el gráfico de valores originales. "
        "3) Identificación de eventos sospechosos mediante reglas deterministas sobre "
        "los propios campos: severidad elevada, palabras clave en Event_Description "
        "(failed, unauthorized, denied, lockout…) y Event ID conocidos de Windows "
        "Security (4625, 4648, 4740, 7045…). "
        "4) Correlación entre IP de origen, hosts y eventos en el tiempo. "
        "5) Redacción del informe a partir exclusivamente de los datos calculados; "
        "ninguna cifra proviene de fuera de los registros analizados."
    )

    # --- Hallazgos ---------------------------------------------------------
    findings: List[Dict[str, Any]] = []

    if highest and levels[highest]["count"] > 0:
        block = levels[highest]
        findings.append(
            {
                "id": "H-01",
                "title": f"Registros clasificados en nivel {block['label']}",
                "level": highest,
                "explanation": (
                    f"{block['count']} de {total} registros ({block['percentage']}%) "
                    f"quedaron en el nivel «{block['label']}», que corresponde a "
                    f"{LEVEL_SEVERITY_HINT[highest]}. {block['justification']}"
                ),
                "evidence": block["evidence"],
            }
        )

    if ctx["suspicious_events"] > 0:
        top_ips = ctx["suspicious_by_ip"][:3]
        ip_txt = ", ".join(
            f"{i['ip']} → {i['hosts']} hosts / {i['events']} eventos" for i in top_ips
        ) or "n/d"
        findings.append(
            {
                "id": "H-02",
                "title": "Eventos sospechosos identificados",
                "level": highest or "medio",
                "explanation": (
                    f"Se detectaron {ctx['suspicious_events']} eventos sospechosos "
                    f"({_pct(ctx['suspicious_events'], total)} del total) por combinación "
                    f"de severidad, palabras clave de fallo de autenticación y Event ID. "
                    f"Correlación por IP de origen: {ip_txt}."
                ),
                "evidence": ctx["suspicious_evidence"][:4],
            }
        )

    # Respuestas HTTP de error (4xx/5xx) en el campo Result: hallazgo con las
    # evidencias de esos registros concretos (Log_ID, Timestamp, Severity).
    errors = ctx.get("result_errors") or {}
    error_codes_txt = ""
    error_hosts_txt = ""
    if int(errors.get("total") or 0) > 0:
        codes = errors.get("codes") or {}
        error_codes_txt = ", ".join(
            f"{code} ×{n}" for code, n in sorted(codes.items(), key=lambda kv: -kv[1])
        )
        code_meanings = "; ".join(
            f"{code} → {_http_code_meaning(code)}" for code in sorted(codes)
        )
        err_hosts = errors.get("hosts") or {}
        error_hosts_txt = ", ".join(f"{h} ({n})" for h, n in list(err_hosts.items())[:4])
        sev_err = errors.get("severities") or {}
        sev_txt = ", ".join(f"{s} ×{n}" for s, n in sorted(sev_err.items(), key=lambda kv: -kv[1]))
        lvl_err = errors.get("levels") or {}
        lvl_key = next((l for l in LEVEL_ORDER if int(lvl_err.get(l, 0) or 0) > 0), None)
        findings.append(
            {
                "id": f"H-{len(findings) + 1:02d}",
                "title": "Respuestas HTTP de error en los registros (4xx/5xx)",
                "level": lvl_key or "medio",
                "explanation": (
                    f"{errors['total']} de {total} registros ({_pct(int(errors['total']), total)}) "
                    f"tienen en el campo Result un código de respuesta HTTP de error: {error_codes_txt}. "
                    f"Se concentran en {error_hosts_txt or 'n/d'} y su severidad original es "
                    f"{sev_txt or 'n/d'}"
                    + (
                        f", que la política de clasificación mapea al nivel "
                        f"«{LEVEL_LABELS.get(lvl_key or '', '')}»."
                        if lvl_key
                        else "."
                    )
                    + f" Referencia técnica del marco HTTP (no es un dato del archivo): {code_meanings}. "
                    "Corresponde revisar el servicio que genera esos códigos (rutas solicitadas y "
                    "logs de aplicación) sin salirse de lo observado en el archivo."
                ),
                "evidence": (errors.get("examples") or [])[:6],
            }
        )

    medio = levels["medio"]
    if medio["count"] > 0:
        findings.append(
            {
                "id": f"H-{len(findings) + 1:02d}",
                "title": "Volumen de eventos en nivel Medio",
                "level": "medio",
                "explanation": (
                    f"{medio['count']} registros ({medio['percentage']}%) permanecen en "
                    f"nivel Medio: {LEVEL_SEVERITY_HINT['medio']}. {medio['justification']}"
                ),
                "evidence": medio["evidence"][:2],
            }
        )

    if ctx["top_hosts"]:
        hosts_txt = ", ".join(
            f"{h} ({c})" for h, c in list(ctx["top_hosts"].items())[:4]
        )
        findings.append(
            {
                "id": f"H-{len(findings) + 1:02d}",
                "title": "Concentración de eventos por host",
                "level": "medio" if levels["medio"]["count"] else "bajo",
                "explanation": (
                    f"Los hosts con mayor volumen de eventos son: {hosts_txt}. "
                    f"La concentración ayuda a priorizar la revisión de esos activos."
                ),
                "evidence": [],
            }
        )

    # --- Correlación -------------------------------------------------------
    if ctx["suspicious_by_ip"]:
        top = ctx["suspicious_by_ip"][0]
        correlation = (
            f"La IP de origen con mayor alcance es {top['ip']}: {top['events']} eventos "
            f"sospechosos distribuidos en {top['hosts']} hosts distintos. Alcanzar varios "
            f"hosts desde un mismo origen es un indicador de barrido o movimiento lateral "
            f"y debe validarse contra los registros de autenticación de esos activos. "
            f"Top de IPs: " + "; ".join(
                f"{i['ip']} ({i['hosts']} hosts / {i['events']} eventos)"
                for i in ctx["suspicious_by_ip"][:5]
            ) + "."
        )
    else:
        correlation = (
            "No se detectaron correlaciones entre IP de origen y múltiples hosts en los "
            "registros analizados."
        )

    # --- Plan de acción ----------------------------------------------------
    action_plan: List[Dict[str, Any]] = [
        {
            "priority": "P1",
            "action": "Revisar los registros del nivel más alto y validar su alcance",
            "rationale": (
                f"{levels[highest]['count']} registros en nivel "
                f"«{levels[highest]['label']}» ({levels[highest]['percentage']}%) "
                f"con justificación: {levels[highest]['justification']}"
            )
            if highest
            else "Sin registros en niveles elevados.",
        },
        {
            "priority": "P1",
            "action": "Auditar las IPs de origen con mayor número de hosts impactados",
            "rationale": (
                "; ".join(
                    f"{i['ip']}: {i['events']} eventos sospechosos en {i['hosts']} hosts"
                    for i in ctx["suspicious_by_ip"][:3]
                )
                or "Sin correlaciones IP→hosts relevantes."
            ),
        },
        {
            "priority": "P2",
            "action": "Revisar los hosts con mayor volumen de eventos sospechosos",
            "rationale": (
                "Hosts priorizados por cantidad de eventos: "
                + ", ".join(f"{h} ({c})" for h, c in list(ctx["top_hosts"].items())[:5])
                + "."
            ),
        },
        {
            "priority": "P3",
            "action": "Mantener monitoreo continuo sobre el nivel Medio/Bajo",
            "rationale": (
                f"{levels['medio']['count']} registros en Medio y "
                f"{levels['bajo']['count']} en Bajo ({levels['bajo']['percentage']}% del "
                f"total) requieren línea base y alertas de variación."
            ),
        },
        {
            "priority": "P4",
            "action": "Documentar y re-ejecutar el análisis tras cada incorporación de datos",
            "rationale": (
                f"Archivo «{ctx['file_name']}» con {total} registros y {len(ctx['columns'])} "
                f"campos; re-ejecutar para verificar la evolución de la criticidad."
            ),
        },
    ]

    # Acción específica para los errores HTTP, solo si el archivo los contiene
    if error_codes_txt:
        action_plan.append(
            {
                "priority": "P2",
                "action": "Revisar los registros con respuesta HTTP de error (4xx/5xx)",
                "rationale": (
                    f"{errors['total']} registros con {error_codes_txt} en {error_hosts_txt or 'n/d'}; "
                    "inspeccionar el servicio web de origen y las rutas solicitadas en esos eventos."
                ),
            }
        )

    # --- Conclusiones ------------------------------------------------------
    conclusions = (
        f"El archivo «{ctx['file_name']}» presenta un riesgo global «{highest_label}» "
        f"sobre {total} registros en {hosts} hosts. {breakdown}. "
        f"Se identificaron {ctx['suspicious_events']} eventos sospechosos y "
        f"{len([f for f in findings])} hallazgos fundamentados en los propios registros. "
        f"Toda conclusión de este informe es reproducible a partir de los datos "
        f"importados; no se incorporaron evidencias externas a los registros analizados."
    )

    return {
        "executive_summary": executive,
        "methodology": methodology,
        "correlation": correlation,
        "conclusions": conclusions,
        "findings": findings,
        "action_plan": action_plan,
        "source": "local",
        "model": "motor local determinista (grounding en los registros)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Modelo remoto (opcional)
# ---------------------------------------------------------------------------
def _clean_json(text: str) -> Optional[Dict[str, Any]]:
    """Acepta JSON puro o JSON envuelto en ```json ... ```."""
    if not text:
        return None
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.S | re.I)
    if fence:
        candidate = fence.group(1).strip()
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            return None
        candidate = candidate[start : end + 1]
    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# Presupuesto de generación SOLO para la llamada del informe: los demás usos
# de num_predict no se tocan. El informe es largo: con el tope del chat (512)
# el JSON llegaba truncado y se descartaba. Contingencia documentada, no
# activa: si en la corrida de aceptación el JSON llega truncado, subir a 12288.
_REPORT_NUM_PREDICT = 1600

# User prompt endurecido en 4 bloques, alineado con las reglas A–D del
# validador (bloque 2 = regla C campo a campo; bloque 3 = reglas A/B/D y
# honestidad de la fuente). Solo pide los 4 campos de prosa: findings y
# action_plan los genera el sistema.
_REPORT_USER_PROMPT = (
    "Redacta la prosa del informe técnico a partir de estos datos calculados.\n"
    "Responde SOLO con JSON válido con exactamente estas 4 claves (cada valor, string):\n"
    '  "executive_summary": string,\n'
    '  "methodology": string,\n'
    '  "correlation": string,\n'
    '  "conclusions": string\n'
    "\n"
    "1) ALCANCE. Redactas únicamente executive_summary, methodology, correlation y "
    "conclusions. Los hallazgos (findings) y el plan de acción (action_plan) los "
    "genera el sistema: NO los emitas, no emitas ningún campo «id» ni ninguna otra "
    "clave fuera de las 4 pedidas.\n"
    "\n"
    "2) ESTRUCTURA POR CAMPO (lo exige el validador; complételo campo a campo):\n"
    "  - executive_summary: los «N registros» del total EN LA MISMA FRASE, la "
    "etiqueta de riesgo del ctx tal como aparece en DATOS, la distribución "
    "completa de niveles (cada nivel con count > 0 nombrado de forma concreta con "
    "«nivel» + su nombre, sin repetir frases) y el total de eventos sospechosos.\n"
    "  - methodology: pasos numerados que cubran la importación del archivo, la "
    "clasificación por severidad y las reglas de sospecha, citando al menos una "
    "clave original de severity_mapping tal como aparece en DATOS (su valor "
    "«raw» literal, sin traducir).\n"
    "  - correlation: la PRIMERA IP de suspicious_by_ip con SU conteo de eventos, "
    "tal como figura en DATOS.\n"
    "  - conclusions: etiqueta de riesgo, total de registros, distribución completa "
    "de niveles y la reproducibilidad (todo procede de los datos importados; nada "
    "externo a ellos).\n"
    "\n"
    "3) REGLAS DURAS:\n"
    "  - Todo número sale de los DATOS tal cual: prohibido recalcular, aproximar o "
    "reasignar una cifra a otro sustantivo (LO PROHIBIDO), con el ejemplo literal "
    "(«97» es una cifra arbitraria): «97 registros de Event ID 4625» — el total de "
    "registros pegado al Event ID 4625.\n"
    "  - «N de Event ID X» solo si N es exactamente event_id_counts[X].\n"
    "  - Sin frases repetidas: ninguna frase de 5 palabras o más aparece dos veces.\n"
    "  - Sin inventar tooling: es un archivo importado, no un SIEM en vivo; no "
    "menciones consultas, agentes ni consolas que no consten en DATOS.\n"
    "  - Español formal y verificable; si un dato no está en DATOS, no lo "
    "menciones ni lo infieras.\n"
    "\n"
    "4) FORMATO: SOLO JSON válido, exactamente las 4 claves anteriores, sin "
    "markdown y sin texto adicional.\n"
    "\n"
    "DATOS:\n"
)


async def _remote_narrative(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """LLM para el informe. Devuelve None si está apagado o falla."""
    cfg = ai_config.get()
    if not cfg["enabled"] or not cfg["api_url"]:
        return None

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _REPORT_USER_PROMPT
            + json.dumps(ctx, ensure_ascii=False, default=str),
        },
    ]

    text = await ai_config.chat(
        messages,
        temperature=0.2,
        # El informe es largo: con el tope del chat (512) el JSON llega
        # truncado y se descarta. Para SOLO esta llamada se aplica el suelo
        # _REPORT_NUM_PREDICT (sin mutar lo guardado en el panel); -1 =
        # sin límite.
        cfg={
            **cfg,
            "num_ctx": max(cfg["num_ctx"], 8192),
        },
        num_predict=(
            -1 if cfg["num_predict"] == -1
            else max(cfg["num_predict"], _REPORT_NUM_PREDICT)
        ),
    )
    if not text:
        return None
    parsed = _clean_json(text)
    if parsed is None:
        logger.warning(
            "El LLM no devolvió JSON válido para el informe (¿truncado?): %.200s", text
        )
    return parsed


# Campos de prosa que el LLM puede aportar (los únicos que pide el prompt y
# que el validador A–D evalúa) y los 6 campos del contrato del informe.
_PROSE_FIELDS = ("executive_summary", "methodology", "correlation", "conclusions")
_CAMPOS_INFORME = (
    "executive_summary",
    "methodology",
    "findings",
    "correlation",
    "conclusions",
    "action_plan",
)


def _merge(fallback: Dict[str, Any], aceptados: Dict[str, str]) -> Dict[str, Any]:
    """Overlay campo a campo: solo la prosa VALIDADA reemplaza al fallback.

    Siempre produce los 6 campos del informe. `findings` y `action_plan` salen
    del fallback POR CONSTRUCCIÓN: aunque `aceptados` los traiga (el prompt no
    los pide y el validador los descarta), no se pisan — así mueren los
    problemas de hallazgos/plan inventados. `field_sources` registra de dónde
    salió cada campo: "llm" solo si el validador aceptó ese campo de prosa,
    "local" en cualquier otro caso (siempre local en findings/action_plan).
    `source` y `model` los decide `generate_report_narrative`.
    """
    out = {c: fallback[c] for c in _CAMPOS_INFORME}
    for campo in _PROSE_FIELDS:
        value = aceptados.get(campo)
        if isinstance(value, str) and value.strip():
            out[campo] = value
    out["field_sources"] = {
        campo: (
            "llm"
            if campo in _PROSE_FIELDS and isinstance(aceptados.get(campo), str)
            and aceptados[campo].strip()
            else "local"
        )
        for campo in _CAMPOS_INFORME
    }
    # Claves del fallback que no se tocan: source, model, generated_at.
    for key, value in fallback.items():
        out.setdefault(key, value)
    return out


# ---------------------------------------------------------------------------
# Validación de la narrativa remota (reglas A–D)
# ---------------------------------------------------------------------------
# Reglas puras (sin red) que el validador del informe (tarea 5) compondrá:
# cada una devuelve una lista de motivos legibles de rechazo; [] = pasa.

_NUM_RE = re.compile(r"(?<![\w.\-/])(\d{1,3}(?:[.,]\d{3})+|\d+)(?:\s*%|\b)(?![\w-])")
# Un punto seguido de dígito o una barra JUSTO DESPUÉS del número marcan una
# IP o una fecha («10.10.2.15», «31/12/2025»); el lookaround de _NUM_RE ya
# bloquea lo pegado a la izquierda y los guiones a la derecha («2026-01-10»).
# El punto de cierre de frase («…Event ID 4625.») no lleva dígito tras el
# punto, así que esos números sí cuentan.
_PART_RE = re.compile(r"\.(?=\d)|/")
# Literales de porcentaje con coma o punto decimal («33,6 %», «33.6%»).
_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_PCT_TOL = 0.1


def _parse_number(raw: str) -> float:
    """«1.234» / «1,234» / «1234» → 1234.0 (el separador siempre es de miles)."""
    cleaned = raw.replace(".", "").replace(",", "") if len(raw) > 3 else raw
    return float(cleaned)


def _number_spans(text: str) -> List[Tuple[int, str, float]]:
    """(posición, texto crudo, valor) de cada número real del texto.

    El lookaround descarta lo pegado a punto/guion/barra (IPs, fechas), igual
    que `scoring.number_in_answer`; _PART_RE cubre el resto del lado derecho.
    """
    found: List[Tuple[int, str, float]] = []
    for m in _NUM_RE.finditer(text):
        if _PART_RE.match(text, m.end()):
            continue  # octeto de IP o parte de fecha, no una cifra suelta
        raw = m.group(1)
        found.append((m.start(1), raw, _parse_number(raw)))
    return found


def _numbers(text: str) -> List[Tuple[str, float]]:
    """Todas las apariciones de números: (texto crudo, valor float)."""
    return [(raw, value) for _pos, raw, value in _number_spans(text)]


def _anchors(ctx: Any) -> set[float]:
    """Cifras anclables del contexto: barrido recursivo (dicts/listas/scalars).

    Se anclan todos los enteros de conteos y los strings ENTERAMENTE
    numéricos (Event_ID «4625», código HTTP «404»). Las IP («10.10.2.15») no
    casan con \\d+ y sus octetos no se anclan.
    """
    found: set[float] = set()
    if isinstance(ctx, dict):
        for key, value in ctx.items():
            # Las claves también cuentan: códigos HTTP y Event_ID viven como
            # claves de conteo («404»: 30, «4625»: 4).
            if isinstance(key, str) and re.fullmatch(r"\d+", key.strip()):
                found.add(float(key))
            found |= _anchors(value)
    elif isinstance(ctx, (list, tuple, set)):
        for value in ctx:
            found |= _anchors(value)
    elif isinstance(ctx, bool):
        pass  # bool es int en Python: True/False no son cifras del informe
    elif isinstance(ctx, int):
        found.add(float(ctx))
    elif isinstance(ctx, str) and re.fullmatch(r"\d+", ctx.strip()):
        found.add(float(ctx))
    return found


def _percentages(ctx: Any) -> List[float]:
    """Porcentajes derivables del contexto (comparar con tolerancia ±0,1).

    Incluye `level_counts[l]/rows*100`, `suspicious_events/rows*100` y
    cualquier `percentage` ya presente en el ctx (p. ej. risk_levels).
    """
    found: List[float] = []
    if isinstance(ctx, dict):
        rows = ctx.get("rows")
        if isinstance(rows, (int, float)) and not isinstance(rows, bool) and rows:
            for count in (ctx.get("level_counts") or {}).values():
                if isinstance(count, (int, float)) and not isinstance(count, bool):
                    found.append(count / rows * 100)
            susp = ctx.get("suspicious_events")
            if isinstance(susp, (int, float)) and not isinstance(susp, bool):
                found.append(susp / rows * 100)
        for key, value in ctx.items():
            if key == "percentage" and isinstance(value, (int, float)) and not isinstance(value, bool):
                found.append(float(value))
            else:
                found.extend(_percentages(value))
    elif isinstance(ctx, (list, tuple)):
        for value in ctx:
            found.extend(_percentages(value))
    return found


def _rule_anchor(text: str, ctx: dict) -> List[str]:
    """Regla A: cada cifra del texto debe estar anclada al contexto.

    Un número seguido de % se contrasta con `_percentages` (±0,1); el resto
    son enteros y deben estar en `_anchors`. Los porcentajes con coma decimal
    («33,6 %») también son válidos: el literal del % se parsea completo y se
    retira del texto antes de revisar enteros (así «33» de «33,6» no se
    exige como ancla suelta).
    """
    reasons: List[str] = []
    pcts = _percentages(ctx)
    for m in _PCT_RE.finditer(text):
        value = float(m.group(1).replace(",", "."))
        if not any(abs(value - p) <= _PCT_TOL for p in pcts):
            reasons.append(f"porcentaje {m.group(1)}% sin ancla")
    anchors = _anchors(ctx)
    for raw, value in _numbers(_PCT_RE.sub(" ", text)):
        if value not in anchors:
            reasons.append(f"número {raw} sin ancla")
    return reasons


def _rule_redundancy(text: str) -> List[str]:
    """Regla D: una frase normalizada repetida ≥2 veces con ≥5 palabras.

    Frases cortas (<5 palabras) no cuentan: una enumeración («Nivel crítico.»
    repetido) no es redundancia. El motivo cita la frase tal como se compara.
    """
    counts: Dict[str, int] = {}
    for part in re.split(r"[.;\n]+", text):
        norm = " ".join(re.sub(r"[^\w\s]", " ", part.lower()).split())
        if norm:
            counts[norm] = counts.get(norm, 0) + 1
    return [
        f"frase repetida: «{norm}»"
        for norm, times in counts.items()
        if times >= 2 and len(norm.split()) >= 5
    ]


# --- Regla B: atribución número ↔ etiqueta --------------------------------
# Ventana de atribución: la etiqueta debe caer a ≤40 chars tras el número.
_WINDOW = 40
# Minúsculas sin tildes PRESERVANDO posiciones (las ventanas se miden sobre
# el texto original); a diferencia de `_norm_text` (NFD), la tabla es 1:1.
_DEACC_TABLE = str.maketrans("áéíóúüñ", "aeiouun")

_EVENT_ID_RE = re.compile(r"event[\s_]+id\s+(\d+)")
_SUSPICIOUS_RE = re.compile(r"sospechos[oa]s?")
# \b inicial evita que «terror» suene a «error»; «http» va pegado a la palabra.
_ERRORS_RE = re.compile(r"\berror(?:es)?\s*(?:http\b)?")
# Códigos HTTP 4xx/5xx citados como palabra («404», «500»).
_HTTP_CODE_RE = re.compile(r"\b[45]\d{2}\b")
# Co-ocurrencia exigida para atribuir «N … errores» a errores HTTP: junto a la
# mención debe haber «http» o un código 4xx/5xx (ventana ±_WINDOW). Sin esto,
# «12 registros con error humano» se reclamaba como http_errors.total (falso
# positivo corregido en la tarea 5, ruling del controller).
_NEAR_HTTP_RE = re.compile(r"\bhttp\b|\b[45]\d{2}\b")


def _norm_pos(text: str) -> str:
    """Minúsculas y sin tildes, con las posiciones intactas (para ventanas)."""
    return (text or "").lower().translate(_DEACC_TABLE)


def _label_matches(norm: str) -> List[Tuple[int, str]]:
    """(posición, etiqueta_canónica) de cada etiqueta atribuible del texto.

    Etiquetas: `event_id:X`, `nivel:L`, `sospechosos`, `errores http` y
    `codigo:C`. Los patrones de nivel son los de `_LEVEL_PATTERNS` (definido
    más abajo, en el bloque de chat: se resuelven en tiempo de ejecución).
    """
    found: List[Tuple[int, str]] = []
    for m in _EVENT_ID_RE.finditer(norm):
        found.append((m.start(), f"event_id:{m.group(1)}"))
    for lvl in LEVEL_ORDER:
        for m in re.finditer(rf"nivel\s+({_LEVEL_PATTERNS[lvl]})", norm):
            found.append((m.start(), f"nivel:{lvl}"))
    for m in _SUSPICIOUS_RE.finditer(norm):
        found.append((m.start(), "sospechosos"))
    for m in _ERRORS_RE.finditer(norm):
        # Solo es «errores HTTP» si junto a la mención hay «http» o un código
        # 4xx/5xx: «12 registros con error humano» no atribuye nada a nadie.
        ventana = norm[max(0, m.start() - _WINDOW): m.end() + _WINDOW]
        if _NEAR_HTTP_RE.search(ventana):
            found.append((m.start(), "errores http"))
    for m in _HTTP_CODE_RE.finditer(norm):
        found.append((m.start(), f"codigo:{m.group()}"))
    return found


def _pairs(text: str) -> List[Tuple[float, str]]:
    """(número, etiqueta) con etiqueta en ventana ≤40 chars tras el número.

    Los literales de % se retiran primero (igual que en la regla A): «33,9 %
    del nivel crítico» no es una atribución del 33 ni del 9 al nivel. Si dos
    números compiten por la misma etiqueta, queda el más cercano (criterio de
    `number_in_answer` con unidades); un número que YA es un código HTTP no
    reclama otro código («los códigos 404 y 500» enumera, no atribuye).
    """
    norm = _PCT_RE.sub(" ", _norm_pos(text))
    labels = _label_matches(norm)
    best: Dict[int, Tuple[int, float, str]] = {}  # por posición de etiqueta
    for pos, raw, value in _number_spans(norm):
        for lpos, tag in labels:
            dist = lpos - (pos + len(raw))
            if not 0 <= dist <= _WINDOW:
                continue
            if tag.startswith("codigo:") and re.fullmatch(r"[45]\d{2}", raw):
                continue  # «404 y 500» son los códigos, no la cifra atribuida
            if lpos not in best or dist < best[lpos][0]:
                best[lpos] = (dist, value, tag)
    return [
        (best[lpos][1], best[lpos][2]) for lpos in sorted(best)
    ]


def _num(value: Any) -> str:
    """Cifra legible en los motivos: 97.0 → «97», 33.9 → «33,9»."""
    number = float(value)
    return str(int(number)) if number == int(number) else f"{number:.1f}".replace(".", ",")


def _http_errors(ctx: dict) -> dict:
    """Bloque de errores HTTP: el brief lo llama `http_errors` y el ctx real
    `result_errors` (mismas claves total/codes); se aceptan los dos nombres."""
    return ctx.get("http_errors") or ctx.get("result_errors") or {}


# Fin de frase para acotar la atribución: corridas de «.!?;» seguidas de
# espacio (o fin de texto) y saltos de línea. Los puntos de IP y los
# decimales («10.10.2.15», «33.9») no separan: exigen blanco tras la raya.
_SENT_END_RE = re.compile(r"[.!?;]+(?=\s|$)|\n+")


def _frase_en(text: str, pos: int) -> Tuple[int, int]:
    """[inicio, fin) de la frase que contiene la posición `pos`."""
    inicio = 0
    fin = len(text)
    for m in _SENT_END_RE.finditer(text):
        if m.end() <= pos:
            inicio = m.end()
        elif m.start() >= pos:
            fin = m.start()
            break
    return inicio, fin


def _sospechosos_de_ip_mencionada(text: str, value: float, ctx: dict) -> bool:
    """¿`value` es el conteo REAL de la IP a la que la frase lo atribuye?

    «La IP 10.10.2.15 ha generado 15 eventos sospechosos» es CORRECTO aunque
    15 ≠ 174 (el total global): la cifra se atribuye a la IP, no al total.
    Evidencia de la tarea 8 (aceptación): esa frase, verificada contra
    `suspicious_by_ip` del fixture (10.10.2.15 → 15), era rechazada por la
    regla B al resolver la etiqueta «sospechosos» contra el total global.

    La atribución se acota para no abrir la clase de fallo histórica (#1,
    misatribución entre varias IPs citadas). La cifra se respalda solo si:
    - la IP coincide con límites de token en el texto («10.10.2.1» NO vale
      dentro de «10.10.2.15»);
    - la IP más cercana a ESA aparición de `value` DENTRO DE LA MISMA FRASE
      es la que tiene `events == value`: que otra IP citada en el texto (en
      otra frase o más lejos) tenga ese conteo no basta —«…10.10.2.15 y
      10.10.1.44… La IP 10.10.2.15 ha generado 27 eventos…» sigue
      rechazándose cuando el 27 es el conteo de 10.10.1.44—;
    - el ctx trae `suspicious_by_ip` con esa IP: sin el bloque (o sin IPs
      en la frase) la función degrada a False y rige el total global.
    """
    entradas = [
        e for e in (ctx.get("suspicious_by_ip") or [])
        if isinstance(e, dict) and str(e.get("ip") or "")
    ]
    if not entradas:
        return False
    # Ocurrencias reales de la cifra (los octetos de IP no cuentan).
    cifras = [
        (pos, pos + len(raw))
        for pos, raw, num in _number_spans(text)
        if num == value
    ]
    if not cifras:
        return False
    # Ocurrencias de cada IP del ctx con límites de token.
    ocurrencias: List[Tuple[int, int, float]] = []
    for entrada in entradas:
        ip = str(entrada["ip"])
        try:
            events = float(entrada["events"])
        except (TypeError, ValueError):
            continue
        for m in re.finditer(rf"(?<![\d.]){re.escape(ip)}(?![\d.])", text):
            ocurrencias.append((m.start(), m.end(), events))
    if not ocurrencias:
        return False
    for inicio, fin in cifras:
        frase_inicio, frase_fin = _frase_en(text, inicio)
        cercanas = [
            (max(istart - fin, inicio - iend, 0), events)
            for istart, iend, events in ocurrencias
            if frase_inicio <= istart and iend <= frase_fin
        ]
        # La IP más cercana de la frase respalda la cifra → atribución OK.
        if cercanas and min(cercanas)[1] == value:
            return True
    return False


def _rule_attribution(text: str, ctx: dict) -> List[str]:
    """Regla B: la cifra pegada a una etiqueta debe ser la de su fuente.

    Cada pareja de `_pairs` se resuelve contra el ctx: `event_id_counts`,
    `level_counts`, `suspicious_events` y `http_errors`/`result_errors`. Un
    Event ID citado pero ausente del top-20 (o un ctx sin el dict) también
    motiva rechazo: la cifra no es verificable.
    """
    reasons: List[str] = []
    event_counts = ctx.get("event_id_counts")
    for value, tag in _pairs(text):
        kind, _, arg = tag.partition(":")
        if kind == "event_id":
            label = f"Event ID {arg}"
            if not isinstance(event_counts, dict):
                reasons.append(
                    f"«{_num(value)} … {label}» sin event_id_counts en el contexto"
                )
                continue
            if arg not in event_counts:
                reasons.append(f"«{_num(value)} … {label}» sin dato en el contexto")
                continue
            truth: Any = event_counts[arg]
        elif kind == "nivel":
            label = f"nivel {LEVEL_LABELS.get(arg, arg).lower()}"
            truth = (ctx.get("level_counts") or {}).get(arg, 0)
        elif kind == "codigo":
            label = arg
            truth = (_http_errors(ctx).get("codes") or {}).get(arg, 0)
        elif tag == "errores http":
            label = tag
            truth = _http_errors(ctx).get("total") or 0
        else:  # sospechosos
            label = tag
            truth = ctx.get("suspicious_events") or 0
            # Cifra atribuida a una IP mencionada con SU conteo: la frase es
            # correcta aunque no sea el total global (tarea 8, ruling 3).
            if value != truth and _sospechosos_de_ip_mencionada(text, value, ctx):
                continue
        if value != truth:
            reasons.append(f"«{_num(value)} … {label}» ≠ {_num(truth)}")
    return reasons


# --- Regla C: contenido obligatorio por campo -----------------------------
# Marcadores de paso de la metodología, en 3 grupos: importación del archivo,
# clasificación por severidad y aplicación de reglas de sospecha.
_METHOD_MARKERS = (
    r"importaci|archivo|excel",
    r"severidad|nivel|clasific",
    r"sospech|regla",
)


def _mentions_level(norm: str, lvl: str) -> bool:
    """¿El texto nombra el nivel CONCRETO («nivel crítico»), no un pronombre?

    Comparte el patrón con `_label_matches`: «…clasificados en este nivel»
    no menciona nivel alguno y no cuenta como distribución presentada.
    """
    return re.search(rf"nivel\s+({_LEVEL_PATTERNS[lvl]})", norm) is not None


def _has_number(text: str, value: Any) -> bool:
    """¿Aparece `value` como número real del texto (sin IPs, fechas ni %)?"""
    try:
        target = float(value)
    except (TypeError, ValueError):
        return False
    return any(number == target for _raw, number in _numbers(_PCT_RE.sub(" ", text)))


def _rule_required(field: str, text: str, ctx: dict) -> List[str]:
    """Regla C: contenido obligatorio por campo, derivado SOLO del ctx.

    Solo se exige lo que los datos permiten: niveles con count>0, una IP de
    `suspicious_by_ip` con SU conteo, claves de `severity_mapping` solo si el
    mapping no está vacío. Campos sin regla (findings, action_plan) pasan.
    """
    norm = _norm_pos(text)
    reasons: List[str] = []
    rows = ctx.get("rows")
    risk = str(ctx.get("risk", "") or "")
    levels = ctx.get("level_counts") or {}
    suspicious = ctx.get("suspicious_events") or 0

    def risk_label_present() -> bool:
        # El patrón completo de _LEVEL_PATTERNS con \b cubre
        # crítico/críticos/crítica y no casa «alt…» de «alternativa».
        if not risk:
            return False
        pattern = _LEVEL_PATTERNS.get(risk) or re.escape(risk)
        return re.search(rf"\b{pattern}\b", norm) is not None

    def missing_levels() -> List[str]:
        return [
            f"falta nivel {LEVEL_LABELS.get(lvl, lvl).lower()} de la distribución"
            for lvl in LEVEL_ORDER
            if int(levels.get(lvl, 0) or 0) > 0 and not _mentions_level(norm, lvl)
        ]

    if field == "executive_summary":
        if rows and not any(
            _has_number(sentence, rows) and re.search(r"\bregistros?\b", sentence)
            for sentence in re.split(r"[.;!?\n]+", text)
        ):
            reasons.append(f"falta «{_num(rows)} registros» en la misma frase")
        if not risk_label_present():
            reasons.append(
                f"falta etiqueta de riesgo «{LEVEL_LABELS.get(risk, risk)}»"
            )
        reasons.extend(missing_levels())
        if suspicious and not _has_number(text, suspicious):
            reasons.append(f"falta total de eventos sospechosos ({suspicious})")
    elif field == "conclusions":
        if not risk_label_present():
            reasons.append(
                f"falta etiqueta de riesgo «{LEVEL_LABELS.get(risk, risk)}»"
            )
        if rows and not _has_number(text, rows):
            reasons.append(f"falta el total de registros ({_num(rows)})")
        reasons.extend(missing_levels())
    elif field == "correlation":
        entries = [
            entry for entry in (ctx.get("suspicious_by_ip") or [])
            if isinstance(entry, dict) and entry.get("ip")
        ]
        if entries and not any(
            str(entry["ip"]) in text and _has_number(text, entry.get("events"))
            for entry in entries
        ):
            reasons.append(
                "falta IP con su conteo de eventos "
                f"(p. ej. «{entries[0]['ip']}» con {_num(entries[0].get('events') or 0)})"
            )
    elif field == "methodology":
        matched = sum(1 for pattern in _METHOD_MARKERS if re.search(pattern, norm))
        if matched < 2:
            reasons.append(
                f"faltan pasos del método ({matched} de 3 grupos: "
                "importación, clasificación, sospecha)"
            )
        raws = [
            str(entry.get("raw"))
            for entry in (ctx.get("severity_mapping") or [])
            if isinstance(entry, dict) and entry.get("raw")
        ]
        if raws and not any(_norm_text(raw) in norm for raw in raws):
            reasons.append(
                f"falta clave original de severidad (p. ej. «{raws[0]}»)"
            )
    return reasons


# ---------------------------------------------------------------------------
# Validador del informe: composición de las reglas A–D campo a campo
# ---------------------------------------------------------------------------
# (etiqueta, regla) en el orden del brief: A ancla numérica, B atribución,
# C contenido obligatorio por campo, D anti-redundancia. Cortocircuito: un
# motivo basta para rechazar el campo. Las firmas de las reglas difieren en
# los parámetros, así que cada entrada adapta la llamada; los lambdas resuelven
# el nombre del módulo en tiempo de ejecución (monkeypatcheables en tests).
_FIELD_RULES: Tuple[Tuple[str, Any], ...] = (
    ("A", lambda text, field, ctx: _rule_anchor(text, ctx)),
    ("B", lambda text, field, ctx: _rule_attribution(text, ctx)),
    ("C", lambda text, field, ctx: _rule_required(field, text, ctx)),
    ("D", lambda text, field, ctx: _rule_redundancy(text)),
)


def _validate_fields(ctx: Dict[str, Any], remote: Any) -> Dict[str, str]:
    """Aplica las reglas A–D a cada campo de prosa; devuelve SOLO los aceptados.

    Un campo ausente, vacío o rechazado no aparece en el resultado → la
    orquestación usará el texto del fallback para él. Solo itera los 4 campos
    de prosa: findings/action_plan no se evalúan (el sistema los genera).

    Ninguna excepción se propaga al caller: una regla que lanza rechaza SOLO
    su campo, con log de diagnóstico «campo <f> rechazado (regla X): <motivo>»
    (ruling de la tarea 5: el informe jamás falla por el validador).
    """
    aceptados: Dict[str, str] = {}
    if not isinstance(remote, dict):
        return aceptados
    for field in _PROSE_FIELDS:
        text = remote.get(field)
        if not isinstance(text, str) or not text.strip():
            continue  # sin texto usable → ese campo sale del fallback
        value = text.strip()
        regla_actual = "?"
        motivo: Optional[str] = None
        try:
            for etiqueta, regla in _FIELD_RULES:
                regla_actual = etiqueta
                motivos = regla(value, field, ctx)
                if motivos:
                    motivo = motivos[0]
                    break  # un motivo basta: no se siguen evaluando reglas
        except Exception as exc:  # noqa: BLE001 – rechazo del campo, no error
            logger.warning(
                "campo %s rechazado (regla %s): excepción al evaluarla "
                "(%s: %s)",
                field, regla_actual, type(exc).__name__, exc,
            )
            continue
        if motivo is not None:
            logger.warning(
                "campo %s rechazado (regla %s): %s", field, regla_actual, motivo
            )
        else:
            aceptados[field] = value
    return aceptados


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
async def generate_report_narrative(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Redacta el informe técnico a partir del resumen calculado.

    Siempre devuelve un informe completo: primero se construye el basado en
    reglas (100% anclado a los registros) y, si el modelo remoto aporta prosa
    que PASA las reglas A–D, se superpone campo a campo sin perder evidencias
    ni cifras. Cualquier fallo total (LLM apagado, excepción del chat, JSON
    inválido) deja `aceptados` vacío → fallback completo, idéntico al de hoy.
    """
    ctx = build_context(summary)
    fallback = _fallback_narrative(ctx)
    try:
        remote = await _remote_narrative(ctx)
        aceptados = _validate_fields(ctx, remote)
    except Exception as exc:  # noqa: BLE001 – nunca romper el informe
        logger.warning(
            "informe: fallo del modelo remoto (%s: %s); se usa el fallback",
            type(exc).__name__, exc,
        )
        aceptados = {}
    narrative = _merge(fallback, aceptados)
    # source == "llm" SOLO con ≥1 campo de prosa aceptado; si no, el informe
    # es 100% local y `model` sigue siendo el del motor determinista.
    if any(aceptados.get(campo) for campo in _PROSE_FIELDS):
        narrative["source"] = "llm"
        narrative["model"] = ai_config.get()["model"] or "modelo remoto"
    return narrative


# ---------------------------------------------------------------------------
# Apoyo de la IA al análisis de datos (observaciones automáticas)
# ---------------------------------------------------------------------------
def _local_insights(ctx: Dict[str, Any]) -> List[str]:
    """Observaciones breves y verificables sobre los datos importados."""
    total = ctx["rows"]
    if not total:
        return ["El archivo no contiene registros para analizar."]

    levels = ctx["risk_levels"]
    insights: List[str] = []

    elevated = levels["critico"]["count"] + levels["alto"]["count"] + levels["medio"]["count"]
    insights.append(
        f"Distribución de criticidad: {levels['bajo']['count']} registros en nivel Bajo "
        f"({_pct(levels['bajo']['count'], total)}) y {elevated} en Medio o superior "
        f"({_pct(elevated, total)}); el riesgo global queda en «{ctx['risk_label']}»."
    )

    if ctx["suspicious_events"]:
        top_ip = ctx["suspicious_by_ip"][0] if ctx["suspicious_by_ip"] else None
        extra = (
            f" La IP {top_ip['ip']} es la de mayor alcance: {top_ip['events']} eventos en "
            f"{top_ip['hosts']} hosts distintos."
            if top_ip
            else ""
        )
        insights.append(
            f"Se detectaron {ctx['suspicious_events']} eventos sospechosos "
            f"({_pct(ctx['suspicious_events'], total)}) por severidad elevada, palabras clave "
            f"de fallo de autenticación y Event ID relevantes.{extra}"
        )

    # Campo Result: éxito/fallo de la operación o código HTTP de respuesta.
    results = ctx.get("result_counts") or {}
    if results:
        top = sorted(results.items(), key=lambda kv: -kv[1])[:4]
        parts = " · ".join(
            f"{count} «{value}» ({_pct(count, total)})" for value, count in top
        )
        http_errors = {
            value: count for value, count in results.items()
            if value.isdigit() and 400 <= int(value) < 600
        }
        extra = ""
        if http_errors:
            errs = ", ".join(
                f"{value} ×{count}"
                for value, count in sorted(http_errors.items(), key=lambda kv: -kv[1])
            )
            extra = (
                f"; los códigos HTTP de error ({errs}) suman "
                f"{sum(http_errors.values())} respuestas fallidas del servidor"
            )
        insights.append(f"Distribución de Result: {parts}{extra}.")

    if ctx["top_hosts"]:
        host, count = next(iter(ctx["top_hosts"].items()))
        insights.append(
            f"Concentración por host: {host} concentra {count} eventos "
            f"({_pct(count, total)}), por lo que conviene priorizar su revisión."
        )

    days = ctx["events_per_day"]
    if days:
        peak = max(days.items(), key=lambda kv: kv[1])
        if len(days) == 1:
            insights.append(
                f"Ventana temporal: los {total} registros pertenecen a un único día "
                f"({peak[0]}); no hay serie histórica para detectar tendencias."
            )
        else:
            insights.append(
                f"Ventana temporal: {len(days)} días analizados, con pico de "
                f"{peak[1]} eventos el {peak[0]}."
            )

    if ctx["missing_columns"]:
        insights.append(
            "Calidad del dato: faltan columnas esperadas en el archivo "
            f"({', '.join(ctx['missing_columns'])}); esas facetas no pudieron analizarse."
        )
    else:
        insights.append(
            "Calidad del dato: el archivo incluye todas las columnas esperadas, "
            "por lo que el análisis cubre integridad, autenticación, procesos y origen IP."
        )

    non_numeric = ctx.get("non_numeric_event_ids") or []
    if non_numeric:
        insights.append(
            "Se observaron identificadores de evento no numéricos "
            f"({', '.join(non_numeric)}) que provienen de otra fuente de logs "
            "y deben revisarse por separado."
        )

    return insights


def generate_data_insights(summary: Dict[str, Any]) -> List[str]:
    """Observaciones de la IA sobre los datos (para el dashboard).

    Igual que el informe: solo se generan a partir de cifras calculadas a
    partir de los registros importados (grounding estricto).
    """
    return _local_insights(build_context(summary))


# ---------------------------------------------------------------------------
# Consulta en lenguaje natural sobre los registros (RAG)
# ---------------------------------------------------------------------------
# Recuperación semántica local + respuesta anclada a los eventos recuperados.
# El modelo (si hay) solo REDACTA: las cifras, hosts, IPs y Log_ID salen de la
# evidencia recuperada y se validan antes de mostrarse.

QA_MAX_EVIDENCE = 8
QA_MIN_SCORE = 0.30  # por debajo de esto avisamos de baja confianza
# Memoria multi-turno: turnos de conversación que se inyectan en el prompt
# (por análisis, en RAM) y tope de caracteres de cada respuesta recordada.
# Acotados a propósito: con num_ctx=2048 la ventana es finita y el prompt
# actual (evidencias + referencia) ya ocupa ~1200 tokens; 4 turnos × 350
# chars dejan margen sin desbordar ni truncar la pregunta vigente.
CHAT_HISTORY_TURNS = 4
QA_HISTORY_ANSWER_CHARS = 350
QA_SYSTEM_PROMPT = (
    "Eres un analista SOC de nivel 2 respondiendo una consulta sobre eventos de "
    "un SIEM. Respondes en español, con precisión técnica y sin adornos. "
    "Trabajas EXCLUSIVAMENTE con los eventos recuperados que se te entregan: si "
    "un dato no está en la evidencia, no lo menciones ni lo infieras. Cita cada "
    "afirmación con el Log_ID del evento que la respalda. Nunca inventes hosts, "
    "IPs, usuarios, fechas, cifras ni eventos. Si la evidencia no alcanza para "
    "responder, dilo explícitamente. "
    "Estructura la respuesta: 1) respuesta directa en la primera frase; "
    "2) justificación con los Log_ID citados; 3) solo si aporta, un cierre con "
    "riesgo o siguiente paso. Sé breve: máximo ~150 palabras salvo que se pida "
    "detalle."
)


def build_qa_context(
    question: str,
    hits: List[Dict[str, Any]],
    summary: Optional[Dict[str, Any]] = None,
    reference: Optional[Dict[str, Any]] = None,
    index_note: str = "",
) -> Dict[str, Any]:
    """Contexto que la IA (o el fallback) puede ver: solo eventos recuperados."""
    summary = summary or {}
    evidence = []
    for hit in hits[:QA_MAX_EVIDENCE]:
        meta = hit.get("metadata") or {}
        evidence.append({
            "log_id": meta.get("log_id", "—"),
            "timestamp": meta.get("timestamp", "—"),
            "host": meta.get("hostname", "—"),
            "event_id": meta.get("event_id", "—"),
            "user": meta.get("user", "—"),
            "source_ip": meta.get("source_ip", "—"),
            "destination": meta.get("destination", "—"),
            "severity": meta.get("severity", "—"),
            "level": hit.get("level", "bajo"),
            "suspicious": bool(hit.get("suspicious")),
            "description": meta.get("description", "—"),
            "result": meta.get("result", "—"),
            "relevance": float(hit.get("score", 0.0)),
            "document": hit.get("document", ""),
        })
    return {
        "question": question.strip(),
        "dataset_rows": int(summary.get("rows", 0) or 0),
        "retrieved_total": len(hits),
        "level_counts": summary.get("level_counts") or {},
        # Datos de agregación (conteos de TODO el dataset): permiten responder
        # preguntas del tipo «¿cuántos...?» / «¿qué hosts concentran...?» con
        # cifras exactas en lugar de con unas pocas filas recuperadas.
        "top_hosts": summary.get("top_hosts") or {},
        "hosts_by_level": summary.get("top_hosts_by_level") or {},
        # Campo Result (Success/Failure/Allowed o código HTTP) y sus hosts.
        "result_counts": summary.get("result_counts") or {},
        "result_hosts": summary.get("result_hosts") or {},
        "suspicious_events": int(summary.get("suspicious_events") or 0),
        "unique_hosts": summary.get("unique_hosts"),
        # Aviso del backend: el índice semántico aún se está construyendo y la
        # recuperación de esta consulta ha sido solo léxica.
        "index_note": index_note,
        "evidence": evidence,
        # Base de conocimiento (MITRE ATT&CK + glosario de Event IDs): material
        # de referencia del marco, etiquetado aparte para no confundirlo con
        # una afirmación sobre los registros.
        "reference": reference or {"available": False, "event_ids": [], "techniques": [], "note": ""},
    }


def _reference_lines(reference: Optional[Dict[str, Any]]) -> List[str]:
    """Bloque de referencia técnica (KB) para la respuesta en texto.

    Se etiqueta explícitamente como material del marco: explica significados,
    no afirma nada sobre los registros analizados.
    """
    if not reference or not reference.get("available"):
        return []
    events = reference.get("event_ids") or []
    techs = reference.get("techniques") or []
    if not events and not techs:
        return []
    lines = [
        "**Referencia técnica** (MITRE ATT&CK y glosario de Event IDs de Windows — "
        "contexto del marco, no un hallazgo de tus registros):",
    ]
    for item in events[:QA_MAX_EVIDENCE]:
        lines.append(f"- `Event ID {item.get('id')}` — {item.get('titulo')}")
    for item in techs[:QA_MAX_EVIDENCE]:
        # Solo ~79 técnicas están curadas en español; las demás vienen de la
        # fuente oficial en inglés, así que se usa ese texto en lugar de un
        # hueco vacío tras los dos puntos.
        desc = str(item.get("descripcion_es") or "").strip() or str(
            item.get("description_en") or ""
        ).strip()
        lines.append(
            f"- `{item.get('id')} {item.get('name')}` ({item.get('tactic_es')}): {desc}"
        )
    return lines


# ---------------------------------------------------------------------------
# Consultas de AGREGACIÓN (conteos, distribución, hosts dominantes)
# ---------------------------------------------------------------------------
# Una pregunta como «¿qué hosts concentran los eventos críticos?» NO se
# contesta con 8 filas recuperadas (que además pueden ser irrelevantes): se
# contesta con los conteos exactos calculados sobre TODO el dataset.
AGGREGATE_MARKERS = (
    "cuantos", "cuantas", "distribucion", "distribuyen", "concentran",
    "concentra", "con mas", "mas eventos", "reparten", "reparto", "desglose",
    "por nivel", "por criticidad", "por host", "resumen", "estadistic",
    "ranking", "top host", "top hosts", "total de", "como se distribuyen",
)

# Campo Result del archivo: éxito/fallo de la operación o código HTTP.
RESULT_MARKERS = (
    "resultado", "result", "codigo de respuesta", "codigos de respuesta",
    "codigo http", "codigos http", "respuestas http", "status code",
    "failure", "success", "allowed", "fallo", "fallaron", "fallidas",
    "fallidos", "fallido", "exito", "exitos", "permitido", "permitidos",
    "http", "iis",
)

# Consultas que piden FILAS concretas: se responden con la recuperación
# normal (evidencias con Log_ID), no con un resumen de conteos.
ROW_REQUEST_MARKERS = (
    "muestrame", "muestra los", "listar", "listado", "ver los", "ver las",
    "ejemplo", "log_id", "cual fue el evento",
)

# Contexto para interpretar un código suelto («¿hubo 500?») como HTTP.
HTTP_CONTEXT = ("http", "respuesta", "codigo", "status", "servidor", "web", "iis", "proxy")

# Equivalencias entre lo que escribe el analista y los valores del archivo.
_RESULT_SYNONYMS = {
    "failure": ("fallo", "fallaron", "fallido", "fallidos", "fallida",
                "fallidas", "fracaso", "fracasaron"),
    "success": ("exito", "exitos", "exitosa", "exitosos", "correcto",
                "correctos", "completado"),
    "allowed": ("permitido", "permitidos", "permitidas", "autorizado",
                "autorizados", "aceptado"),
}

_LEVEL_PATTERNS = {
    "critico": r"critic[oa]s?",
    "alto": r"alt[oa]s?",
    "medio": r"medi[oa]s?",
    "bajo": r"baj[oa]s?",
}


def _norm_text(text: str) -> str:
    """Minúsculas y sin tildes/ñ: para comparar consultas escritas en español."""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", (text or "").lower())
        if unicodedata.category(ch) != "Mn"
    )


def is_result_question(question: str) -> bool:
    """¿La consulta versa sobre el campo Result (éxito/fallo o código HTTP)?

    Se responde con los conteos exactos del resumen: la distribución de
    `Result` existe entera en el análisis y no hace falta recuperar filas.
    """
    text = _norm_text(question)
    # «muéstrame los eventos con resultado Failure»: se piden FILAS, no
    # conteos, así que esta consulta va por la recuperación normal.
    if any(marker in text for marker in ROW_REQUEST_MARKERS):
        return False
    if any(marker in text for marker in RESULT_MARKERS):
        return True
    # Códigos sueltos (500, 404…) solo cuentan si hay contexto de respuesta.
    if re.search(r"\b[245]\d\d\b", text) and any(ctx in text for ctx in HTTP_CONTEXT):
        return True
    return False


def is_aggregate_question(question: str) -> bool:
    """¿La consulta pide un conteo / distribución / ranking sobre el dataset?"""
    text = _norm_text(question)
    if any(marker in text for marker in AGGREGATE_MARKERS):
        return True
    # Las preguntas sobre el campo Result son agregaciones: se contestan con
    # los conteos exactos del archivo, no con unas pocas filas recuperadas.
    return is_result_question(question)


def _level_terms(question: str) -> List[str]:
    """Niveles de criticidad mencionados en la consulta (sin acentos)."""
    text = _norm_text(question)
    found = [lvl for lvl in LEVEL_ORDER if re.search(rf"\b{_LEVEL_PATTERNS[lvl]}\b", text)]
    if re.search(r"\bsospechos", text):
        found.append("sospechoso")
    return found


def _result_terms(question: str, counts: Dict[str, Any]) -> List[str]:
    """Valores de Result citados en la consulta, con sus claves reales del archivo."""
    text = _norm_text(question)
    found: List[str] = []
    for key in counts:
        key_norm = _norm_text(str(key))
        if not key_norm:
            continue
        hit = (
            re.search(rf"\b{re.escape(key_norm)}\b", text)
            if key_norm.isdigit()
            else key_norm in text
        )
        if hit and str(key) not in found:
            found.append(str(key))
    # Sinónimos en español para los valores en inglés del archivo
    normalized = {_norm_text(str(k)): str(k) for k in counts}
    for canonical, words in _RESULT_SYNONYMS.items():
        real = normalized.get(canonical)
        if real and real not in found and any(word in text for word in words):
            found.append(real)
    return found


def _missing_http_codes(question: str, counts: Dict[str, Any]) -> List[str]:
    """Códigos HTTP citados en la consulta que NO aparecen en el archivo."""
    text = _norm_text(question)
    if not any(ctx in text for ctx in HTTP_CONTEXT):
        return []
    present = {_norm_text(str(k)) for k in counts}
    missing: List[str] = []
    for code in re.findall(r"\b[245]\d\d\b", text):
        if code not in present and code not in missing:
            missing.append(code)
    return missing


# Marcadores que piden además el reparto GENERAL del dataset (críticas, hosts…):
# en ese caso la respuesta de Result se añade, no sustituye al resto.
GENERAL_DISTRIBUTION_MARKERS = (
    "distribucion", "distribuyen", "reparten", "reparto", "desglose",
    "por nivel", "por criticidad", "por host", "resumen", "estadistic",
    "ranking", "top host", "top hosts", "con mas", "mas eventos",
    "concentran", "concentra", "como se distribuyen",
)


def _result_focused(question: str, results: Dict[str, Any]) -> bool:
    """¿La consulta pide SOLO el reparto de Result?

    - «¿cuántos registros fallaron?» → centrada (marca el valor Failure).
    - «¿hubo errores HTTP 400?» → centrada (código citado).
    - «¿cuántos hay por criticidad y con resultado Success?» → no: pide además
      el reparto general, que se añade a la respuesta.
    """
    if not results or not is_result_question(question):
        return False
    text = _norm_text(question)
    cites_value = bool(
        _result_terms(question, results) or _missing_http_codes(question, results)
    )
    if cites_value:
        return not any(marker in text for marker in GENERAL_DISTRIBUTION_MARKERS)
    return not any(marker in text for marker in AGGREGATE_MARKERS)


def _result_lines(ctx: Dict[str, Any]) -> List[str]:
    """Reparto EXACTO del campo Result (éxito/fallo y códigos HTTP de respuesta)."""
    results = ctx.get("result_counts") or {}
    if not results:
        return []
    rows = ctx["dataset_rows"]
    question = ctx["question"]
    mentioned = _result_terms(question, results)
    missing = _missing_http_codes(question, results)
    lines: List[str] = []

    # Reparto completo del campo, de mayor a menor
    parts = [
        f"{value} {int(count)} ({_pct(int(count), rows)})"
        for value, count in sorted(results.items(), key=lambda kv: -int(kv[1]))
    ]
    lines.append("Distribución del campo Result: " + " · ".join(parts) + ".")

    hosts_by_result = ctx.get("result_hosts") or {}
    for value in mentioned:
        count = int(results.get(value, 0) or 0)
        lines.append(
            f"Registros con Result «{value}»: {count} de {rows} ({_pct(count, rows)})."
        )
        hosts = hosts_by_result.get(value) or {}
        if hosts:
            lines.append(
                f"Hosts con Result «{value}»: "
                + ", ".join(f"{host} {int(n)}" for host, n in hosts.items())
                + "."
            )
    # «¿devolvieron error HTTP?» (sin citar código concreto): se suman los
    # 4xx/5xx presentes en el archivo, con el detalle de cada uno.
    if not missing and re.search(
        r"error http|errores http|codigo[s]? de error|respuesta[s]? de error",
        _norm_text(question),
    ):
        err = {
            str(k): int(v)
            for k, v in results.items()
            if str(k).isdigit() and str(k)[:1] in ("4", "5")
        }
        if err:
            total_err = sum(err.values())
            detalle = ", ".join(f"{code} ×{n}" for code, n in sorted(err.items()))
            lines.append(
                f"Registros con código HTTP de error (4xx/5xx): {total_err} de {rows} "
                f"({_pct(total_err, rows)}) — {detalle}."
            )
    # Códigos citados que el archivo no contiene: se responde con el 0 explícito
    # (p. ej. «400» no existe en esta carga).
    for code in missing:
        lines.append(
            f"No hay registros con Result «{code}» en este archivo (0 de {rows})."
        )
    return lines


def _aggregate_lines(ctx: Dict[str, Any]) -> List[str]:
    """Líneas con cifras EXACTAS del resumen del análisis (nunca inventadas)."""
    rows = ctx["dataset_rows"]
    counts = ctx["level_counts"] or {}
    question = ctx["question"]
    requested = [lvl for lvl in _level_terms(question) if lvl in LEVEL_ORDER]
    hosts_by_level = ctx.get("hosts_by_level") or {}
    lines: List[str] = []
    result_q = is_result_question(question)
    results = ctx.get("result_counts") or {}
    # Pregunta centrada en Result (sin pedir además el reparto general):
    # se responde solo con ese campo para no enturbiar la respuesta.
    if _result_focused(question, results):
        return _result_lines(ctx) + [
            f"Todas las cifras proceden del resumen calculado sobre tus {rows} "
            "registros importados; no se ha añadido ningún dato externo."
        ]

    def _hosts_for(lvl: str, n: int = 3) -> List[tuple]:
        pairs = [
            (str(host), int(data.get(lvl, 0) or 0))
            for host, data in hosts_by_level.items()
            if isinstance(data, dict) and int(data.get(lvl, 0) or 0) > 0
        ]
        pairs.sort(key=lambda kv: (-kv[1], kv[0]))
        return pairs[:n]

    # 1) Distribución completa por criticidad
    if counts:
        distribution = " · ".join(
            f"{LEVEL_LABELS.get(lvl, lvl)} {int(counts.get(lvl, 0) or 0)}"
            for lvl in LEVEL_ORDER
        )
        lines.append(
            f"Distribución de los {rows} registros por criticidad: {distribution}."
        )
        empty = [lvl for lvl in LEVEL_ORDER if not int(counts.get(lvl, 0) or 0)]
        empty = [lvl for lvl in empty if lvl not in requested]
        if empty:
            lines.append(
                "Sin registros en "
                + ", ".join(f"«{LEVEL_LABELS.get(lvl, lvl)}»" for lvl in empty)
                + f" (0 de {rows})."
            )

    # 2) Hosts que concentran el nivel pedido (o el dominante si no se pide)
    def _hosts_line(lvl: str) -> Optional[str]:
        pairs = _hosts_for(lvl)
        if not pairs:
            return None
        return (
            f"Hosts que concentran el nivel «{LEVEL_LABELS.get(lvl, lvl)}»: "
            + ", ".join(f"{host} ({count})" for host, count in pairs)
            + "."
        )

    if requested:
        for lvl in requested:
            count = int(counts.get(lvl, 0) or 0)
            label = LEVEL_LABELS.get(lvl, lvl)
            if count:
                lines.append(f"Registros en «{label}»: {count} de {rows} ({_pct(count, rows)}).")
                line = _hosts_line(lvl)
                if line:
                    lines.append(line)
                continue
            # Nivel pedido sin datos: se dice de forma explícita y se ofrece
            # el nivel más crítico que sí existe en la carga.
            present = next(
                (l for l in LEVEL_ORDER if int(counts.get(l, 0) or 0) > 0), None
            )
            if present is None:
                lines.append(f"No hay registros clasificados como «{label}» (0 de {rows}).")
                continue
            lines.append(
                f"No hay registros clasificados como «{label}» (0 de {rows}). "
                f"El nivel con más eventos es «{LEVEL_LABELS.get(present, present)}» "
                f"con {int(counts.get(present, 0) or 0)}."
            )
            line = _hosts_line(present)
            if line:
                lines.append(line)
    else:
        top = ctx.get("top_hosts") or {}
        if top:
            lines.append(
                "Hosts con más eventos (todos los niveles): "
                + ", ".join(f"{host} ({int(n)})" for host, n in list(top.items())[:4])
                + "."
            )
        present = next((l for l in LEVEL_ORDER if int(counts.get(l, 0) or 0) > 0), None)
        if present:
            line = _hosts_line(present)
            if line:
                lines.append(line)

    # 3) Sospechosos y hosts distintos
    suspicious = int(ctx.get("suspicious_events") or 0)
    if "sospechoso" in _level_terms(ctx["question"]) or not requested:
        if suspicious:
            lines.append(
                f"Eventos marcados como sospechosos: {suspicious} de {rows} "
                f"({_pct(suspicious, rows)})."
            )
        elif "sospechoso" in _level_terms(ctx["question"]):
            lines.append(f"Ningún registro quedó marcado como sospechoso (0 de {rows}).")
    unique_hosts = ctx.get("unique_hosts")
    if unique_hosts:
        lines.append(f"Hosts distintos en la carga: {int(unique_hosts)}.")

    # 4) Campo Result (éxito/fallo o código HTTP), si la consulta lo toca
    if result_q:
        lines += _result_lines(ctx)

    lines.append(
        f"Todas las cifras proceden del resumen calculado sobre tus {rows} "
        "registros importados; no se ha añadido ningún dato externo."
    )
    return lines


def _aggregate_answer(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Respuesta determinista a consultas de agregación (conteos exactos).

    Devuelve ``None`` si la consulta no es de agregación o no hay dataset.
    """
    if not is_aggregate_question(ctx["question"]) or not ctx["dataset_rows"]:
        return None
    reference = ctx.get("reference") or {
        "available": False, "event_ids": [], "techniques": [], "note": "",
    }
    # Titular según lo que pide la consulta: reparto de Result o resumen general
    only_result = _result_focused(ctx["question"], ctx.get("result_counts") or {})
    headline = (
        f"Resumen exacto del campo Result en tus {ctx['dataset_rows']} registros importados:"
        if only_result
        else f"Resumen exacto de tus {ctx['dataset_rows']} registros importados:"
    )
    lines = [
        headline,
        "",
    ]
    lines += _aggregate_lines(ctx)
    return {
        "answer": "\n".join(lines),
        "evidence": [],
        # La referencia del marco no aporta a una pregunta de conteos: se
        # omite para no mezclar técnicas MITRE con cifras del dataset.
        "reference": {"available": False, "event_ids": [], "techniques": [], "note": ""},
        "summary_line": f"resumen del dataset · {ctx['dataset_rows']} registros",
        "confidence": "alta",
        "engine": "deterministic",
        "model": None,
        "warnings": [],
        "mode": "aggregate",
    }


def _qa_fallback(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Respuesta determinista sobre los eventos recuperados (sin modelo)."""
    question = ctx["question"]
    evidence = ctx["evidence"]
    rows = ctx["dataset_rows"]
    reference = ctx.get("reference") or {}

    if not evidence:
        lines = [
            f"No encontré registros que se parezcan semánticamente a "
            f"«{question}» entre los {rows} registros disponibles.",
            "",
            "Prueba a reformular la consulta con términos que aparezcan en tus "
            "registros (host, usuario, Event ID, IP), o filtra por criticidad / "
            "eventos sospechosos desde la tabla de registros.",
        ]
        if ctx.get("index_note"):
            lines += ["", ctx["index_note"]]
        if rows:
            lines += ["", f"Resumen de tus {rows} registros mientras tanto:"]
            lines += _aggregate_lines(ctx)
        lines += _reference_lines(reference)
        return {
            "answer": "\n".join(lines),
            "evidence": [],
            "reference": reference,
            "summary_line": "Sin coincidencias en el índice",
            "confidence": "ninguna",
            "engine": "deterministic",
            "model": None,
            "warnings": ["sin_resultados"],
            "mode": "evidence",
        }

    # Conteos siempre derivados de los eventos recuperados, nunca del modelo
    level_counts: Dict[str, int] = {}
    hosts: Dict[str, int] = {}
    ips: Dict[str, int] = {}
    event_ids: Dict[str, int] = {}
    suspicious = 0
    for item in evidence:
        lvl = item["level"]
        level_counts[lvl] = level_counts.get(lvl, 0) + 1
        host = str(item["host"] or "—")
        hosts[host] = hosts.get(host, 0) + 1
        ip = str(item["source_ip"] or "—")
        if ip and ip != "—":
            ips[ip] = ips.get(ip, 0) + 1
        ev = str(item["event_id"] or "—")
        if ev and ev != "—":
            event_ids[ev] = event_ids.get(ev, 0) + 1
        if item["suspicious"]:
            suspicious += 1

    def _top(counter: Dict[str, int], n: int = 3) -> str:
        ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        return ", ".join(f"{key} ({value})" for key, value in ordered[:n])

    distribution = " · ".join(
        f"{LEVEL_LABELS.get(lvl, lvl)} {level_counts[lvl]}"
        for lvl in LEVEL_ORDER
        if level_counts.get(lvl)
    )

    best = float(evidence[0].get("relevance", 0.0))
    weak = best < QA_MIN_SCORE

    lines: List[str] = []
    if weak:
        lines += [
            f"No encontré evidencia directa en los {rows} registros que responda a "
            f"«{question}». La mejor coincidencia tiene una similitud de {best:.2f} "
            f"(umbral {QA_MIN_SCORE:.2f}), así que **no puedo confirmar** la consulta.",
            "",
            "Te dejo igualmente las coincidencias más parecidas por si querés revisarlas "
            "a mano; no interpretan la pregunta:",
        ]
    else:
        lines += [
            f"Encontré {len(evidence)} de {ctx['retrieved_total']} eventos recuperados "
            f"de los {rows} registros indexados que responden a «{question}».",
            "",
            f"**Distribución:** {distribution or 'sin niveles'} — {suspicious} marcados como sospechosos.",
            f"**Hosts implicados:** {_top(hosts)}.",
            f"**Origen:** {_top(ips)}.",
            f"**Event IDs:** {_top(event_ids)}.",
            "",
            "**Evidencias (más relevantes primero):**",
        ]
    for position, item in enumerate(evidence, start=1):
        lines.append(
            f"{position}. `{item['log_id']}` · {LEVEL_LABELS.get(item['level'], item['level'])} · "
            f"host {item['host']} · origen {item['source_ip']} · Event_ID {item['event_id']} · "
            f"similitud {item['relevance']:.2f}"
        )
        description = str(item.get("document", "")).replace("\n", " ")
        if description:
            lines.append(f"   > {description[:240]}")

    lines.append("")
    if weak:
        lines.append(
            "Si querés, reformulá la consulta (p. ej. «autenticaciones fallidas» o "
            "«Event ID 4625») o filtrá por criticidad desde la tabla de registros."
        )
    else:
        lines.append(
            "Todos los datos anteriores provienen de los registros importados; "
            "no se ha generado ninguna cifra nueva."
        )
    if ctx.get("index_note"):
        lines.append("")
        lines.append(ctx["index_note"])

    ref_lines = _reference_lines(reference)
    if ref_lines:
        lines.append("")
        lines.extend(ref_lines)

    warnings: List[str] = []
    if weak:
        warnings.append("baja_confianza")

    return {
        "answer": "\n".join(lines),
        "evidence": evidence,
        "reference": reference,
        "summary_line": (
            f"{len(evidence)} eventos · {distribution or 'sin niveles'} · "
            f"mejor similitud {best:.2f}"
        ),
        "confidence": "alta" if best >= 0.55 else ("media" if best >= QA_MIN_SCORE else "baja"),
        "engine": "deterministic",
        "model": None,
        "warnings": warnings,
        "mode": "evidence",
    }


def _cited_log_ids(text: str) -> List[str]:
    """Log_ID candidatos citados en una respuesta (shape derivado de los reales)."""
    return re.findall(r"\b[A-Za-z]{2,12}[-_]\d{2,}\b", text or "")


# --- Presupuesto del bloque few-shot contra num_ctx -------------------------
# Aproximación chars→tokens medida sobre prompts REALES de este proyecto (no
# la regla genérica de «4 chars/tok», que se queda corta con JSON):
#   5818 chars ↔ 2253 tok   y   6753 chars ↔ 2545 tok   ⇒  ~2,6 chars/tok
# Se usa 2,5 (por debajo de lo medido) para SOBREESTIMAR el consumo y que el
# presupuesto nunca se quede corto.
CHARS_PER_TOKEN = 2.5
# Colchón de lo que no se puede medir aquí (redondeos, tokenización real).
CTX_MARGIN_TOKENS = 128
# Etiqueta y aviso del bloque few-shot («NO es un hallazgo de los registros»…).
FEEDBACK_BLOCK_OVERHEAD_CHARS = 160
# Coste por ejemplo dentro del bloque (la etiqueta «consulta «…» → …»).
FEEDBACK_PER_EXAMPLE_CHARS = 140


def _feedback_note(question: str, base_chars: int, history_chars: int) -> Tuple[str, int]:
    """Bloque few-shot con el criterio del analista sobre consultas afines.

    **Presupuestado contra `num_ctx`.** El prompt NO debe pasar de la ventana:
    si pasa, Ollama recorta los mensajes MÁS VIEJOS —el system prompt con las
    reglas de no inventar Log_ID— en vez de acortar la respuesta, y el modelo
    deja de seguir las instrucciones justo cuando la conversación se pone
    larga. Por eso, si la memoria de turnos ya se comió la ventana, se inyectan
    CERO ejemplos: el few-shot es un plus, nunca lo que haga perder instrucciones.

    Vacío si no hay ejemplos aprendidos todavía: la app responde igual que
    antes (cero regresión). Es criterio de redacción, nunca un hallazgo, y el
    propio bloque lo advierte al modelo.

    Devuelve `(bloque, nº de ejemplos inyectados)`: el conteo lo informa la
    respuesta como `rag_examples_used` (auditoría del harness de evaluación).
    Se cuenta sobre el propio bloque («- Consulta «», contrato de
    `feedback_store.prompt_block`) para que el número nunca mienta.
    """
    try:
        num_ctx = int(ai_config.get()["num_ctx"])
        used_tok = (len(QA_SYSTEM_PROMPT) + history_chars + base_chars) / CHARS_PER_TOKEN
        free_chars = (num_ctx - used_tok - CTX_MARGIN_TOKENS) * CHARS_PER_TOKEN
        budget = free_chars - FEEDBACK_BLOCK_OVERHEAD_CHARS
        per_example = feedback_store.EXAMPLE_CHARS + FEEDBACK_PER_EXAMPLE_CHARS
        if budget < per_example:
            return "", 0   # no cabe ni un ejemplo: se omite el few-shot entero
        limit = min(feedback_store.MAX_EXAMPLES, int(budget // per_example))
        block = feedback_store.prompt_block(question, limit)
    except Exception:  # noqa: BLE001 – el few-shot nunca tumba la consulta
        return "", 0
    if not block:
        return "", 0
    return f"{block}\n\n", block.count("- Consulta «")


def _grounding_ok(answer: str, evidence: List[Dict[str, Any]]) -> bool:
    """Rechaza la respuesta del modelo si cita Log_ID inexistentes.

    Toma el prefijo de los Log_ID reales (p. ej. «EVT-») y exige que todo
    token con ese formato exista en la evidencia recuperada. Un solo ID
    inventado invalida la respuesta entera y se usa el fallback determinista.
    """
    known = {str(item.get("log_id", "")) for item in evidence}
    known -= {"", "—", "None"}
    if not known:
        return True  # sin ids reales no hay nada que validar

    prefixes = {k.split("-")[0].split("_")[0] for k in known}
    for token in _cited_log_ids(answer):
        family = token.split("-")[0].split("_")[0]
        if family in prefixes and token not in known:
            return False
    return True


def _slim_for_prompt(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Evidencia serializada SOLO para el prompt del LLM (no para la UI).

    El campo `document` del envelope repite literalmente los campos crudos
    (Log_ID, Timestamp, Hostname…) más [DESC] y [ANÁLISIS]: enviarlo al modelo
    dobla el prompt sin aportar información nueva y solo encarece el prefill.
    El modelo trabaja con los campos estructurados, que son más fáciles de
    citar sin inventar Log_IDs.

    NOTA: se descartó que el prefill corriera en CPU por VRAM insuficiente
    (medido en la GTX 1060 de 6 GB: prefill en GPU a ~30 ms incluso con
    num_ctx=16384). El motivo real para adelgazar es el tamaño del prompt,
    no un desplazamiento a CPU.
    """
    slim: List[Dict[str, Any]] = []
    for item in evidence:
        slim.append({
            "log_id": item.get("log_id"),
            "timestamp": item.get("timestamp"),
            "host": item.get("host"),
            "event_id": item.get("event_id"),
            "user": item.get("user"),
            "source_ip": item.get("source_ip"),
            "severity": item.get("severity"),
            "level": item.get("level"),
            "suspicious": item.get("suspicious"),
            "result": item.get("result"),
            "description": str(item.get("description") or "")[:200],
        })
    return slim


def _ref_note(reference: Optional[Dict[str, Any]]) -> str:
    """Nota de la referencia del marco (MITRE + Event IDs) para el prompt.

    Versión compacta: solo los campos que el modelo necesita para explicar un
    Event ID o una técnica (id, nombre, táctica, descripción corta). Los campos
    largos (`se_detecta` completo) se quedan fuera del prompt: el modelo los
    resumiría igual mal y cuestan tokens de prefill. La descripción usa el
    español curado y, si la técnica no lo está, la fuente oficial en inglés.
    """
    reference = reference or {}
    if not reference.get("available"):
        return ""
    events = [
        {"id": e.get("id"), "titulo": e.get("titulo"), "tecnicas": e.get("relacionados")}
        for e in (reference.get("event_ids") or [])[:QA_MAX_EVIDENCE]
    ]
    techs = [
        {"id": t.get("id"), "name": t.get("name"), "tactic": t.get("tactic_es"),
         "desc": str(t.get("descripcion_es") or t.get("description_en") or "")[:160]}
        for t in (reference.get("techniques") or [])[:QA_MAX_EVIDENCE]
    ]
    return (
        "\n\nREFERENCIA TÉCNICA (MITRE ATT&CK y glosario de Event IDs): úsala SOLO "
        "para explicar qué significa un Event ID o qué técnica representa. "
        "Jamás la presentes como un hallazgo de los registros y no afirmes que "
        "la técnica haya ocurrido salvo que los eventos lo demuestren.\n"
        + json.dumps({"event_ids": events, "techniques": techs},
                     ensure_ascii=False, default=str)
    )


def _qa_messages(
    ctx: Dict[str, Any], history: Optional[List[Dict[str, Any]]] = None
) -> Tuple[List[Dict[str, str]], int]:
    """Mensajes del QA: system + turnos previos (memoria) + consulta actual.

    Devuelve `(mensajes, nº de ejemplos few-shot inyectados)`: el conteo lo
    informa la respuesta como `rag_examples_used` (auditoría para el harness
    de evaluación); la posición del bloque en el prompt no cambia.

    La memoria multi-turno permite resolver referencias como «esos 33
    eventos» («¿y esos mismos con IP externa?»): los últimos
    ``CHAT_HISTORY_TURNS`` turnos viajan como pares user/assistant antes de la
    pregunta vigente. Cada respuesta inyectada se acorta a
    ``QA_HISTORY_ANSWER_CHARS`` porque la ventana de contexto (num_ctx) es
    finita. La RECUPERACIÓN sigue usando solo la pregunta actual: los
    embeddings no entienden contexto conversacional (limitación conocida).
    """
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": QA_SYSTEM_PROMPT}
    ]
    history_chars = 0
    for turn in (history or [])[-CHAT_HISTORY_TURNS:]:
        q = str(turn.get("question") or "").strip()
        a = str(turn.get("answer") or "").strip()
        if not q or not a:
            continue
        answer = a[:QA_HISTORY_ANSWER_CHARS]
        messages.append({"role": "user", "content": q})
        messages.append({"role": "assistant", "content": answer})
        history_chars += len(q) + len(answer)

    head = (
        "Responde la consulta usando SOLO estos eventos recuperados.\n"
        "Cita los Log_ID entre comillas simples. Si no hay evidencia "
        "suficiente, dilo.\n\n"
    )
    tail = (
        "CONSULTA:\n"
        + ctx["question"]
        + "\n\nEVENTOS RECUPERADOS:\n"
        + json.dumps(_slim_for_prompt(ctx["evidence"]), ensure_ascii=False, default=str)
        + _ref_note(ctx.get("reference"))
    )
    # Criterio del analista sobre consultas afines: va ANTES de la evidencia
    # (para que el modelo lo use al redactar) pero presupuestado contra
    # num_ctx: los ejemplos nunca pueden ser lo que desborde la ventana.
    note, used = _feedback_note(ctx.get("question", ""), len(head) + len(tail), history_chars)
    messages.append({"role": "user", "content": head + note + tail})
    return messages, used


async def _remote_answer(
    ctx: Dict[str, Any], history: Optional[List[Dict[str, Any]]] = None
) -> Tuple[Optional[str], Dict[str, Any]]:
    """LLM para la respuesta del asistente: (texto o None, info de auditoría).

    El dict informa `grounding_rechazado` (el guard descartó el texto porque
    citaba Log_ID inexistentes — distinto de «el LLM ni siquiera se usó», que
    es lo que antes se confundía en no-stream) y `rag_examples_used` (cuántos
    ejemplos few-shot viajaron en el prompt). None solo si no hubo texto
    utilizable.
    """
    cfg = ai_config.get()
    if not cfg["enabled"] or not cfg["api_url"] or not ctx["evidence"]:
        # El LLM no va a correr: el bloque few-shot no se construye ni viaja,
        # así que informar un conteo > 0 sería un «ejemplos inyectados» en un
        # prompt que jamás se envió (la pasada determinista del harness contaría
        # cobertura que no existe).
        return None, {"grounding_rechazado": False, "rag_examples_used": 0}
    messages, used = _qa_messages(ctx, history)
    info: Dict[str, Any] = {"grounding_rechazado": False, "rag_examples_used": used}
    text = await ai_config.chat(messages, cfg=cfg)
    if not text:
        return None, info
    if not _grounding_ok(text, ctx["evidence"]):
        return None, {**info, "grounding_rechazado": True}
    return text, info


async def answer_question(
    question: str,
    hits: List[Dict[str, Any]],
    summary: Optional[Dict[str, Any]] = None,
    reference: Optional[Dict[str, Any]] = None,
    index_note: str = "",
    history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Responde una consulta en lenguaje natural sobre los eventos indexados.

    Siempre devuelve una respuesta utilizable: primero se construye la
    determinista (100 % anclada a la evidencia recuperada) y, si hay modelo
    remoto disponible y su respuesta cita solo Log_ID reales, se usa esa
    redacción. Nunca se queda sin respuesta ni inventa datos.

    `reference` es la base de conocimiento (Event IDs + técnicas ATT&CK) que se
    muestra APARTE, etiquetada como material del marco, nunca como hallazgo.
    `history` son los turnos previos de la conversación (memoria multi-turno)
    que el modelo ve antes de la consulta vigente; la recuperación de
    evidencias no cambia (se hace con la pregunta actual).
    """
    ctx = build_qa_context(question, hits, summary, reference=reference, index_note=index_note)
    # Consultas de agregación («¿cuántos...?», «¿qué hosts concentran...?»):
    # se responden con los conteos exactos del dataset, sin pasar por el modelo
    # ni por filas recuperadas (rigor analítico: cero cifras generadas).
    aggregate = _aggregate_answer(ctx)
    if aggregate is not None:
        return {**aggregate, "rag_examples_used": 0}

    fallback = _qa_fallback(ctx)
    if not ctx["evidence"]:
        return {**fallback, "rag_examples_used": 0}

    remote, info = await _remote_answer(ctx, history)
    if remote:
        return {
            **fallback,
            "answer": remote,
            "engine": "llm",
            "model": ai_config.get()["model"],
            "confidence": fallback["confidence"],
            "rag_examples_used": info["rag_examples_used"],
        }
    # El modelo no se usó o su texto fue descartado: se entrega el determinista
    # pero dejando constancia del rechazo del guard (auditoría; en stream ya
    # existía el evento fallback con este warning, aquí estaba invisible).
    warnings = list(fallback.get("warnings") or [])
    if info["grounding_rechazado"] and "grounding_rechazado" not in warnings:
        warnings.append("grounding_rechazado")
    return {**fallback, "warnings": warnings,
            "rag_examples_used": info["rag_examples_used"]}


async def stream_question(
    question: str,
    hits: List[Dict[str, Any]],
    summary: Optional[Dict[str, Any]] = None,
    reference: Optional[Dict[str, Any]] = None,
    index_note: str = "",
    history: Optional[List[Dict[str, Any]]] = None,
):
    """Versión en streaming de ``answer_question`` para el endpoint SSE.

    Emite, en orden, eventos (``type``) pensados para la UI del chat:

    1. ``meta``  – evidencias, bloque MITRE, contadores y confianza: todo lo
       rápido se calcula ANTES de arrancar el modelo, así el analista ve las
       tarjetas mientras la respuesta se escribe.
    2. ``token`` – trozos de texto del LLM (0..n).
    3. ``final`` – texto final + motor (``llm`` si pasó el grounding).
       Si no hay LLM (apagado/caído/sin evidencia) va directo, sin tokens.
    4. ``fallback`` – el LLM falló a mitad de camino o citó Log_ID
       inexistentes: lo transmitido se DESCARTA y se entrega la respuesta
       determinista con un ``warning`` explicando el motivo.

    Nunca termina sin respuesta (mismo contrato que ``answer_question``).
    """
    ctx = build_qa_context(question, hits, summary, reference=reference, index_note=index_note)

    aggregate = _aggregate_answer(ctx)
    if aggregate is not None:
        yield {"type": "final", **aggregate, "rag_examples_used": 0}
        return

    fallback = _qa_fallback(ctx)

    # El prompt se construye ANTES del meta: así el conteo de ejemplos
    # few-shot viaja ya en el primer evento (la UI puede auditar cuánto
    # criterio del analista se inyectó sin esperar al final).
    used = 0
    messages: Optional[List[Dict[str, str]]] = None
    if ctx["evidence"]:
        cfg_check = ai_config.get()
        if cfg_check["enabled"] and cfg_check["api_url"]:
            messages, used = _qa_messages(ctx, history)

    yield {
        "type": "meta",
        "evidence": ctx["evidence"],
        "reference": ctx["reference"],
        "summary_line": fallback["summary_line"],
        "confidence": fallback["confidence"],
        "warnings": fallback["warnings"],
        "mode": fallback["mode"],
        "dataset_rows": ctx["dataset_rows"],
        "rag_examples_used": used,
    }

    if not ctx["evidence"]:
        yield {"type": "final", **fallback, "rag_examples_used": 0}
        return

    cfg = ai_config.get()
    if not cfg["enabled"] or not cfg["api_url"]:
        yield {"type": "final", **fallback, "rag_examples_used": used}
        return

    pieces: List[str] = []
    interrupted = False
    try:
        async for piece in ai_config.chat_stream(messages, cfg=cfg):
            pieces.append(piece)
            yield {"type": "token", "text": piece}
    except Exception as exc:  # noqa: BLE001 – red/caída del LLM a mitad
        interrupted = True
        logger.warning("Streaming del LLM interrumpido: %s", exc)

    text = "".join(pieces).strip()
    if interrupted or not text:
        # Texto parcial NUNCA se entrega como si estuviera acabado: un «El host
        # DC-01 tuvo in…» cortado a mitad induciría a error al analista.
        # Si no llegó ni un token, el LLM ni siquiera arrancó (caída o timeout
        # de conexión): se avisa «sin respuesta»; si arrancó y se cortó, «inter-
        # rumpido», que es más grave porque había texto descartado.
        warning = (
            "llm_sin_respuesta" if interrupted and not pieces else
            "stream_interrumpido" if interrupted else
            "llm_sin_respuesta"
        )
        yield {
            "type": "fallback",
            **fallback,
            "warning": warning,
            "rag_examples_used": used,
        }
        return
    if not _grounding_ok(text, ctx["evidence"]):
        # Un solo Log_ID inventado invalida TODO el texto transmitido:
        # se descarta y se muestra la respuesta determinista con aviso.
        yield {"type": "fallback", **fallback, "warning": "grounding_rechazado",
               "rag_examples_used": used}
        return
    yield {
        "type": "final",
        **fallback,
        "answer": text,
        "engine": "llm",
        "model": cfg["model"],
        "rag_examples_used": used,
    }
