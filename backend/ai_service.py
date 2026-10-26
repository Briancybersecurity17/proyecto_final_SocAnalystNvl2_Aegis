"""Asistente de IA para el análisis de datos y la redacción del informe.

Diseño (rigor analítico):
    El servicio recibe EXCLUSIVAMENTE datos ya calculados por el backend a
    partir de los registros importados (estadísticas + evidencias reales con
    Log_ID / Timestamp / Host / Event_ID). La IA redacta la narrativa, pero
    no puede aportar cifras ni hallazgos nuevos: todo queda anclado a los
    registros (grounding estricto, sin inventar conclusiones).

Dos modos de operación:
    1. Modelo remoto (opcional): si se define ``AI_API_URL`` (compatible con
       la API de OpenAI: ``/v1/chat/completions``) se envía el contexto y se
       usa la respuesta. ``AI_API_KEY`` y ``AI_MODEL`` son opcionales.
    2. Generador local determinista (fallback): si no hay URL configurada, si
       falla la llamada o si la respuesta no es JSON válido, se redacta el
       informe con plantillas basadas en los mismos números. Nunca se queda
       sin informe.

Uso:
    from ai_service import generate_report_narrative
    narrative = generate_report_narrative(summary)
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------------------------------------------------------
# Configuración del modelo remoto (opcional)
# ---------------------------------------------------------------------------
AI_API_URL = os.getenv("AI_API_URL", "").strip()
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini").strip()
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT", "60"))

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
        "result": str(example.get("Result", "") or "—"),
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


async def _remote_narrative(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Llama al modelo remoto. Devuelve None si no está configurado o falla."""
    if not AI_API_URL:
        return None

    payload: Dict[str, Any] = {
        "model": AI_MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Redacta el informe técnico a partir de estos datos calculados.\n"
                    "Responde SOLO con JSON válido con exactamente estas claves:\n"
                    '  "executive_summary": string,\n'
                    '  "methodology": string,\n'
                    '  "correlation": string,\n'
                    '  "conclusions": string,\n'
                    '  "findings": [ {"id": string, "title": string, '
                    '"level": "critico|alto|medio|bajo", "explanation": string} ],\n'
                    '  "action_plan": [ {"priority": "P1|P2|P3|P4", '
                    '"action": string, "rationale": string} ]\n'
                    "Los findings NO llevan evidencias (se añaden luego desde los "
                    "registros). No inventes datos.\n\nDATOS:\n"
                    + json.dumps(ctx, ensure_ascii=False, default=str)
                ),
            },
        ],
    }
    headers = {"Content-Type": "application/json"}
    if AI_API_KEY:
        headers["Authorization"] = f"Bearer {AI_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=AI_TIMEOUT) as client:
            resp = await client.post(AI_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
    except Exception:  # noqa: BLE001 – cualquier fallo cae al modo local
        return None

    # Compatible con OpenAI (choices[0].message.content) o {"content": "..."}
    content = ""
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        content = body.get("content") if isinstance(body, dict) else ""

    parsed = _clean_json(str(content or ""))
    return parsed if parsed else None


def _merge(fallback: Dict[str, Any], remote: Dict[str, Any]) -> Dict[str, Any]:
    """Usa la respuesta remota solo donde aporta contenido; nunca pierde datos."""
    merged = dict(fallback)
    merged["source"] = "llm"
    merged["model"] = AI_MODEL or "modelo remoto"

    for key in ("executive_summary", "methodology", "correlation", "conclusions"):
        value = remote.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()

    # Hallazgos: se reutilizan las evidencias del fallback por id/posición
    remote_findings = remote.get("findings")
    if isinstance(remote_findings, list) and remote_findings:
        findings: List[Dict[str, Any]] = []
        for idx, item in enumerate(remote_findings):
            if not isinstance(item, dict) or not item.get("title"):
                continue
            level = str(item.get("level", "")).lower()
            if level not in LEVEL_ORDER:
                level = fallback["findings"][idx]["level"] if idx < len(fallback["findings"]) else "medio"
            base = fallback["findings"][idx] if idx < len(fallback["findings"]) else {}
            findings.append(
                {
                    "id": item.get("id") or base.get("id") or f"H-{idx + 1:02d}",
                    "title": str(item["title"]),
                    "level": level,
                    "explanation": str(item.get("explanation") or base.get("explanation") or ""),
                    "evidence": base.get("evidence", []),
                }
            )
        if findings:
            merged["findings"] = findings

    remote_plan = remote.get("action_plan")
    if isinstance(remote_plan, list) and remote_plan:
        plan = []
        for item in remote_plan:
            if not isinstance(item, dict) or not item.get("action"):
                continue
            priority = str(item.get("priority", "P3")).upper()
            if priority not in ("P1", "P2", "P3", "P4"):
                priority = "P3"
            plan.append(
                {
                    "priority": priority,
                    "action": str(item["action"]),
                    "rationale": str(item.get("rationale") or ""),
                }
            )
        if plan:
            merged["action_plan"] = plan

    return merged


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
async def generate_report_narrative(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Redacta el informe técnico a partir del resumen calculado.

    Siempre devuelve un informe completo: primero se construye el basado en
    reglas (100% anclado a los registros) y, si hay modelo remoto disponible,
    se superpone su redacción sin perder evidencias ni cifras.
    """
    ctx = build_context(summary)
    fallback = _fallback_narrative(ctx)
    remote = await _remote_narrative(ctx)
    if remote:
        try:
            return _merge(fallback, remote)
        except Exception:  # noqa: BLE001 – nunca romper el informe
            return fallback
    return fallback


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
QA_SYSTEM_PROMPT = (
    "Eres un analista SOC de nivel 2 respondiendo una consulta sobre eventos de "
    "un SIEM. Respondes en español, con precisión técnica y sin adornos. "
    "Trabajas EXCLUSIVAMENTE con los eventos recuperados que se te entregan: si "
    "un dato no está en la evidencia, no lo menciones ni lo infieras. Cita cada "
    "afirmación con el Log_ID del evento que la respalda. Nunca inventes hosts, "
    "IPs, usuarios, fechas, cifras ni eventos. Si la evidencia no alcanza para "
    "responder, dilo explícitamente."
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
        lines.append(
            f"- `{item.get('id')} {item.get('name')}` ({item.get('tactic_es')}): "
            f"{item.get('descripcion_es')}"
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
            "**Evidencias (ordenadas por similitud):**",
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


async def _remote_answer(ctx: Dict[str, Any]) -> Optional[str]:
    """Llama al modelo remoto para redactar la respuesta. None si no aplica."""
    if not AI_API_URL or not ctx["evidence"]:
        return None

    reference = ctx.get("reference") or {}
    ref_note = ""
    if reference.get("available"):
        ref_note = (
            "\n\nREFERENCIA TÉCNICA (MITRE ATT&CK y glosario de Event IDs): úsala SOLO "
            "para explicar qué significa un Event ID o qué técnica representa. "
            "Jamás la presentes como un hallazgo de los registros y no afirmes que "
            "la técnica haya ocurrido salvo que los eventos lo demuestren.\n"
            + json.dumps(
                {"event_ids": reference.get("event_ids", []),
                 "techniques": reference.get("techniques", [])},
                ensure_ascii=False, default=str,
            )
        )

    payload: Dict[str, Any] = {
        "model": AI_MODEL,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": QA_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Responde la consulta usando SOLO estos eventos recuperados.\n"
                    "Cita los Log_ID entre comillas simples. Si no hay evidencia "
                    "suficiente, dilo.\n\nCONSULTA:\n"
                    + ctx["question"]
                    + "\n\nEVENTOS RECUPERADOS:\n"
                    + json.dumps(ctx["evidence"], ensure_ascii=False, default=str)
                    + ref_note
                ),
            },
        ],
    }
    headers = {"Content-Type": "application/json"}
    if AI_API_KEY:
        headers["Authorization"] = f"Bearer {AI_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=AI_TIMEOUT) as client:
            resp = await client.post(AI_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
    except Exception:  # noqa: BLE001 – cualquier fallo cae al modo local
        return None

    content = ""
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        content = body.get("content") if isinstance(body, dict) else ""

    text = str(content or "").strip()
    if not text:
        return None
    return text if _grounding_ok(text, ctx["evidence"]) else None


async def answer_question(
    question: str,
    hits: List[Dict[str, Any]],
    summary: Optional[Dict[str, Any]] = None,
    reference: Optional[Dict[str, Any]] = None,
    index_note: str = "",
) -> Dict[str, Any]:
    """Responde una consulta en lenguaje natural sobre los eventos indexados.

    Siempre devuelve una respuesta utilizable: primero se construye la
    determinista (100 % anclada a la evidencia recuperada) y, si hay modelo
    remoto disponible y su respuesta cita solo Log_ID reales, se usa esa
    redacción. Nunca se queda sin respuesta ni inventa datos.

    `reference` es la base de conocimiento (Event IDs + técnicas ATT&CK) que se
    muestra APARTE, etiquetada como material del marco, nunca como hallazgo.
    """
    ctx = build_qa_context(question, hits, summary, reference=reference, index_note=index_note)
    # Consultas de agregación («¿cuántos...?», «¿qué hosts concentran...?»):
    # se responden con los conteos exactos del dataset, sin pasar por el modelo
    # ni por filas recuperadas (rigor analítico: cero cifras generadas).
    aggregate = _aggregate_answer(ctx)
    if aggregate is not None:
        return aggregate

    fallback = _qa_fallback(ctx)
    if not ctx["evidence"]:
        return fallback

    remote = await _remote_answer(ctx)
    if remote:
        return {
            **fallback,
            "answer": remote,
            "engine": "llm",
            "model": AI_MODEL,
            "confidence": fallback["confidence"],
        }
    return fallback
