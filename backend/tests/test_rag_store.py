"""Aislamiento y contexto del índice RAG (revisión funcional #4).

Dos problemas reales detectados en vivo:

1. El índice semántico vivía en `backend/data/rag`, COMPARTIDO con la suite:
   cada corrida de tests subía los datasets sintéticos y `purge_except`
   (tras cada ingesta) borraba los eventos del análisis real del analista.
   Resultado en la app: ~1 h de «Sin coincidencias en el índice» hasta que
   la autoreparación volvía a indexar.
2. `Process_CommandLine` no se indexaba, así que una pregunta como
   «¿procesos PowerShell sospechosos?» no tenía la evidencia (la línea de
   comando con `-enc`) en los documentos recuperados.
"""
import rag_store


def test_el_indice_rag_de_los_tests_no_vive_en_el_directorio_real(rag_aislado):
    # El conftest debe redirigir RAG_DATA_PATH a un temporal ANTES de la
    # primera subida: si este test falla, la suite está escribiendo (y
    # purgando) el índice real del analista en backend/data/rag.
    directorio_real = (rag_store.Path(rag_store.__file__).parent / "data" / "rag").resolve()
    assert rag_store.RAG_DATA_PATH.resolve() != directorio_real
    assert rag_aislado != directorio_real


def test_build_document_indexa_la_linea_de_comando_del_proceso():
    # Sin Process_CommandLine indexado, el LLM no ve «-enc» ni «-nop» y
    # responde «no encontré procesos PowerShell» con las evidencias delante.
    doc = rag_store.build_document({
        "Log_ID": 207,
        "Hostname": "DC-01",
        "Event_ID": 4688,
        "Process_CommandLine": "powershell.exe -nop -w hidden -enc SQBFAFgA",
        "Event_Description": "A new process was created",
        "_level": "critico",
        "_reason": "Servicio nuevo instalado",
    })
    assert "Process_CommandLine: powershell.exe -nop -w hidden -enc SQBFAFgA" in doc


def test_metadata_y_prompt_llevan_la_linea_de_comando():
    # Indexar la línea de comando no basta: el prompt del LLM se arma desde
    # la METADATA (build_qa_context → _slim_for_prompt), que se quedaba sin
    # ella. El modelo seguía sin ver «-enc» y respondía «no encontré
    # procesos PowerShell» aunque el evento estuviera recuperado.
    meta = rag_store.build_metadata(
        {"Log_ID": 207, "Hostname": "DC-01", "Event_ID": 4688,
         "Process_CommandLine": "powershell.exe -enc SQBFAFgA"},
        analysis_id="aid", position=0,
    )
    assert meta.get("command_line") == "powershell.exe -enc SQBFAFgA"

    from ai_service import build_qa_context, _slim_for_prompt
    ctx = build_qa_context(
        "¿Procesos PowerShell sospechosos?",
        hits=[{"metadata": meta, "level": "critico", "suspicious": True,
               "score": 0.9, "document": ""}],
    )
    assert ctx["evidence"][0]["command_line"] == "powershell.exe -enc SQBFAFgA"
    slim = _slim_for_prompt(ctx["evidence"])
    assert slim[0]["command_line"] == "powershell.exe -enc SQBFAFgA"


def test_expand_query_conecta_powershell_con_su_evidencia():
    # «powershell» no estaba en el léxico: la pregunta en español no añadía
    # el vocabulario inglés de la evidencia real (4104 «Script Block Logging»,
    # 4688 «powershell.exe»), así que la recuperación quedaba a merced del
    # embedding. Debe ampliarse con esos términos, no solo devolver la
    # pregunta intacta (que también contiene «powershell»).
    expandida = rag_store.expand_query("¿Procesos PowerShell sospechosos?")
    assert "script block" in expandida.lower()


def test_agregado_responde_la_ip_que_concentra_eventos():
    # Revisión funcional #4: «¿qué IP concentra más eventos sospechosos?»
    # caía en el agregado pero la respuesta NUNCA mencionaba ninguna IP
    # (_aggregate_lines solo hablaba de niveles/hosts/Result). El resumen ya
    # trae suspicious_by_ip; hay que usarlo cuando la consulta pide IP.
    from ai_service import answer_question

    summary = {
        "rows": 715,
        "level_counts": {"critico": 97, "alto": 8, "medio": 7, "bajo": 603},
        "top_hosts": {"WEB-01": 210, "DC-01": 91},
        "top_hosts_by_level": {},
        "result_counts": {"Failure": 300},
        "result_hosts": {},
        "suspicious_events": 174,
        "unique_hosts": 11,
        "suspicious_by_ip": [
            {"ip": "203.0.113.74", "events": 33, "hosts": 9},
            {"ip": "10.10.2.40", "events": 21, "hosts": 2},
        ],
    }
    import asyncio
    res = asyncio.run(answer_question(
        "¿Qué IP concentra más eventos sospechosos?", [], summary=summary,
    ))
    assert res["mode"] == "aggregate"
    assert "203.0.113.74" in res["answer"]
