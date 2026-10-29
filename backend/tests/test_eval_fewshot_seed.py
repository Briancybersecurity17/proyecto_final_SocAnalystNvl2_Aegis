"""Tests de la siembra few-shot versionada (issue 5 – ruling del spec §6).

El aislamiento (store temporal + DELETE /chat por caso) impide que
`remember_implicit` acumule ejemplos durante la corrida: sin siembra,
`rag_examples_used` es 0 por construcción y el diagnóstico §6 (cobertura
few-shot) no discrimina nada — siempre reporta 0%.

El ruling elegido: sembrar el store aislado desde un snapshot versionado en
git (`eval/fixtures/fewshot_seed.json`). Para que la siembra sea recuperable
hay que aislar TAMBIÉN la colección semántica del feedback
(`FEEDBACK_COLLECTION`): comparte cliente con el RAG y, con la colección
real intacta, `similar()` recibe hits cuyos ids no están en el JSON sembrado
y los descarta sin caer a léxico — el seed sería muerto y §6 seguiría
reportando 0%.
"""
import json

import pytest

import feedback_store
from eval import runner

SEED = runner.SEED_PATH


def _seed_ids():
    return {e["id"] for e in json.loads(SEED.read_text(encoding="utf-8"))}


def test_el_snapshot_fewshot_esta_versionado_y_es_valido():
    # Es un artefacto de medición: si falta o es inválido, la corrida entera
    # pierde el diagnóstico §6 sin que nadie se dé cuenta.
    assert SEED.exists(), "falta el snapshot few-shot versionado en git"
    entries = json.loads(SEED.read_text(encoding="utf-8"))
    assert len(entries) >= 2
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "ids duplicados en el snapshot"
    for e in entries:
        assert e["kind"] in ("positive", "negative")
        for campo in ("id", "question", "answer", "document", "source"):
            assert e.get(campo), f"ejemplo {e.get('id')} sin «{campo}»"


def test_el_store_aislado_arranca_con_el_seed_y_nunca_con_la_memoria_real(tmp_path):
    reales = {e["id"] for e in feedback_store._load()}   # memoria real del analista
    with runner._isolated_store(tmp_path):
        cargados = feedback_store._load()
        assert cargados, "el store aislado quedó vacío: §6 seguiría muerto"
        ids = {e["id"] for e in cargados}
        assert not (ids & reales), "el store aislado filtró ejemplos REALES"
        assert ids == _seed_ids()
        assert (tmp_path / "feedback.json").exists()
    assert feedback_store._cache is None


def test_con_el_seed_la_pregunta_de_un_caso_real_recupera_criterio(tmp_path):
    # Si la pregunta del caso recupera ejemplos del seed, §6 puede decir
    # «se disparó y no ayudó»; sin seed devuelve "" y solo existe el fallo
    # de recall. Se usa la pregunta EXACTA de un caso: así el test es
    # determinista con recuperación léxica (sin chroma) y con semántica.
    pregunta = "¿Qué hizo la IP externa 203.0.113.74?"     # caso eq-ip-ext-34
    with runner._isolated_store(tmp_path):
        assert feedback_store.similar(pregunta), "el seed no es recuperable"
        bloque = feedback_store.prompt_block(pregunta)
        assert "CRITERIO DEL ANALISTA" in bloque


def test_el_store_aislado_parchea_la_coleccion_semantica_y_la_restaura(tmp_path):
    assert feedback_store.FEEDBACK_COLLECTION == "soc_feedback"
    with runner._isolated_store(tmp_path):
        assert feedback_store.FEEDBACK_COLLECTION == runner.EVAL_FEEDBACK_COLLECTION
    assert feedback_store.FEEDBACK_COLLECTION == "soc_feedback"


def test_sembrar_no_toca_la_coleccion_real_del_analista(tmp_path):
    # La colección semántica del feedback vive en el MISMO Chroma que el RAG
    # y no sigue la redirección del JSON: sin colección propia, el _reindex
    # del seed BORRARÍA los ejemplos indexados del analista real.
    col = feedback_store._get_collection()
    if col is None:
        pytest.skip("chroma no disponible en este entorno")
    reales = set(col.get(include=[])["ids"] or [])
    with runner._isolated_store(tmp_path):
        temp = feedback_store._get_collection()
        assert temp is not None
        assert set(temp.get(include=[])["ids"] or []) == _seed_ids()
    tras = feedback_store._get_collection()
    assert set(tras.get(include=[])["ids"] or []) == reales, \
        "la colección semántica REAL del analista quedó modificada"
    # Autolimpieza: la colección temporal no debe sobrevivir a la corrida.
    with pytest.raises(Exception):
        feedback_store.rag_store.get_client().get_collection(
            runner.EVAL_FEEDBACK_COLLECTION)
