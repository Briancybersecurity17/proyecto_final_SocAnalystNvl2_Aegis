"""
test_feedback_store.py
-----------------------
Memoria de criterio del analista (few-shot): captura implícita y explícita,
recuperación para el prompt y degradación sin Chroma.

Todos los tests aíslan el JSON en un tmp_path para no tocar data/feedback.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import feedback_store  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Apunta TODAS las instancias de feedback_store a un JSON temporal.

    `ai_service` puede cargar `feedback_store` como módulo suelto o como
    `backend.feedback_store`: si son instancias distintas, parchear solo la
    local haría que el test de integración escribiera en el JSON real.
    """
    import ai_service

    mods = {m for m in (feedback_store, getattr(ai_service, "feedback_store", None))
            if m is not None}
    for mod in mods:
        monkeypatch.setattr(mod, "FEEDBACK_PATH", tmp_path / "feedback.json")
        monkeypatch.setattr(mod, "_cache", None)
        # Sin Chroma en los tests: ejercita el fallback léxico (que es además
        # lo que corre en una máquina sin dependencias semánticas).
        monkeypatch.setattr(mod, "_get_collection", lambda: None)
    yield
    for mod in mods:
        monkeypatch.setattr(mod, "_cache", None)


Q = "¿Qué hosts concentran inicios de sesión fallidos?"
A = "SRV-DC01 con 12 intentos fallidos (Log_ID 'EVT-10432'), todos de 10.0.3.44."


# ---------------------------------------------------------------------------
# Captura explícita (botón «Corregir»)
# ---------------------------------------------------------------------------
def test_add_correction_guarda_negativo_con_respuesta_buena():
    ex = feedback_store.add_correction(Q, A, bad_answer="No hay datos suficientes.")

    assert ex is not None
    assert ex["kind"] == "negative"
    assert ex["source"] == "explicit"
    assert ex["answer"] == A
    assert ex["bad_answer"] == "No hay datos suficientes."
    # Persiste en el JSON espejo (fuente de verdad).
    raw = json.loads(feedback_store.FEEDBACK_PATH.read_text(encoding="utf-8"))
    assert raw[0]["answer"] == A


def test_add_correction_sin_respuesta_no_guarda_nada():
    assert feedback_store.add_correction(Q, "   ") is None
    assert feedback_store.status()["total"] == 0


def test_correccion_misma_consulta_reemplaza_positivo_previo():
    """Corregir una consulta anula su ejemplo positivo: ya no es válido."""
    feedback_store.remember_implicit(Q, A, next_question=Q + " ¿y por qué?")
    assert feedback_store.status()["positive"] == 1

    feedback_store.add_correction(Q, "Corrección del analista.")

    st = feedback_store.status()
    assert st["positive"] == 0
    assert st["negative"] == 1


def test_correccion_repetida_no_duplica():
    feedback_store.add_correction(Q, A)
    feedback_store.add_correction(Q, A)
    assert feedback_store.status()["total"] == 1


# ---------------------------------------------------------------------------
# Captura implícita (señal de continuación)
# ---------------------------------------------------------------------------
def test_implicito_guarda_cuando_el_tema_continua():
    nxt = Q + " ¿y desde qué IP?"
    ex = feedback_store.remember_implicit(Q, A, next_question=nxt)

    assert ex is not None
    assert ex["kind"] == "positive"
    assert ex["source"] == "implicit"


def test_implicito_no_guarda_si_el_tema_cambia():
    nxt = "Háblame de los firewalls perimetrales y sus reglas."
    assert feedback_store.remember_implicit(Q, A, next_question=nxt) is None
    assert feedback_store.status()["total"] == 0


def test_implicito_no_guarda_sin_siguiente_turno():
    """Sin next_question no hay señal: no adivinamos."""
    assert feedback_store.remember_implicit(Q, A) is None
    assert feedback_store.status()["total"] == 0


def test_implicito_no_autovalida_su_propia_respuesta():
    """A → A': la «siguiente consulta» es la propia respuesta ⇒ bucle."""
    assert feedback_store.remember_implicit(Q, A, next_question=A) is None


def test_implicito_no_repite_ejemplo_ya_guardado():
    feedback_store.remember_implicit(Q, A, next_question=Q + " ¿y por qué?")
    # Misma respuesta: no se revalida a sí misma.
    assert feedback_store.remember_implicit(Q, A, next_question=Q + " ¿y por qué?") is None


def test_consulta_demasiado_corta_no_es_ejemplo():
    assert feedback_store.add_correction("¿IP?", A) is None


# ---------------------------------------------------------------------------
# Recuperación y bloque de prompt
# ---------------------------------------------------------------------------
def test_respuesta_larga_corta_en_frontera_de_palabra():
    """Un ejemplo que muere a media palabra enseña al modelo a truncar en seco."""
    long_answer = ("El host SRV-DC01 concentra los inicios de sesión. " * 40) + "514 (1"
    ex = feedback_store.add_correction(Q, long_answer)

    assert ex is not None
    assert len(ex["answer"]) <= feedback_store.EXAMPLE_CHARS
    assert not ex["answer"].endswith("514 (1")   # no cortó a media palabra
    assert ex["answer"].endswith((".", "sesión"))


def test_respuesta_corta_no_se_alter():
    assert feedback_store.add_correction(Q, A)["answer"] == A


def test_prompt_block_vacio_sin_ejemplos():
    assert feedback_store.prompt_block(Q) == ""


def test_prompt_block_incluye_correccion_y_etiqueta_criterio():
    feedback_store.add_correction(Q, A)

    block = feedback_store.prompt_block(Q)

    assert "CRITERIO DEL ANALISTA" in block
    assert A in block
    assert "DESCARTÓ" in block
    # Aviso de que es criterio, no un hallazgo de los registros.
    assert "NO un hallazgo" in block


def test_prompt_block_separa_bloques_negativos_y_positivos():
    feedback_store.add_correction(Q, "Respuesta descartada y corregida.")
    feedback_store.remember_implicit(Q + " ¿y los hosts?", "Respuesta que funcionó.",
                                     next_question=Q + " ¿y por qué?")
    block = feedback_store.prompt_block(Q, limit=2)

    assert "CRITERIO DEL ANALISTA" in block          # cabecera se mantiene
    assert "DESCARTÓ" in block and "NO un hallazgo" in block
    idx_descartado = block.index("DESCARTÓ")
    idx_funciono = block.index("LE FUNCIONÓ")
    assert idx_descartado < idx_funciono             # bloque negativo primero


def test_los_negativos_van_primero():
    feedback_store.remember_implicit(Q, A, next_question=Q + " ¿y por qué?")
    feedback_store.add_correction(Q, "Versión corregida por el analista.")

    examples = feedback_store.similar(Q, limit=2)

    assert examples[0]["kind"] == "negative"


def test_similar_limite_respetado():
    for i in range(6):
        feedback_store.add_correction(f"{Q} variante {i}", f"Respuesta {i}.")
    assert len(feedback_store.similar(Q, limit=2)) <= 2


# ---------------------------------------------------------------------------
# Límites y persistencia
# ---------------------------------------------------------------------------
def test_store_acotado_a_max_stored():
    for i in range(feedback_store.MAX_STORED + 15):
        feedback_store.add_correction(f"¿Cuántos eventos hay en el host {i}?",
                                      f"Respuesta sobre el host {i}.")
    assert feedback_store.status()["total"] <= feedback_store.MAX_STORED


def test_json_roto_degrada_a_vacio(tmp_path):
    bad = tmp_path / "roto.json"
    bad.write_text("{esto no es json", encoding="utf-8")
    feedback_store.FEEDBACK_PATH = bad
    feedback_store._cache = None

    assert feedback_store._load() == []
    assert feedback_store.prompt_block(Q) == ""


def test_clear_vacia_todo():
    feedback_store.add_correction(Q, A)
    removed = feedback_store.clear()

    assert removed == 1
    assert feedback_store.status()["total"] == 0
    assert feedback_store.prompt_block(Q) == ""


def test_warmup_sin_excepciones():
    assert feedback_store.warmup() is True


# ---------------------------------------------------------------------------
# Presupuesto del few-shot contra num_ctx (nunca desbordar la ventana)
# ---------------------------------------------------------------------------
def _stub_num_ctx(monkeypatch, value: int) -> None:
    """Fija num_ctx sin tocar el ai_config.json real de la app.

    Se parchea el módulo que ai_service USA realmente (`ai_service.ai_config`),
    no un import propio: así funciona igual se importe como `ai_config` o como
    `backend.ai_config`.
    """
    import ai_service

    cfg = ai_service.ai_config
    monkeypatch.setattr(cfg, "get", lambda: {**cfg.DEFAULTS, "num_ctx": value})


def test_fewshot_cabe_en_la_ventana_por_defecto(monkeypatch):
    from ai_service import _feedback_note

    _stub_num_ctx(monkeypatch, 4096)
    for i in range(3):
        feedback_store.add_correction(f"¿Qué hosts concentran inicios fallidos en SRV-DC0{i}?",
                                      f"SRV-DC0{i} concentra 12 intentos fallidos.")

    note = _feedback_note("¿Qué hosts concentran inicios fallidos?", 3900, 1400)

    assert note != ""
    assert "CRITERIO DEL ANALISTA" in note


def test_fewshot_cero_ejemplos_si_la_memoria_lleno_la_ventana(monkeypatch):
    """Si turnos + evidencia ya ocupan num_ctx, inyectar ejemplos haría que
    Ollama recortara el system prompt (reglas de no inventar Log_ID)."""
    from ai_service import _feedback_note

    _stub_num_ctx(monkeypatch, 2048)   # ventana vieja: no alcanza para history+fewshot
    for i in range(3):
        feedback_store.add_correction(f"¿Qué hosts concentran inicios fallidos en SRV-DC0{i}?",
                                      f"SRV-DC0{i} concentra 12 intentos fallidos.")

    # 4 turnos + evidencia: prompt real medido a 2253 tok con num_ctx=2048.
    note = _feedback_note("¿Qué hosts concentran inicios fallidos?", 3900, 1400)

    assert note == ""


def test_fewshot_sin_ejemplos_es_vacio(monkeypatch):
    from ai_service import _feedback_note

    _stub_num_ctx(monkeypatch, 4096)

    assert _feedback_note("¿Qué hosts concentran inicios fallidos?", 3900, 1400) == ""


def test_qa_messages_metel_el_bloque_y_no_le_pasa_de_num_ctx(monkeypatch):
    """Integración: el prompt completo construido por _qa_messages debe caber."""
    from ai_service import _qa_messages, build_qa_context

    _stub_num_ctx(monkeypatch, 4096)
    feedback_store.add_correction(
        "¿Qué hosts concentran inicios de sesión fallidos?",
        "SRV-DC01 concentra 12 intentos fallidos, todos desde 10.10.2.15.",
    )
    hits = [
        {
            "score": 0.7,
            "level": "alto",
            "metadata": {
                "log_id": f"EVT-{10432 + i}",
                "hostname": f"SRV-DC0{i}",
                "event_id": "4624",
                "description": "Inicio de sesión",
                "result": "Success",
            },
        }
        for i in range(8)
    ]
    history = [
        {"question": f"¿Qué hosts concentran eventos sospechosos de nivel {i}?",
         "answer": ("Encontré 8 de 8 eventos. Distribución: Alto 2, Medio 2, Bajo 4. "
                    "Hosts: DC-01 (3), WS-018 (3), FS-SRV01 (2). Event IDs: 4624, 4625.")}
        for i in range(4)
    ]
    ctx = build_qa_context("¿Qué hosts concentran inicios de sesión fallidos?",
                           hits, summary={"rows": 715})

    messages = _qa_messages(ctx, history)
    prompt_chars = sum(len(m["content"]) for m in messages)

    # ~2,5 chars/tok medido: el prompt no debe superar num_ctx por mucho margen.
    assert prompt_chars / 2.5 <= 4096
    assert "CRITERIO DEL ANALISTA" in messages[-1]["content"]
