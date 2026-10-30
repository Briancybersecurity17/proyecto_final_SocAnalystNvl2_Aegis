"""Tarea 2: validador del informe — reglas A (ancla numérica) y D (anti-redundancia).

Reglas puras, sin Ollama ni red: el `ctx` sintético reproduce la forma real
que devuelve `ai_service.build_context` (rows, level_counts, suspicious_*,
risk_levels con percentage…), porque más adelante el validador se alimentará
de análisis reales.
"""

from ai_service import _anchors, _numbers, _percentages, _rule_anchor, _rule_redundancy

# Cifras coherentes: 59/174 = 33,908…% → «33,9 %» cabe en la tolerancia ±0,1.
CTX = {
    "file_name": "registro.xlsx",
    "sheet_names": ["Hoja1"],
    "rows": 174,
    "columns": ["Log_ID", "Timestamp", "Source_IP", "Severity"],
    "risk": "alto",
    "risk_label": "Alto",
    "level_counts": {"critico": 59, "alto": 35, "medio": 40, "bajo": 40},
    "risk_levels": {
        "critico": {"label": "Crítico", "count": 59, "percentage": 33.9},
        "alto": {"label": "Alto", "count": 35, "percentage": 20.1},
    },
    "suspicious_events": 23,
    "suspicious_by_ip": [{"ip": "10.10.2.15", "events": 12, "hosts": 2}],
    "suspicious_evidence": [{"event_id": "4625", "source_ip": "10.10.2.15"}],
    "result_errors": {"codes": {"404": 30}, "total": 41},
}


# --- Regla A -------------------------------------------------------------

def test_regla_a_acepta_cifras_ancladas():
    """rows, suspicious_events, IP y % exacto de un nivel pasan sin motivos."""
    texto = (
        "Se analizaron 174 registros y se detectaron 23 eventos sospechosos. "
        "La IP 10.10.2.15 concentra la actividad, vista el 2026-01-10. "
        "El nivel crítico alcanzó 33,9 % del total."
    )
    assert _rule_anchor(texto, CTX) == []


def test_regla_a_rechaza_numero_sin_ancla():
    """«97» no existe en el ctx → «número 97 sin ancla»; el 4625 sí está."""
    texto = "Se registraron 97 registros de Event ID 4625."
    assert _rule_anchor(texto, CTX) == ["número 97 sin ancla"]


def test_regla_a_ip_y_fecha_no_generan_anclas_falsas():
    """Octetos de IP y partes de fecha no se leen como cifras sueltas."""
    texto = "La IP 10.10.2.15 fue vista el 2026-01-10, sin cifras nuevas."
    assert _rule_anchor(texto, CTX) == []


def test_numbers_formats_y_descarte_ip():
    """«1.234», «1,234», «1234» → 1234; IP y fecha quedan descartadas."""
    assert _numbers("vino 1.234, luego 1,234 y 1234") == [
        ("1.234", 1234.0),
        ("1,234", 1234.0),
        ("1234", 1234.0),
    ]
    assert _numbers("IP 10.10.2.15 del 2026-01-10") == []


def test_helpers_anchors_y_percentages_del_ctx():
    """Anclas = enteros + strings numéricos; porcentajes derivados del ctx."""
    anchors = _anchors(CTX)
    assert 174.0 in anchors and 23.0 in anchors  # rows y suspicious_events
    assert 4625.0 in anchors and 404.0 in anchors  # «4625» y «404» como strings
    assert 10.0 not in anchors  # los octetos de «10.10.2.15» no se anclan
    pcts = _percentages(CTX)
    assert any(abs(p - 33.908) < 0.01 for p in pcts)  # 59/174 derivado
    assert any(abs(p - 13.218) < 0.01 for p in pcts)  # 23/174 derivado
    assert 33.9 in pcts and 20.1 in pcts  # «percentage» presentes en el ctx


# --- Regla D -------------------------------------------------------------

def test_regla_d_rechaza_frase_duplicada():
    """Frase larga (≥5 palabras) repetida ≥2 veces en el mismo campo."""
    frase = "174 eventos sospechosos presentan correlación con múltiples hosts"
    texto = f"Se confirmó que {frase}. Se confirmó que {frase}."
    motivos = _rule_redundancy(texto)
    assert len(motivos) == 1
    assert motivos[0].startswith("frase repetida: «se confirmó que 174")


def test_regla_d_acepta_mismo_contenido_en_campos_distintos():
    """La llamada es por campo: el mismo texto en dos campos no rechaza."""
    campo = "El análisis cubre 174 registros con evidencia completa y trazable."
    assert _rule_redundancy(campo) == []
    assert _rule_redundancy(campo) == []


def test_regla_d_acepta_frases_cortas_repetidas():
    """Una enumeración repite frases de <5 palabras sin que eso sea rechazo."""
    texto = "Nivel crítico. Nivel crítico. Alto. Alto. Nivel crítico."
    assert _rule_redundancy(texto) == []
