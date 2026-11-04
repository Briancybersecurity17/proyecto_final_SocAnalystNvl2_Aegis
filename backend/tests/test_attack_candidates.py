# -*- coding: utf-8 -*-
"""Fichas candidatas: aprendizaje de ataques nuevos con aprobación humana.

TDD (rojo→verde) de la pieza 1 de la fase 3:

- `attack_candidates` guarda borradores en
  `data/attack_cards_candidates.json` con estado `pendiente`: NUNCA entran
  al catálogo hasta que un humano lo aprueba (nunca automático).
- Validación DETERMINISTA antes de guardar: tipos de regla cerrados,
  columnas y Event IDs reales del dataframe (`attack_cards.evaluate`),
  id/sinónimos que no colisionen con el catálogo y campos de ficha completos.
- `approve` mueve la ficha a `knowledge/attack_cards.json` e invalida la
  caché de `attack_cards` (`match_card` la ve al instante); `reject` solo
  cambia el estado y respeta la decisión (no se revive sola).
- `behavior_correlations.py` sigue intacto.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

import attack_cards
import attack_candidates


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
COLUMNAS = [
    "Log_ID", "Timestamp", "Hostname", "Event_ID", "User", "Source_IP", "Result",
]


def _df():
    """3× 4723 (umbral de la candidata) + 1× 4625 y 1× 4624."""
    filas = []
    for i in range(3):
        filas.append({
            "Log_ID": f"EVT-{i + 1:03d}", "Timestamp": "2026-01-10 08:00",
            "Hostname": "DC-01", "Event_ID": 4723, "User": "jsmith",
            "Source_IP": "10.10.2.15", "Result": "Success",
        })
    filas.append({
        "Log_ID": "EVT-004", "Timestamp": "2026-01-10 09:00",
        "Hostname": "DC-01", "Event_ID": 4625, "User": "jsmith",
        "Source_IP": "10.10.2.15", "Result": "Failure",
    })
    filas.append({
        "Log_ID": "EVT-005", "Timestamp": "2026-01-10 09:01",
        "Hostname": "DC-01", "Event_ID": 4624, "User": "jsmith",
        "Source_IP": "10.10.2.15", "Result": "Success",
    })
    return pd.DataFrame(filas, columns=COLUMNAS)


def _draft(idc="cambio_password_masivo", **extra):
    """Borrador válido completo (todos los campos obligatorios de ficha)."""
    draft = {
        "id": idc,
        "nombre": "Cambio de contraseña masivo",
        "que_es": "Muchas modificaciones de contraseña en corto tiempo.",
        "como_se_ve": "Eventos 4723 repetidos para el mismo usuario o host.",
        "event_ids": ["4723"],
        "mitre": ["T1098"],
        "sinonimos": ["cambio de contraseña masivo", "password reset masivo"],
        "regla": {"tipo": "event_presence", "min": 3},
        "umbrales": "≥3 eventos 4723 en el archivo.",
        "ejemplo": "Tres cambios de contraseña de jsmith en una hora.",
    }
    draft.update(extra)
    return draft


@pytest.fixture(autouse=True)
def aislado(tmp_path, monkeypatch):
    """Candidatas y catálogo en temporales: ni el JSON del analista ni el
    catálogo real se tocan (patrón de feedback_store / analysis_persist)."""
    monkeypatch.setattr(
        attack_candidates, "CANDIDATES_PATH",
        tmp_path / "attack_cards_candidates.json",
    )
    monkeypatch.setattr(attack_candidates, "_cache", None)
    # Catálogo: copia real en tmp — así las colisiones de id/sinónimo se
    # prueban contra las 15 fichas de verdad sin escribir el archivo real.
    catalogo = tmp_path / "attack_cards.json"
    catalogo.write_text(
        attack_cards.CATALOGO.read_text(encoding="utf-8"), encoding="utf-8",
    )
    monkeypatch.setattr(attack_cards, "CATALOGO", catalogo)
    monkeypatch.setattr(attack_cards, "_CACHE", None)
    yield tmp_path
    # La caché del catálogo vuelve a None: el próximo load relee el real.
    monkeypatch.setattr(attack_cards, "_CACHE", None)


# ---------------------------------------------------------------------------
# add_candidate: validación determinista + guardado como pendiente
# ---------------------------------------------------------------------------
def test_add_candidate_guarda_pendiente_con_su_evaluacion():
    res = attack_candidates.add_candidate(_draft(), "¿Hubo cambios de contraseña?", _df())

    assert res["ok"] is True
    assert res["motivo"] == "guardada"
    candidata = res["candidate"]
    assert candidata["id"] == "cambio_password_masivo"
    assert candidata["status"] == "pendiente"
    assert candidata["question"] == "¿Hubo cambios de contraseña?"
    # La evaluación viaja para que el revisor vea qué encontró la regla.
    assert candidata["evaluacion"]["applicable"] is True
    assert candidata["evaluacion"]["motivo"] == "ok"
    assert candidata["evaluacion"]["total"] == 1

    # Persistida en disco (fuente de verdad), no solo en RAM.
    crudo = json.loads(attack_candidates.CANDIDATES_PATH.read_text(encoding="utf-8"))
    assert crudo["candidates"][0]["id"] == "cambio_password_masivo"
    # Y filtrable por estado.
    assert len(attack_candidates.list_candidates("pendiente")) == 1
    assert attack_candidates.list_candidates("aprobada") == []
    assert len(attack_candidates.list_candidates()) == 1


def test_candidata_pendiente_no_entra_al_catalogo():
    attack_candidates.add_candidate(_draft(), "¿Hubo cambios de contraseña?", _df())

    # El catálogo NO cambió: match_card ni siquiera conoce la candidata.
    ids = [c["id"] for c in attack_cards.load_cards()]
    assert "cambio_password_masivo" not in ids
    assert attack_cards.match_card("¿hubo un cambio de contraseña masivo?") is None


def test_add_candidate_rechaza_regla_desconocida():
    draft = _draft(regla={"tipo": "eval_dinamico", "expr": "1 == 1"})
    res = attack_candidates.add_candidate(draft, "pregunta", _df())

    assert res["ok"] is False
    assert res["motivo"] == "regla_desconocida"
    assert attack_candidates.list_candidates() == []


def test_add_candidate_rechaza_columna_inventada():
    draft = _draft(regla={
        "tipo": "group_threshold", "columna": "Puerto", "min": 3,
        "conteo": {"Event_ID": "4625"},
    })
    res = attack_candidates.add_candidate(draft, "pregunta", _df())

    assert res["ok"] is False
    assert res["motivo"] == "sin_columna"
    assert "Puerto" in res["missing"]
    assert attack_candidates.list_candidates() == []


def test_add_candidate_rechaza_event_ids_ausentes():
    draft = _draft(event_ids=["9999"],
                   regla={"tipo": "event_presence", "min": 3})
    res = attack_candidates.add_candidate(draft, "pregunta", _df())

    assert res["ok"] is False
    assert res["motivo"] == "sin_event_ids"
    assert attack_candidates.list_candidates() == []


def test_add_candidate_rechaza_id_ya_en_el_catalogo():
    res = attack_candidates.add_candidate(_draft(idc="fuerza_bruta"), "p", _df())

    assert res["ok"] is False
    assert res["motivo"] == "id_duplicado"
    assert attack_candidates.list_candidates() == []


def test_add_candidate_rechaza_sinonimo_ya_cubierto():
    # Sinónimo exacto de una ficha existente: dos fichas con el mismo
    # sinónimo harían ambigua a match_card.
    res = attack_candidates.add_candidate(
        _draft(idc="fuerza_bruta_v2", sinonimos=["fuerza bruta"]), "p", _df(),
    )

    assert res["ok"] is False
    assert res["motivo"] == "sinonimo_duplicado"
    assert attack_candidates.list_candidates() == []


def test_add_candidate_rechaza_id_con_formato_invalido():
    res = attack_candidates.add_candidate(_draft(idc="Fuerza Bruta!"), "p", _df())

    assert res["ok"] is False
    assert res["motivo"] == "id_invalido"
    assert attack_candidates.list_candidates() == []


def test_add_candidate_rechaza_borrador_incompleto():
    draft = _draft()
    del draft["nombre"]
    del draft["umbrales"]
    res = attack_candidates.add_candidate(draft, "p", _df())

    assert res["ok"] is False
    assert res["motivo"] == "borrador_invalido"
    assert attack_candidates.list_candidates() == []


def test_add_candidate_rechaza_regla_ausente():
    draft = _draft()
    draft["regla"] = None
    res = attack_candidates.add_candidate(draft, "p", _df())

    assert res["ok"] is False
    assert res["motivo"] == "sin_regla"
    assert attack_candidates.list_candidates() == []


def test_add_candidate_sin_dataframe_no_valida():
    res = attack_candidates.add_candidate(_draft(), "p", None)

    assert res["ok"] is False
    assert res["motivo"] == "sin_datos"
    assert attack_candidates.list_candidates() == []


# ---------------------------------------------------------------------------
# Dedup: repetir la pregunta no llena el JSON de duplicados
# ---------------------------------------------------------------------------
def test_pendiente_duplicada_se_actualiza_en_lugar_de_duplicar():
    attack_candidates.add_candidate(_draft(), "primera pregunta", _df())
    res = attack_candidates.add_candidate(_draft(), "segunda pregunta", _df())

    assert res["ok"] is True
    assert res["motivo"] == "actualizada"
    candidatas = attack_candidates.list_candidates()
    assert len(candidatas) == 1
    assert candidatas[0]["question"] == "segunda pregunta"
    assert candidatas[0]["status"] == "pendiente"


def test_rechazada_no_se_revive():
    attack_candidates.add_candidate(_draft(), "p", _df())
    attack_candidates.reject("cambio_password_masivo")

    res = attack_candidates.add_candidate(_draft(), "p otra vez", _df())

    assert res["ok"] is False
    assert res["motivo"] == "rechazada_previa"
    assert len(attack_candidates.list_candidates()) == 1


# ---------------------------------------------------------------------------
# approve / reject: la decisión es humana y se refleja en el catálogo
# ---------------------------------------------------------------------------
def test_approve_mueve_la_ficha_al_catalogo_e_invalida_la_cache():
    attack_candidates.add_candidate(_draft(), "¿Hubo cambios de contraseña?", _df())
    # Fuerza la lectura del catálogo para que el cambio de caché sea observable.
    assert attack_cards.match_card("¿hubo un cambio de contraseña masivo?") is None

    res = attack_candidates.approve("cambio_password_masivo")

    assert res["ok"] is True
    assert res["motivo"] == "aprobada"
    assert res["candidate"]["status"] == "aprobada"
    # La caché se invalidó: match_card ve la ficha recién aprobada YA.
    card = attack_cards.match_card("¿hubo un cambio de contraseña masivo?")
    assert card is not None
    assert card["id"] == "cambio_password_masivo"
    # Y la regla evaluó igual que en la validación original.
    assert attack_cards.evaluate(card, _df())["applicable"] is True


def test_approve_es_idempotente_y_no_duplica_en_el_catalogo():
    attack_candidates.add_candidate(_draft(), "p", _df())
    attack_candidates.approve("cambio_password_masivo")
    res = attack_candidates.approve("cambio_password_masivo")

    assert res["ok"] is True
    assert res["motivo"] == "ya_aprobada"
    ids = [c["id"] for c in attack_cards.load_cards()]
    assert ids.count("cambio_password_masivo") == 1


def test_approve_con_id_inexistente_falla():
    res = attack_candidates.approve("no_existe")

    assert res["ok"] is False
    assert res["motivo"] == "no_existe"
    assert res["candidate"] is None


def test_reject_marca_rechazada_sin_tocar_el_catalogo():
    attack_candidates.add_candidate(_draft(), "p", _df())

    res = attack_candidates.reject("cambio_password_masivo")

    assert res["ok"] is True
    assert res["motivo"] == "rechazada"
    assert res["candidate"]["status"] == "rechazada"
    assert len(attack_candidates.list_candidates("rechazada")) == 1
    assert attack_cards.match_card("¿hubo un cambio de contraseña masivo?") is None


def test_reject_con_id_inexistente_falla():
    res = attack_candidates.reject("no_existe")

    assert res["ok"] is False
    assert res["motivo"] == "no_existe"


def test_rechazada_no_se_puede_aprobar():
    attack_candidates.add_candidate(_draft(), "p", _df())
    attack_candidates.reject("cambio_password_masivo")

    res = attack_candidates.approve("cambio_password_masivo")

    assert res["ok"] is False
    assert res["motivo"] == "rechazada_previa"
    assert attack_cards.match_card("¿hubo un cambio de contraseña masivo?") is None


# ---------------------------------------------------------------------------
# Aislamiento: el JSON real del analista jamás se escribe desde la suite
# ---------------------------------------------------------------------------
def test_el_path_real_del_analista_no_es_el_que_usa_la_suite():
    # Tras el fixture, el path activo es un tmp; el real apunta a data/.
    assert attack_candidates.CANDIDATES_PATH != attack_candidates.CANDIDATES_PATH_REAL
    assert attack_candidates.CANDIDATES_PATH.name == "attack_cards_candidates.json"
    assert Path(attack_candidates.CANDIDATES_PATH_REAL).parent.name == "data"
