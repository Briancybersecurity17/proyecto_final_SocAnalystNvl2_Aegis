"""Tarea 4: prompt endurecido del informe (4 campos) + num_predict 1600.

Estilo `test_chat_stream.py`: `ai_config.chat` se falsifica con
`monkeypatch.setattr` para capturar el prompt y las opciones que envía
`_remote_narrative`, devolviendo una respuesta fija — sin tocar Ollama.

El `ctx` es sintético e incluye `event_id_counts` y `severity_mapping` (desde
la tarea 5 los propaga `build_context`; aquí se fija el contrato del PROMPT,
no el del wiring) — por eso `_remote_narrative` se invoca directamente con el
ctx sintético.
"""

import asyncio
import json
import re

import pytest

import ai_service

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

# Misma forma que el CTX de test_informe_validador: cifras coherentes y las
# claves que el prompt endurecido cita (event_id_counts, severity_mapping).
_CTX = {
    "file_name": "registro.xlsx",
    "sheet_names": ["Hoja1"],
    "rows": 174,
    "columns": ["Log_ID", "Timestamp", "Source_IP", "Severity"],
    "risk": "alto",
    "risk_label": "Alto",
    "level_counts": {"critico": 59, "alto": 35, "medio": 0, "bajo": 80},
    "suspicious_events": 23,
    "suspicious_by_ip": [{"ip": "10.10.2.15", "events": 12, "hosts": 2}],
    "event_id_counts": {"4625": 102, "4624": 40},
    "severity_mapping": [
        {"raw": "Warning", "level": "alto", "count": 35, "percentage": "20,1%"},
    ],
}


@pytest.fixture
def ai(tmp_path, monkeypatch):
    """Configuración aislada en tmp, LLM encendido para que el informe llame."""
    monkeypatch.setattr(ai_service.ai_config, "CONFIG_PATH", tmp_path / "ai_config.json")
    ai_service.ai_config.update({"enabled": True, "num_predict": 512})
    return ai_service.ai_config


@pytest.fixture
def informe(ai, monkeypatch):
    """Falsifica `ai_config.chat` y devuelve un ejecutable de `_remote_narrative`.

    `correr(**config)` actualiza la config ANTES de la llamada (para probar
    el suelo de num_predict con valores distintos del default) y devuelve la
    captura: messages, cfg, num_predict y el resultado parseado.
    """
    captura: dict = {}

    async def chat_falso(messages, temperature=None, cfg=None, num_predict=None):
        captura["messages"] = messages
        captura["temperature"] = temperature
        captura["cfg"] = cfg
        captura["num_predict"] = num_predict
        return _RESPUESTA

    monkeypatch.setattr(ai, "chat", chat_falso)

    def correr(**config):
        if config:
            ai.update(config)
        captura["resultado"] = asyncio.run(ai_service._remote_narrative(_CTX))
        return captura

    return correr


def _user_prompt(captura: dict) -> str:
    """El user prompt es el último mensaje enviado al modelo."""
    mensajes = captura["messages"]
    assert mensajes[-1]["role"] == "user"
    return mensajes[-1]["content"]


# ---------------------------------------------------------------------------
# 4a. Alcance y formato del prompt
# ---------------------------------------------------------------------------


def test_prompt_pide_solo_las_cuatro_claves_de_prosa(informe):
    prompt = _user_prompt(informe())
    for clave in ("executive_summary", "methodology", "correlation", "conclusions"):
        assert f'"{clave}"' in prompt, f"falta la clave {clave} en el prompt"
    # findings/action_plan son del sistema: NO se piden como claves a producir
    # (no aparece el patrón de clave JSON «"x":») y tampoco campos «id».
    assert not re.search(r'"(?:findings|action_plan)"\s*:', prompt)
    assert not re.search(r'"id"\s*:', prompt)
    # Pero sí se nombran explícitamente como prohibidos, para que el modelo
    # no las emita por inercia del prompt anterior.
    assert "findings" in prompt
    assert "action_plan" in prompt


def test_prompt_bloque_reglas_duras_completas(informe):
    """Bloque 3: sin frases repetidas, sin tooling inventado, sin
    recomendaciones genéricas vacías (spec §6.3), español formal."""
    prompt = _user_prompt(informe())
    assert "repetid" in prompt
    assert "SIEM" in prompt
    assert "genéricas" in prompt
    assert "formal" in prompt


# ---------------------------------------------------------------------------
# 4a. Reglas duras: cifras de los DATOS, ejemplo prohibido, event_id_counts
# ---------------------------------------------------------------------------


def test_prompt_contiene_el_ejemplo_prohibido_y_la_prohibicion_de_reasignar(informe):
    prompt = _user_prompt(informe())
    # El ejemplo literal del brief, marcado como LO PROHIBIDO.
    assert "97 registros de Event ID 4625" in prompt
    assert "PROHIBIDO" in prompt
    assert "reasignar" in prompt
    # «N de Event ID X» solo si N = event_id_counts[X].
    assert "event_id_counts" in prompt


def test_prompt_bloque_estructura_espeja_la_regla_c(informe):
    """Lo que el validador exige por campo, el prompt lo pide campo a campo."""
    prompt = _user_prompt(informe())
    assert "registros" in prompt          # executive: total «N registros»
    assert "sospechosos" in prompt        # executive: total de sospechosos
    assert "numerados" in prompt          # methodology: pasos numerados
    assert "severity_mapping" in prompt   # methodology: claves originales
    assert "suspicious_by_ip" in prompt   # correlation: primera IP + conteo
    assert "reproducib" in prompt         # conclusions: reproducibilidad


def _seccion(prompt: str, campo: str) -> str:
    """La línea del bloque 2 correspondiente a un campo concreto."""
    idx = prompt.index(f"- {campo}:")
    return prompt[idx:prompt.index("\n", idx)]


def test_prompt_conclusions_pide_el_literal_nivel_por_nivel(informe):
    """Regla C exige «nivel X» (regex `_mentions_level`) TAMBIÉN en conclusions.

    El prompt solo pedía ese formato en executive_summary; en conclusions
    decía «distribución completa de niveles» sin exigir la palabra «nivel».
    El modelo entonces escribía «Crítico: 97 ▸ Alto: 8 …» (sin «nivel») y el
    validador rechazaba el campo con la misma función → 3/4. El espejo
    prompt↔validador debe ser completo: conclusions también pide «nivel» +
    su nombre.
    """
    prompt = _user_prompt(informe())
    seccion = _seccion(prompt, "conclusions")
    assert "«nivel»" in seccion, (
        "la sección conclusions no pide nombrar cada nivel con «nivel» + su "
        "nombre, pero el validador (regla C / _mentions_level) lo exige"
    )
    assert "+ su nombre" in seccion


def test_prompt_executive_sigue_pidiendo_el_literal_nivel(informe):
    """El espejo existente en executive_summary no se rompe con el fix."""
    prompt = _user_prompt(informe())
    seccion = _seccion(prompt, "executive_summary")
    assert "«nivel»" in seccion


# ---------------------------------------------------------------------------
# 4b. Payload y presupuesto
# ---------------------------------------------------------------------------


def test_payload_incluye_event_id_counts(informe):
    """El ctx completo (con event_id_counts) viaja en la sección DATOS."""
    prompt = _user_prompt(informe())
    datos = prompt.split("DATOS:", 1)[1]
    assert '"event_id_counts"' in datos
    assert '"4625": 102' in datos


def test_informe_usa_num_predict_1600(informe):
    captura = informe()
    assert captura["num_predict"] == 1600
    # La respuesta falsa se parsea: el pipeline completo funciona.
    assert captura["resultado"]["executive_summary"] == "Resumen ejecutivo."


def test_num_predict_1600_es_un_suelo_no_un_techo(informe):
    """Un tope configurado MAYOR se respeta; -1 (sin límite) también."""
    assert informe(num_predict=2048)["num_predict"] == 2048
    assert informe(num_predict=-1)["num_predict"] == -1


def test_informe_conserva_num_ctx_8192(informe):
    """El suelo de ventana de la llamada del informe no cambia."""
    captura = informe()
    assert captura["cfg"]["num_ctx"] >= 8192
