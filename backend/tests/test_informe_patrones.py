"""Contrato prompt ↔ validador de los patrones conductuales (regla C).

Mismo espejo que `test_informe_prompt` / `test_informe_validador` para los
demás campos: lo que el prompt de «correlation» pide campo a campo, la regla
C lo exige en el mismo campo — si el ctx trae `behavior_patterns`, el modelo
DEBE citar al menos un patrón completo (sujeto + SU cifra) y el validador
rechaza el campo si no lo hace.

Cubre además dos riesgos concretos de este contrato nuevo:
 - la regla B (atribución) no debe rechazar la cifra de un patrón por
   compararla con el recuento GLOBAL del Event ID;
 - el fallback local debe contar los patrones aunque el LLM no aporte prosa.
"""

import asyncio
import json

import pytest

import ai_service
from ai_service import (
    _fallback_narrative,
    _rule_attribution,
    _rule_required,
    _validate_fields,
    build_context,
)

# Respuesta fija del LLM: JSON válido con exactamente las 4 claves de prosa.
_RESPUESTA = json.dumps(
    {
        "executive_summary": "Resumen ejecutivo.",
        "methodology": "Metodología.",
        "correlation": "Correlación.",
        "conclusions": "Conclusiones.",
    },
    ensure_ascii=False,
)

# Cifras del patrón elegidas para que coincidan con las del texto de abajo:
# 3 tipos de evento, 40 registros, 33 fallos… (anclables vía build_context).
_PATRONES = {
    "multi_event_ips": [
        {"ip": "203.0.113.74", "event_types": 3, "events": 40, "hosts": 1},
    ],
    "repeated_actions": [
        {"ip": "203.0.113.74", "host": "DC-01", "event_id": "4625", "count": 33},
    ],
    "multi_host_ips": [],
    "multi_host_users": [],
    "brute_force": [
        {"ip": "203.0.113.74", "failures": 33, "successes": 2,
         "hosts": 1, "events": 35},
    ],
    "totals": {
        "multi_event_ips": 1, "repeated_actions": 1, "multi_host_ips": 0,
        "multi_host_users": 0, "brute_force": 1,
    },
}

_SUMMARY = {
    "file_name": "registro.xlsx",
    "sheet_names": ["Hoja1"],
    "rows": 12,
    "level_counts": {"critico": 2, "alto": 3, "medio": 4, "bajo": 3},
    "event_id_counts": {"4625": 9},          # global ≠ 33 del patrón
    "suspicious_events": 5,
    "suspicious_by_ip": [{"ip": "10.10.2.15", "events": 4, "hosts": 2}],
    "behavior_patterns": _PATRONES,
}

# Correlación que cumple la regla C vieja (IP + su conteo) pero NO cita ningún
# patrón: es exactamente lo que el nuevo requisito debe rechazar.
SIN_PATRON = (
    "La IP 10.10.2.15 generó 4 eventos sospechosos en 2 hosts de la red "
    "analizada. Debe validarse contra los registros de autenticación."
)

# …y la misma prosa añadiendo un patrón COMPLETO (sujeto + cifra en la frase).
CON_PATRON = (
    SIN_PATRON
    + " Además, la IP 203.0.113.74 aparece en 3 tipos de evento con 40 "
    "registros en DC-01."
)


def _ctx_con_patrones():
    return build_context(_SUMMARY)


def _ctx_sin_patrones():
    return build_context({**_SUMMARY, "behavior_patterns": {}})


# ---------------------------------------------------------------------------
# Lado del prompt: lo que se le pide al modelo
# ---------------------------------------------------------------------------
@pytest.fixture
def ai(tmp_path, monkeypatch):
    """Configuración aislada en tmp, LLM encendido para que el informe llame."""
    monkeypatch.setattr(ai_service.ai_config, "CONFIG_PATH", tmp_path / "ai_config.json")
    ai_service.ai_config.update({"enabled": True,
                                 "api_url": "https://api.test/openai/v1/chat/completions"})
    return ai_service.ai_config


@pytest.fixture
def informe(ai, monkeypatch):
    """Falsifica `ai_config.chat` y devuelve un ejecutable de `_remote_narrative`."""
    captura: dict = {}

    async def chat_falso(messages, temperature=None, cfg=None):
        captura["messages"] = messages
        captura["temperature"] = temperature
        captura["cfg"] = cfg
        return _RESPUESTA

    monkeypatch.setattr(ai, "chat", chat_falso)

    def correr(**config):
        if config:
            ai.update(config)
        captura["resultado"] = asyncio.run(ai_service._remote_narrative(_ctx_con_patrones()))
        return captura

    return correr


def _user_prompt(captura: dict) -> str:
    mensajes = captura["messages"]
    assert mensajes[-1]["role"] == "user"
    return mensajes[-1]["content"]


def test_prompt_correlation_pide_citar_un_patron_completo(informe):
    """Espejo: el prompt exige sujeto + cifra del patrón, como la regla C."""
    prompt = _user_prompt(informe())
    assert "behavior_patterns" in prompt      # la clave viaja en DATOS
    assert "patrón conductual" in prompt      # y se nombra lo que hay que citar
    assert "tipos de evento" in prompt        # multi_event_ips
    assert "fuerza bruta" in prompt           # brute_force
    # La redacción de los patrones es el OBJETIVO del campo, no una lista suelta
    assert "no una lista de cifras sueltas" in prompt


def test_prompt_correlation_sigue_pidiendo_la_ip_de_suspicious_by_ip(informe):
    """El requisito viejo no se sustituye: se acumula al nuevo."""
    prompt = _user_prompt(informe())
    seccion = prompt[prompt.index("- correlation:"):]
    seccion = seccion[:seccion.index("\n")]
    assert "suspicious_by_ip" in seccion
    assert "behavior_patterns" in seccion


# ---------------------------------------------------------------------------
# Lado del validador: la regla C espejo del prompt
# ---------------------------------------------------------------------------
def test_regla_c_rechaza_correlation_sin_patron():
    motivos = _rule_required("correlation", SIN_PATRON, _ctx_con_patrones())
    assert any("patrón conductual" in motivo for motivo in motivos), motivos


def test_regla_c_acepta_correlation_con_el_patron_citado():
    assert _rule_required("correlation", CON_PATRON, _ctx_con_patrones()) == []


def test_regla_c_nombrar_la_ip_sin_su_cifra_no_es_citar_el_patron():
    """La IP suelta en OTRA frase no vale: la cifra tiene que ir con ella."""
    texto = (
        "La IP 10.10.2.15 generó 4 eventos sospechosos en 2 hosts de la red. "
        "Habría que revisar también la IP 203.0.113.74 por su comportamiento."
    )
    motivos = _rule_required("correlation", texto, _ctx_con_patrones())
    assert any("patrón conductual" in motivo for motivo in motivos), motivos


def test_regla_c_sin_patrones_en_el_ctx_no_exige_nada():
    """Solo se exige lo que los datos permiten (misma regla de oro de siempre)."""
    assert _rule_required("correlation", SIN_PATRON, _ctx_sin_patrones()) == []


# ---------------------------------------------------------------------------
# Regla B: la cifra del patrón no es el recuento GLOBAL del Event ID
# ---------------------------------------------------------------------------
def test_regla_b_acepta_la_cifra_del_patron_en_la_misma_frase():
    """«33 … Event ID 4625» con la IP del patrón en la frase: 33 es SU cifra.

    Sin el patrón en el ctx la misma frase se rechaza (el global de 4625 es
    9): el control negativo de abajo es la prueba de que la excepción vive en
    los datos y no en un perdón general a la regla B.
    """
    texto = (
        "La IP 203.0.113.74 registró 33 autenticaciones fallidas (Event ID "
        "4625) contra DC-01."
    )
    assert _rule_attribution(texto, _ctx_con_patrones()) == []
    motivos = _rule_attribution(texto, _ctx_sin_patrones())
    assert any("Event ID 4625" in motivo for motivo in motivos), motivos


def test_regla_b_sigue_castigando_la_cifra_ajena_al_patron():
    """Una cifra que NO es la del sujeto citado sigue rechazándose."""
    texto = (
        "La IP 203.0.113.74 registró 41 autenticaciones fallidas (Event ID "
        "4625) contra DC-01."
    )
    motivos = _rule_attribution(texto, _ctx_con_patrones())
    assert any("Event ID 4625" in motivo for motivo in motivos), motivos


# ---------------------------------------------------------------------------
# Orquestación completa: prosa válida → merge; y fallback con patrones
# ---------------------------------------------------------------------------
def test_validate_fields_acepta_la_correlation_con_patron():
    prosa = {
        "executive_summary": (
            "Se analizaron 12 registros de seguridad. El riesgo global es "
            "nivel crítico. Distribución: 2 en nivel crítico, 3 en nivel "
            "alto, 4 en nivel medio y 3 en nivel bajo. Se detectaron 5 "
            "eventos sospechosos."
        ),
        "methodology": (
            "1) Importación del archivo registro.xlsx. 2) Clasificación de "
            "severidad: Warning a crítico. 3) Reglas de sospecha sobre los "
            "campos de cada registro."
        ),
        "correlation": CON_PATRON,
        "conclusions": (
            "Riesgo global crítico. Total: 12 registros. Distribución: 2 en "
            "nivel crítico, 3 en nivel alto, 4 en nivel medio y 3 en nivel "
            "bajo. Toda cifra procede de los datos importados."
        ),
    }
    aceptados = _validate_fields(_ctx_con_patrones(), prosa)
    assert aceptados.get("correlation") == CON_PATRON


def test_validate_fields_rechaza_solo_la_correlation_sin_patron():
    prosa = {
        "executive_summary": (
            "Se analizaron 12 registros de seguridad. El riesgo global es "
            "nivel crítico. Distribución: 2 en nivel crítico, 3 en nivel "
            "alto, 4 en nivel medio y 3 en nivel bajo. Se detectaron 5 "
            "eventos sospechosos."
        ),
        "methodology": (
            "1) Importación del archivo registro.xlsx. 2) Clasificación de "
            "severidad: Warning a crítico. 3) Reglas de sospecha sobre los "
            "campos de cada registro."
        ),
        "correlation": SIN_PATRON,
        "conclusions": (
            "Riesgo global crítico. Total: 12 registros. Distribución: 2 en "
            "nivel crítico, 3 en nivel alto, 4 en nivel medio y 3 en nivel "
            "bajo. Toda cifra procede de los datos importados."
        ),
    }
    aceptados = _validate_fields(_ctx_con_patrones(), prosa)
    assert "correlation" not in aceptados
    assert set(aceptados) == {"executive_summary", "methodology", "conclusions"}


def test_fallback_local_cuenta_los_patrones():
    """Sin LLM el informe sigue narrando los patrones (motor local, no vacío)."""
    narrativa = _fallback_narrative(_ctx_con_patrones())
    correlacion = narrativa["correlation"]
    assert "Patrones conductuales" in correlacion
    assert "203.0.113.74" in correlacion
    assert "33" in correlacion          # fallos del patrón de fuerza bruta
    # Y las cifras siguen siendo anclables: el informe local pasa sus reglas
    assert ai_service._rule_anchor(correlacion, _ctx_con_patrones()) == []


def test_fallback_sin_patrones_no_inventa_nada():
    narrativa = _fallback_narrative(_ctx_sin_patrones())
    assert "Patrones conductuales" not in narrativa["correlation"]
