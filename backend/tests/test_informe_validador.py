"""Tarea 3: validador del informe — reglas B (atribución) y C (contenido obligatorio).

Reglas puras, sin Ollama ni red: el `ctx` sintético reproduce la forma real
que devuelve `ai_service.build_context` (rows, level_counts, suspicious_*,
risk_levels con percentage…). `event_id_counts` y `severity_mapping` ya los
propaga `build_context` desde la tarea 5: aquí se siguen incluyendo
sintéticos para fijar el contrato de las reglas de forma independiente.
"""

from ai_service import (
    _anchors,
    _numbers,
    _pairs,
    _percentages,
    _rule_anchor,
    _rule_attribution,
    _rule_redundancy,
    _rule_required,
)

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
    "event_id_counts": {"4625": 102, "4624": 40},
    "severity_mapping": [
        {"raw": "Warning", "level": "critico", "count": 59, "percentage": "33,9%"},
    ],
}

# Cifras coherentes: nivel medio sin eventos (no se exige en el informe).
CTX_C = {
    "rows": 174,
    "risk": "alto",
    "level_counts": {"critico": 59, "alto": 35, "medio": 0, "bajo": 80},
    "suspicious_events": 23,
    "suspicious_by_ip": [
        {"ip": "10.10.2.15", "events": 12, "hosts": 2},
        {"ip": "10.10.9.7", "events": 8, "hosts": 1},
    ],
    "severity_mapping": [
        {"raw": "Warning", "level": "alto", "count": 35, "percentage": "20,1%"},
        {"raw": "Informational", "level": "bajo", "count": 80, "percentage": "45,9%"},
    ],
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


def test_regla_a_anchorea_cifras_embebidas_en_prosa_del_ctx():
    """Revisión funcional #2: el LLM escribía «4625 (33)» porque 33 aparece
    en el ctx embebido en prosa (justificación/evidencia), pero `_anchors`
    solo anclaba enteros sueltos → la regla A lo rechazaba con el falso
    positivo «número 33 sin ancla» y el informe entero caía al local.

    Las IP y fechas de esas cadenas NO generan anclas (los octetos ya los
    descarta `_numbers`), y una cifra que no aparece en ninguna parte del
    ctx sigue rechazándose igual."""
    ctx = {
        "rows": 715,
        "level_counts": {"critico": 97, "alto": 8, "medio": 7, "bajo": 603},
        "risk_justification": (
            "Nivel «Crítico»: 97 de 715 registros. El Event ID 4625 (33) "
            "concentra intentos de logon fallidos en DC-01 (10.10.2.15)."
        ),
    }
    assert _rule_anchor("El Event ID 4625 sumó 33 registros.", ctx) == []
    assert _rule_anchor("El Event ID 4625 sumó 34 registros.", ctx) == [
        "número 34 sin ancla"
    ]


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


# --- Regla B -------------------------------------------------------------

def test_pairs_etiqueta_en_ventana():
    """(número, etiqueta_canónica) solo con la etiqueta a ≤40 chars del número."""
    assert _pairs("Se detectaron 97 eventos del Event ID 4625.") == [
        (97.0, "event_id:4625")
    ]


def test_regla_b_event_id_con_cifra_correcta_pasa():
    texto = "El patrón dominante son 102 eventos del Event ID 4625."
    assert _rule_attribution(texto, CTX) == []


def test_regla_b_event_id_cifra_reasignada_rechaza():
    """«97 … Event ID 4625» reasigna la cifra real (102) → motivo con ≠."""
    texto = "El patrón dominante son 97 eventos del Event ID 4625."
    assert _rule_attribution(texto, CTX) == ["«97 … Event ID 4625» ≠ 102"]


def test_regla_b_event_id_no_contado_rechaza():
    """Citando un Event ID fuera del top-20 no hay cifra que lo salve."""
    texto = "Se observaron 5 eventos del Event ID 9999."
    assert _rule_attribution(texto, CTX) == [
        "«5 … Event ID 9999» sin dato en el contexto"
    ]


def test_regla_b_event_id_sin_counts_en_el_ctx():
    """Un ctx sin `event_id_counts` (antes de la tarea 5) también motiva."""
    ctx = {k: v for k, v in CTX.items() if k != "event_id_counts"}
    texto = "Se observaron 102 eventos del Event ID 4625."
    assert _rule_attribution(texto, ctx) == [
        "«102 … Event ID 4625» sin event_id_counts en el contexto"
    ]


def test_regla_b_nivel_contra_level_counts():
    assert _rule_attribution("Hubo 59 eventos en el nivel crítico.", CTX) == []
    assert _rule_attribution("Hubo 715 eventos en el nivel crítico.", CTX) == [
        "«715 … nivel crítico» ≠ 59"
    ]


def test_regla_b_pronombre_este_nivel_no_es_etiqueta():
    """«715 registros … clasificados en este nivel» no atribuye nada (trampa)."""
    texto = "Los 715 registros fueron clasificados en este nivel según la severidad."
    assert _rule_attribution(texto, CTX) == []


def test_regla_b_sospechosos_correcto_y_reasignado():
    assert _rule_attribution("Se detectaron 23 eventos sospechosos.", CTX) == []
    assert _rule_attribution("Se detectaron 50 eventos sospechosos.", CTX) == [
        "«50 … sospechosos» ≠ 23"
    ]


def test_regla_b_sospechosos_atribuido_a_ip_mencionada_pasa():
    """Tarea 8 (ruling 3): cifra de una IP mencionada con SU conteo real.

    Evidencia del harness sobre fixtures/sample.xlsx: «La IP 10.10.2.15 ha
    generado 15 eventos sospechosos» es correcto (suspicious_by_ip[10.10.2.15]
    = 15, total global 174) pero la regla lo rechazaba contra el total."""
    ctx = {**CTX, "suspicious_events": 174,
           "suspicious_by_ip": [{"ip": "10.10.2.15", "events": 15, "hosts": 9}]}
    texto = "La IP 10.10.2.15 ha generado 15 eventos sospechosos."
    assert _rule_attribution(texto, ctx) == []


def test_regla_b_sospechosos_total_inventado_sin_ip_sigue_cayendo():
    """Sin IP en la frase, la cifra sigue exigiendo el total global."""
    ctx = {**CTX, "suspicious_events": 174,
           "suspicious_by_ip": [{"ip": "10.10.2.15", "events": 15, "hosts": 9}]}
    assert _rule_attribution("Se detectaron 15 eventos sospechosos.", ctx) == [
        "«15 … sospechosos» ≠ 174"
    ]


def test_regla_b_sospechosos_conteo_erroneo_de_ip_mencionada_cae():
    """La IP está mencionada pero su conteo real no es el citado → rechazo."""
    ctx = {**CTX, "suspicious_events": 174,
           "suspicious_by_ip": [{"ip": "10.10.2.15", "events": 15, "hosts": 9}]}
    texto = "La IP 10.10.2.15 ha generado 99 eventos sospechosos."
    assert _rule_attribution(texto, ctx) == ["«99 … sospechosos» ≠ 174"]


def test_regla_b_sospechosos_conteo_de_otra_ip_no_mencionada_cae():
    """El conteo pertenece a una IP ausente de la frase → no salva."""
    ctx = {**CTX, "suspicious_events": 174,
           "suspicious_by_ip": [
               {"ip": "10.10.2.15", "events": 15, "hosts": 9},
               {"ip": "10.10.1.44", "events": 27, "hosts": 7},
           ]}
    texto = "La IP 10.10.1.99 ha generado 15 eventos sospechosos."
    assert _rule_attribution(texto, ctx) == ["«15 … sospechosos» ≠ 174"]


def test_regla_b_sospechosos_cifra_de_ip_no_adyacente_rechaza():
    """Revisión tarea 8 (round 1): dos IPs citadas, la cifra de la NO
    adyacente no vale.

    «…10.10.2.15 y 10.10.1.44 son las más activas. La IP 10.10.2.15 ha
    generado 27 eventos sospechosos» es FALSO (10.10.2.15 → 15; el 27 es de
    10.10.1.44): el hecho de que OTRA IP mencionada tenga `events == 27` no
    respalda la atribución a 10.10.2.15 (clase #1, misatribución)."""
    ctx = {**CTX, "suspicious_events": 174,
           "suspicious_by_ip": [
               {"ip": "10.10.2.15", "events": 15, "hosts": 9},
               {"ip": "10.10.1.44", "events": 27, "hosts": 7},
           ]}
    texto = ("10.10.2.15 y 10.10.1.44 son las más activas. La IP "
             "10.10.2.15 ha generado 27 eventos sospechosos.")
    assert _rule_attribution(texto, ctx) == ["«27 … sospechosos» ≠ 174"]


def test_regla_b_sospechosos_ip_adyacente_correcta_pasa_con_dos_ips():
    """Con dos IPs en la frase, la cifra de la adyacente SÍ pasa."""
    ctx = {**CTX, "suspicious_events": 174,
           "suspicious_by_ip": [
               {"ip": "10.10.2.15", "events": 15, "hosts": 9},
               {"ip": "10.10.1.44", "events": 27, "hosts": 7},
           ]}
    texto = ("La IP 10.10.1.44 ha generado 27 eventos sospechosos, "
             "por delante de 10.10.2.15.")
    assert _rule_attribution(texto, ctx) == []


def test_regla_b_sospechosos_ip_subcadena_no_respaldan_cifra():
    """Límites de token: «10.10.2.1» (events 15) NO respalda el 15 citado
    junto a «10.10.2.15» — la subcadena no es la IP adyacente."""
    ctx = {**CTX, "suspicious_events": 174,
           "suspicious_by_ip": [{"ip": "10.10.2.1", "events": 15, "hosts": 9}]}
    texto = "La IP 10.10.2.15 ha generado 15 eventos sospechosos."
    assert _rule_attribution(texto, ctx) == ["«15 … sospechosos» ≠ 174"]


def test_regla_b_sospechosos_ctx_sin_por_ip_degrada_al_total():
    """Sin `suspicious_by_ip` en el ctx, la IP del texto no salva nada:
    el helper degrada a False (`or []`) y rige el total global."""
    ctx = {**CTX, "suspicious_events": 174}
    ctx.pop("suspicious_by_ip", None)
    texto = "La IP 10.10.2.15 ha generado 15 eventos sospechosos."
    assert _rule_attribution(texto, ctx) == ["«15 … sospechosos» ≠ 174"]


def test_regla_b_errores_http_total():
    assert _rule_attribution("El servidor devolvió 41 respuestas con errores HTTP.", CTX) == []
    assert _rule_attribution("El servidor devolvió 12 respuestas con errores HTTP.", CTX) == [
        "«12 … errores http» ≠ 41"
    ]


def test_regla_b_codigo_http_contra_codes():
    assert _rule_attribution("Se contabilizaron 30 respuestas 404.", CTX) == []
    assert _rule_attribution("Se contabilizaron 12 respuestas 404.", CTX) == [
        "«12 … 404» ≠ 30"
    ]


def test_regla_b_listado_etiqueta_antes_rechaza_cifra_ajena():
    """Forma del listado del spec (§4, «Etiqueta: N (P %)»): la cifra va
    DESPUÉS de la etiqueta. La regla B era solo «hacia delante» y no
    emparejaba esta forma: el 4.625 se anclaba en la regla A (es la clave
    «4625» de event_id_counts) pero NO es el conteo del nivel crítico — la
    clase histórica 97→4625 en forma de listado."""
    texto = "Distribución: Nivel Crítico: 4.625 (64,6 %) del total."
    assert _rule_attribution(texto, CTX) == ["«4625 … nivel crítico» ≠ 59"]


def test_regla_b_listado_etiqueta_antes_cifra_correcta_pasa():
    """El listado correcto sí pasa, incluida la forma multi-fila: la cifra
    pegada con «:» a SU etiqueta gana aunque la fila anterior esté cerca."""
    assert _rule_attribution("Distribución: Nivel Crítico: 59 (33,9 %).",
                             CTX) == []
    multi = ("Distribución: Nivel Crítico: 59 (33,9 %), "
             "Nivel Medio: 40 (22,9 %).")
    assert _rule_attribution(multi, CTX) == []


def test_regla_b_event_ids_plural_rechaza_cifra_ajena_y_acepta_la_correcta():
    """El plural «Event IDs 4625» (spec: «Event ID(s) X») debe formar pareja:
    la regex exigía «event id <n>» sin plural y la atribución errónea evadía
    la regla B (solo la cazaba el eval con `eventid_pairs_bad`)."""
    assert _rule_attribution("Se revisaron 40 eventos de Event IDs 4625.",
                             CTX) == ["«40 … Event ID 4625» ≠ 102"]
    assert _rule_attribution("Se revisaron 102 eventos de Event IDs 4625.",
                             CTX) == []


def test_regla_b_paso_numerado_tras_etiqueta_no_atribuye():
    """El parejo «hacia atrás» no confunde el número de PASO siguiente con la
    cifra de la etiqueta: «…normaliza al nivel crítico. 3) Aplicación de
    reglas…» — el 3 pertenece al paso, no al nivel (regresión que rechazaba
    prosa correcta con pasos numerados, la forma que pide el prompt)."""
    texto = ("2) Clasificación de la severidad original: el valor Warning se "
             "normaliza al nivel crítico. 3) Aplicación de reglas de sospecha.")
    assert _rule_attribution(texto, CTX) == []


def test_regla_b_cifra_a_pie_de_frase_con_punto_no_es_paso():
    """La cifra seguida de «.» a pie de frase NO es número de paso.

    El descarte de «)»/«.» existe para no confundir el paso de metodología
    («…al nivel crítico. 3) Aplicación…», el 3 arranca frase) con la cifra
    de la etiqueta. Pero el listado de distribución del prompt cierra la
    última fila con punto («…Nivel Bajo: 603 (84,3 %).»): el 603 iba
    descartado como «paso», ganaba el 7 de «Nivel Medio: 7» por la ventana
    de 40 chars y la regla B rechazaba «7 … nivel bajo ≠ 603» — un falso
    positivo que tumbaba conclusions con la distribución completa correcta.
    """
    ctx = {"level_counts": {"critico": 97, "alto": 8, "medio": 7, "bajo": 603}}
    texto = ("Distribución: Nivel Crítico: 97, Nivel Alto: 8, "
             "Nivel Medio: 7, Nivel Bajo: 603.")
    assert _rule_attribution(texto, ctx) == []
    # Y la atribución es la correcta: cada etiqueta con SU cifra.
    parejas = dict((tag, val) for val, tag in _pairs(texto))
    assert parejas["nivel:bajo"] == 603.0
    assert parejas["nivel:medio"] == 7.0


def test_regla_b_mas_de_40_chars_no_atribuye():
    """Fuera de la ventana de 40 chars la etiqueta no atribuye al número."""
    texto = (
        "Se detectaron 50 eventos, revisados uno a uno con detenimiento "
        "durante la auditoría completa, sospechosos."
    )
    assert _rule_attribution(texto, CTX) == []


def test_regla_b_dos_numeros_usa_el_mas_cercano():
    """«174 … 23 sospechosos»: compiten y gana el más cercano (174 no rechaza)."""
    texto = "El dataset 174 incluye 23 eventos sospechosos."
    assert _rule_attribution(texto, CTX) == []


def test_regla_b_http_errors_o_result_errors():
    """La regla lee `http_errors` (brief) o `result_errors` (ctx real)."""
    ctx = {k: v for k, v in CTX.items() if k != "result_errors"}
    ctx["http_errors"] = {"total": 41, "codes": {"404": 30}}
    texto = "El servidor devolvió 12 respuestas con errores HTTP."
    assert _rule_attribution(texto, ctx) == ["«12 … errores http» ≠ 41"]


def test_regla_b_error_humano_no_es_error_http():
    """Sin «http» ni código 4xx/5xx cerca, «errores» NO se reclama.

    Falso positivo corregido (ruling de la tarea 5): «12 registros con error
    humano» no es http_errors.total y rechazarlo por ≠41 era injusto.
    """
    texto = "Se observaron 12 registros con error humano en la ingesta."
    assert _rule_attribution(texto, CTX) == []


def test_regla_b_errores_http_con_cifra_correcta_sigue_atribuyendo():
    """La pareja legítima «41 errores HTTP» se reclama y valida contra total."""
    assert _rule_attribution("La ventana registró 41 errores HTTP en total.", CTX) == []
    assert _rule_attribution("La ventana registró 12 errores HTTP en total.", CTX) == [
        "«12 … errores http» ≠ 41"
    ]


def test_regla_b_errores_con_codigo_5xx_cerca_tambien_atribuye():
    """Un código 4xx/5xx junto a «errores» habilita la atribución (sin «http»)."""
    ctx = dict(CTX, result_errors={"codes": {"500": 41}, "total": 41})
    assert _rule_attribution("Se contabilizaron 41 errores con código 500.", ctx) == []


# --- Regla C -------------------------------------------------------------

def test_regla_c_executive_acepta_prosa_completa():
    texto = (
        "Se analizaron 174 registros con riesgo alto. "
        "La distribución: nivel crítico 59, nivel alto 35 y nivel bajo 80. "
        "Se detectaron 23 eventos sospechosos que requieren revisión."
    )
    assert _rule_required("executive_summary", texto, CTX_C) == []


def test_regla_c_executive_rechaza_sin_distribucion_completa():
    texto = (
        "Se analizaron 174 registros con riesgo alto. "
        "La distribución: nivel crítico 59 y nivel alto 35. "
        "Se detectaron 23 eventos sospechosos."
    )
    assert _rule_required("executive_summary", texto, CTX_C) == [
        "falta nivel bajo de la distribución"
    ]


def test_regla_c_executive_rechaza_sin_registros_ni_sospechosos():
    """«174 registros» debe ir en la MISMA frase; el resto son omisiones."""
    texto = "El análisis con riesgo alto cubre nivel crítico, alto y bajo."
    assert _rule_required("executive_summary", texto, CTX_C) == [
        "falta «174 registros» en la misma frase",
        "falta nivel alto de la distribución",
        "falta nivel bajo de la distribución",
        "falta total de eventos sospechosos (23)",
    ]


def test_regla_c_executive_rechaza_sin_etiqueta_de_riesgo():
    texto = "Se analizaron 174 registros con sospechosos."
    motivos = _rule_required("executive_summary", texto, CTX_C)
    assert "falta etiqueta de riesgo «Alto»" in motivos


def test_regla_c_nivel_mediatrico_no_cuenta_como_nivel_medio():
    """Hallazgo 3: «nivel mediático» (de repercusión en prensa) no menciona
    el nivel medio — el patrón medi[oa]s? casaba «media» dentro de
    «mediatico» sin límite de palabra a la derecha (el eval ya usaba \\b)."""
    base = (
        "Se analizaron 174 registros con riesgo alto. "
        "La distribución: nivel crítico, nivel alto y nivel bajo. "
        "El incidente tuvo repercusión de nivel {x}. "
        "Se detectaron 23 eventos sospechosos."
    )
    assert _rule_required("executive_summary", base.format(x="mediático"),
                          CTX) == ["falta nivel medio de la distribución"]
    assert _rule_required("executive_summary", base.format(x="medio"),
                          CTX) == []


def test_regla_c_conclusions_acepta_y_rechaza():
    bueno = (
        "El riesgo general es alto sobre 174 registros. "
        "La distribución fue nivel crítico 59, nivel alto 35 y nivel bajo 80."
    )
    assert _rule_required("conclusions", bueno, CTX_C) == []
    malo = (
        "El riesgo general es alto sobre 174 registros, "
        "con nivel crítico 59 y nivel alto 35."
    )
    assert _rule_required("conclusions", malo, CTX_C) == [
        "falta nivel bajo de la distribución"
    ]


def test_regla_c_correlation_acepta_ip_con_su_conteo():
    texto = "La IP 10.10.2.15 concentró 12 eventos sospechosos sobre 2 hosts."
    assert _rule_required("correlation", texto, CTX_C) == []


def test_regla_c_correlation_rechaza_ip_sin_conteo():
    """La IP citada debe llevar SU número de eventos, no solo el nombre."""
    texto = "La IP 10.10.9.7 fue la más activa durante toda la ventana de análisis."
    assert _rule_required("correlation", texto, CTX_C) == [
        "falta IP con su conteo de eventos (p. ej. «10.10.2.15» con 12)"
    ]


def test_regla_c_methodology_acepta_pasos_y_clave_original():
    texto = (
        "Se importó el archivo Excel y se normalizó la severidad Warning "
        "al nivel alto; después se aplicaron reglas de sospecha."
    )
    assert _rule_required("methodology", texto, CTX_C) == []


def test_regla_c_methodology_rechaza_sin_pasos_ni_clave():
    texto = "Se cargó el archivo Excel tal como lo envió el equipo de operaciones."
    assert _rule_required("methodology", texto, CTX_C) == [
        "faltan pasos del método (1 de 3 grupos: importación, clasificación, sospecha)",
        "falta clave original de severidad (p. ej. «Warning»)",
    ]


def test_regla_c_methodology_sin_mapping_no_exige_claves():
    """Solo lo que los datos permiten: severity_mapping vacío → sin claves."""
    ctx = dict(CTX_C, severity_mapping={})
    texto = (
        "Se cargó el archivo Excel, se clasificó la severidad "
        "y se aplicaron reglas de sospecha."
    )
    assert _rule_required("methodology", texto, ctx) == []


def test_regla_c_clave_severidad_con_limite_de_palabra():
    """Hallazgo 4: la clave raw se busca con límites de palabra — «Warning»
    dentro de «warnings» (plural genérico) no satisface el requisito."""
    bueno = (
        "Se importó el archivo Excel y se normalizó la severidad Warning "
        "al nivel alto; después se aplicaron reglas de sospecha."
    )
    assert _rule_required("methodology", bueno, CTX) == []
    malo = (
        "Se importó el archivo Excel; se revisó la salida warnings del "
        "sistema y se aplicaron reglas de sospecha por severidad."
    )
    assert _rule_required("methodology", malo, CTX) == [
        "falta clave original de severidad (p. ej. «Warning»)"
    ]


def test_regla_c_campos_sin_reglas_no_exigen_nada():
    """Los campos fuera de los 4 (p. ej. findings) no tienen requisitos."""
    assert _rule_required("findings", "Prosas libres sin requisitos.", CTX_C) == []
