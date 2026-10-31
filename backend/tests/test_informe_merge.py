"""Tarea 5: `_merge` campo a campo, `field_sources` y orquestación con validador.

Estilo `test_informe_prompt`: `ai_config.chat` se falsifica (config aislada en
tmp) para recorrer el pipeline COMPLETO de `generate_report_narrative`
(fallback → remoto → validación A–D → merge) sin tocar Ollama.

El summary es sintético y coherente: los conteos cuadran entre `level_counts`,
`risk_levels`, `suspicious_*` y `event_id_counts`, de modo que la «respuesta
remota válida» se redacta contra las MISMAS reglas que exigirían estos datos
reales — si mintiera, el campo caería al fallback y el test lo diría.
"""

import asyncio
import json
import logging

import pytest

import ai_service
from ai_service import (
    _fallback_narrative,
    _merge,
    _validate_fields,
    build_context,
)

# Los 6 campos del contrato del informe y los 4 de prosa que pide el prompt.
CAMPOS = (
    "executive_summary",
    "methodology",
    "findings",
    "correlation",
    "conclusions",
    "action_plan",
)
PROSA = ("executive_summary", "methodology", "correlation", "conclusions")

# Summary sintético: 12 registros, riesgo Crítico, distribución completa,
# 5 sospechosos desde 10.10.2.15 (4 eventos) y 1 error HTTP 500. Los
# porcentajes de risk_levels cuadran con level_counts/rows (build_context los
# lee tal cual).
_SUMMARY = {
    "file_name": "registro.xlsx",
    "sheet_names": ["Hoja1"],
    "rows": 12,
    "columns": ["Log_ID", "Event_ID", "Source_IP", "Severity", "Result"],
    "risk": "critico",
    "risk_justification": "Nivel «Crítico»: 2 de 12 registros clasificados en este nivel.",
    "level_counts": {"critico": 2, "alto": 3, "medio": 4, "bajo": 3},
    "risk_levels": {
        "critico": {"count": 2, "percentage": 16.7},
        "alto": {"count": 3, "percentage": 25.0},
        "medio": {"count": 4, "percentage": 33.3},
        "bajo": {"count": 3, "percentage": 25.0},
    },
    "event_id_counts": {"4625": 4, "4624": 3},
    "severity_mapping": [
        {"raw": "Warning", "level": "critico", "count": 2, "percentage": 16.7},
        {"raw": "High", "level": "alto", "count": 3, "percentage": 25.0},
    ],
    "suspicious_events": 5,
    "suspicious_by_ip": [{"ip": "10.10.2.15", "events": 4, "hosts": 2}],
    "suspicious_details": [],
    "http_errors": {"codes": {"500": 1}, "total": 1},
}

# Prosa remota que pasa las reglas A–D contra el ctx de _SUMMARY: todas sus
# cifras (12, 2, 3, 4, 5, IPs) están en los datos y cada campo cubre lo que
# exige la regla C.
_PROSA_VALIDA = {
    "executive_summary": (
        "Se analizaron 12 registros del archivo registro.xlsx con riesgo global "
        "«Crítico». Distribución por criticidad: 2 registros en nivel crítico, "
        "3 en nivel alto, 4 en nivel medio y 3 en nivel bajo. Se detectaron "
        "5 eventos sospechosos que exigen revisión prioritaria."
    ),
    "methodology": (
        "1) Importación del archivo Excel registro.xlsx con lectura de todas "
        "las hojas. 2) Clasificación de la severidad original: el valor "
        "Warning se normaliza al nivel crítico. 3) Aplicación de reglas de "
        "sospecha sobre Event_ID y descripción. 4) Correlación entre IP de "
        "origen y hosts. 5) Redacción del informe solo con datos calculados."
    ),
    "correlation": (
        "La IP de origen con mayor alcance es 10.10.2.15: 4 eventos en "
        "2 hosts distintos. Varios hosts desde un mismo origen sugieren "
        "barrido y debe validarse contra los registros de autenticación."
    ),
    "conclusions": (
        "El riesgo global del archivo registro.xlsx es «Crítico» sobre "
        "12 registros. La distribución completa: 2 registros en nivel crítico, "
        "3 en nivel alto, 4 en nivel medio y 3 en nivel bajo. Toda cifra "
        "procede de los datos importados y es reproducible."
    ),
}

# El modelo, además de la prosa, emite findings/action_plan (aunque el prompt
# se lo prohíbe): el validador los descarta y _merge no los pisa nunca.
_RESPUESTA_CON_ESTRUCTURALES = json.dumps(
    {
        **_PROSA_VALIDA,
        "findings": [{"id": "EVT-1", "title": "Hallazgo inventado"}],
        "action_plan": [{"priority": "P1", "action": "Acción inventada"}],
    },
    ensure_ascii=False,
)

# «97» no existe en el ctx: la regla A rechaza SOLO ese campo.
_PROSA_NUMERO_INVENTADO = {
    **_PROSA_VALIDA,
    "executive_summary": _PROSA_VALIDA["executive_summary"].replace(
        "12 registros", "97 registros", 1
    ),
}

# Ningún campo puede pasar: 97 no está en el contexto en ninguno.
_PROSA_TODO_INVENTADO = {
    campo: f"Se analizaron 97 registros inventados para {campo}." for campo in PROSA
}


def _fallback():
    """El informe local de referencia para el mismo summary."""
    return _fallback_narrative(build_context(_SUMMARY))


@pytest.fixture
def ai(tmp_path, monkeypatch):
    """Config aislada en tmp con el LLM encendido: el informe intentará llamar."""
    monkeypatch.setattr(ai_service.ai_config, "CONFIG_PATH", tmp_path / "ai_config.json")
    ai_service.ai_config.update({"enabled": True})
    return ai_service.ai_config


def _activar_chat(monkeypatch, respuesta=None, fallo=None, captura=None):
    """Falsifica `ai_config.chat`: devuelve `respuesta`, lanza `fallo` o captura
    los messages enviados (para verificar el prompt que viaja al modelo)."""

    async def chat_falso(messages, temperature=None, cfg=None, num_predict=None):
        if captura is not None:
            captura.append(messages)
        if fallo is not None:
            raise fallo
        return respuesta

    monkeypatch.setattr(ai_service.ai_config, "chat", chat_falso)


def _generar(monkeypatch, **chat):
    """Pipeline completo `generate_report_narrative` con el chat falsificado."""
    _activar_chat(monkeypatch, **chat)
    return asyncio.run(ai_service.generate_report_narrative(_SUMMARY))


# ---------------------------------------------------------------------------
# 5a. `_merge`: overlay campo a campo + field_sources
# ---------------------------------------------------------------------------


def test_resumen_ejecutivo_no_embebe_la_justificacion_del_riesgo():
    """Revisión funcional #3: la misma justificación de riesgo (233 car.)
    aparecía 3 veces en el informe — §1, embebida en el executive_summary
    («Justificación global: …») y §3—. La prosa del resumen debe resumir,
    no replicar el párrafo que §3 ya explica junto a la tabla."""
    fallback = _fallback()
    justificacion = _SUMMARY["risk_justification"]
    assert justificacion not in fallback["executive_summary"]
    assert "Justificación global" not in fallback["executive_summary"]


def test_merge_nunca_pisa_findings_ni_action_plan():
    """Aunque `aceptados` traiga findings/action_plan, mandan los del fallback."""
    fallback = _fallback()
    merged = _merge(
        fallback,
        {
            "executive_summary": "Resumen redactado por el LLM.",
            "findings": "hallazgo infiltrado",
            "action_plan": "plan infiltrado",
        },
    )
    assert merged["findings"] == fallback["findings"]
    assert merged["action_plan"] == fallback["action_plan"]
    assert merged["executive_summary"] == "Resumen redactado por el LLM."
    assert merged["field_sources"]["findings"] == "local"
    assert merged["field_sources"]["action_plan"] == "local"


def test_merge_field_sources_con_las_seis_claves():
    """field_sources cubre los 6 campos; vacío no cuenta como aceptado."""
    fallback = _fallback()
    merged = _merge(
        fallback, {"executive_summary": "Aceptado.", "conclusions": "   "}
    )
    assert set(merged["field_sources"]) == set(CAMPOS)
    assert merged["field_sources"] == {
        "executive_summary": "llm",
        "methodology": "local",
        "findings": "local",
        "correlation": "local",
        "conclusions": "local",
        "action_plan": "local",
    }
    # Los campos no aceptados salen textualmente del fallback.
    assert merged["methodology"] == fallback["methodology"]
    assert merged["conclusions"] == fallback["conclusions"]


def test_merge_sin_aceptados_todo_local():
    merged = _merge(_fallback(), {})
    assert set(merged["field_sources"]) == set(CAMPOS)
    assert set(merged["field_sources"].values()) == {"local"}


# ---------------------------------------------------------------------------
# 5b/5c. Orquestación: remoto → validador → merge → source
# ---------------------------------------------------------------------------


def test_orquestacion_remoto_valido_da_prosa_llm_y_field_sources_mixto(ai, monkeypatch):
    captura = []
    narrativa = _generar(
        monkeypatch, respuesta=_RESPUESTA_CON_ESTRUCTURALES, captura=captura
    )

    # Los 4 campos de prosa aceptados vienen del LLM, carácter a carácter.
    for campo in PROSA:
        assert narrativa[campo] == _PROSA_VALIDA[campo]
        assert narrativa["field_sources"][campo] == "llm"

    # findings/action_plan salen del fallback: los del modelo se descartan.
    fallback = _fallback()
    assert narrativa["findings"] == fallback["findings"]
    assert narrativa["action_plan"] == fallback["action_plan"]
    assert narrativa["field_sources"]["findings"] == "local"
    assert narrativa["field_sources"]["action_plan"] == "local"

    assert narrativa["source"] == "llm"
    assert narrativa["model"] == (
        ai_service.ai_config.get()["model"] or "modelo remoto"
    )

    # build_context (whitelist) lleva las claves nuevas al prompt del modelo.
    prompt = captura[-1][-1]["content"]
    assert '"event_id_counts"' in prompt
    assert '"severity_mapping"' in prompt


def test_orquestacion_numero_inventado_rechaza_solo_ese_campo(ai, monkeypatch):
    narrativa = _generar(
        monkeypatch,
        respuesta=json.dumps(_PROSA_NUMERO_INVENTADO, ensure_ascii=False),
    )
    fallback = _fallback()

    # «97» sin ancla → ese campo cae al fallback y queda marcado como local.
    assert narrativa["executive_summary"] == fallback["executive_summary"]
    assert narrativa["field_sources"]["executive_summary"] == "local"

    # El resto de la prosa sí pasó: field_sources queda mixto.
    assert narrativa["methodology"] == _PROSA_VALIDA["methodology"]
    assert narrativa["field_sources"]["methodology"] == "llm"
    assert narrativa["source"] == "llm"


def test_orquestacion_si_nada_se_acepta_source_local(ai, monkeypatch):
    """source == "llm" SOLO con ≥1 campo de prosa aceptado."""
    narrativa = _generar(
        monkeypatch,
        respuesta=json.dumps(_PROSA_TODO_INVENTADO, ensure_ascii=False),
    )
    fallback = _fallback()
    for campo in CAMPOS:
        assert narrativa[campo] == fallback[campo]
    assert narrativa["source"] == "local"
    assert set(narrativa["field_sources"].values()) == {"local"}


def test_orquestacion_chat_que_lanza_devuelve_el_fallback_completo(
    ai, monkeypatch, caplog
):
    """Excepción en el chat → narrativa idéntica al fallback y source local."""
    with caplog.at_level(logging.WARNING, logger="soc.ai"):
        narrativa = _generar(monkeypatch, fallo=RuntimeError("ollama caído"))
    fallback = _fallback()
    for campo in CAMPOS:
        assert narrativa[campo] == fallback[campo]
    assert narrativa["source"] == "local"
    assert narrativa["model"] == fallback["model"]
    assert set(narrativa["field_sources"].values()) == {"local"}
    assert any("fallo del modelo remoto" in r.getMessage() for r in caplog.records)


def test_orquestacion_json_invalido_devuelve_el_fallback_completo(ai, monkeypatch):
    """Respuesta que no es JSON → mismo comportamiento que fallo total."""
    narrativa = _generar(monkeypatch, respuesta="esto no es JSON: {truncado")
    fallback = _fallback()
    for campo in CAMPOS:
        assert narrativa[campo] == fallback[campo]
    assert narrativa["source"] == "local"
    assert set(narrativa["field_sources"].values()) == {"local"}


# ---------------------------------------------------------------------------
# build_context: claves nuevas de la whitelist (spec §5 / ruling del controller)
# ---------------------------------------------------------------------------


def test_build_context_propaga_event_id_counts_y_severity_mapping():
    ctx = build_context(_SUMMARY)
    assert ctx["event_id_counts"] == {"4625": 4, "4624": 3}
    assert ctx["severity_mapping"] == _SUMMARY["severity_mapping"]


def test_build_context_claves_nuevas_vacias_sin_datos():
    """Un summary sin las claves (p. ej. análisis viejo) trae {} por defecto."""
    ctx = build_context({})
    assert ctx["event_id_counts"] == {}
    assert ctx["severity_mapping"] == {}


# ---------------------------------------------------------------------------
# `_validate_fields`: contrato de entrada/salida y rechazos sin excepciones
# ---------------------------------------------------------------------------


def test_validate_fields_acepta_prosa_valida_y_descarta_los_estructurales():
    """El validador itera SOLO los 4 campos de prosa, aunque el modelo emita más."""
    remote = json.loads(_RESPUESTA_CON_ESTRUCTURALES)
    remote["findings"] = [{"id": "EVT-1"}]
    remote["action_plan"] = ["infiltrado"]
    aceptados = _validate_fields(build_context(_SUMMARY), remote)
    assert set(aceptados) == set(PROSA)
    assert aceptados == _PROSA_VALIDA


def test_validate_fields_entrada_ausente_o_vacia_devuelve_vacio():
    ctx = build_context(_SUMMARY)
    assert _validate_fields(ctx, None) == {}
    assert _validate_fields(ctx, {}) == {}
    assert _validate_fields(ctx, {"executive_summary": "   "}) == {}


def test_validate_fields_loguea_el_motivo_del_rechazo(caplog):
    """Formato del log: «campo <f> rechazado (regla X): <motivo>»."""
    ctx = build_context(_SUMMARY)
    remota = {**_PROSA_VALIDA, "executive_summary": "Se analizaron 97 registros."}
    with caplog.at_level(logging.WARNING, logger="soc.ai"):
        aceptados = _validate_fields(ctx, remota)
    assert "executive_summary" not in aceptados
    assert set(aceptados) == set(PROSA) - {"executive_summary"}
    assert any(
        r.getMessage() == "campo executive_summary rechazado (regla A): número 97 sin ancla"
        for r in caplog.records
    )


def test_validate_fields_excepcion_en_regla_rechaza_solo_ese_campo(
    monkeypatch, caplog
):
    """Una regla que lanza NO se propaga: rechaza SOLO su campo y loguea."""
    ctx = build_context(_SUMMARY)
    original = ai_service._rule_required

    def regla_rota(field, text, context):
        if field == "correlation":
            raise RuntimeError("regla rota")
        return original(field, text, context)

    monkeypatch.setattr(ai_service, "_rule_required", regla_rota)
    with caplog.at_level(logging.WARNING, logger="soc.ai"):
        aceptados = _validate_fields(ctx, _PROSA_VALIDA)

    assert set(aceptados) == set(PROSA) - {"correlation"}
    assert any(
        "campo correlation rechazado (regla C):" in r.getMessage()
        and "RuntimeError" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Contrato HTTP: POST /report no cambia las claves viejas y expone
# field_sources con las 6 claves (spec §3.3 / eval `rep-field-sources`).
# ---------------------------------------------------------------------------


def test_report_endpoint_devuelve_field_sources_con_seis_claves(
    client, analysis_id, ai, monkeypatch
):
    _activar_chat(monkeypatch, respuesta=_RESPUESTA_CON_ESTRUCTURALES)
    res = client.post(f"/api/analysis/{analysis_id}/report")
    assert res.status_code == 200
    body = res.json()

    # Claves viejas intactas (el frontend lee executive_summary, findings…).
    for campo in CAMPOS:
        assert campo in body
        assert body["field_sources"][campo] in ("llm", "local")
    assert set(body["field_sources"]) == set(CAMPOS)
    assert body["source"] in ("llm", "local")
    assert body["model"]
