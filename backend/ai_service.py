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
        "risk_levels": risk_levels,
        "suspicious_events": int(summary.get("suspicious_events", 0)),
        "suspicious_by_ip": summary.get("suspicious_by_ip") or [],
        "suspicious_evidence": [
            _evidence(e) for e in (summary.get("suspicious_details") or [])[:6]
        ],
    }


# ---------------------------------------------------------------------------
# Fallback local determinista
# ---------------------------------------------------------------------------
def _pct(count: int, total: int) -> str:
    return f"{(count / total) * 100:.1f}%" if total else "0,0%"


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
        "niveles (Crítico/Alto/Medio/Bajo). "
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
