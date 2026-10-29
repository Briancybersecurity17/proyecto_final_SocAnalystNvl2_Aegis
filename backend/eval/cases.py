"""cases.py — los 24 casos del harness con su verdad calculable.

Cada caso define `expected` como un cálculo pandas sobre la muestra fija
(`fixtures/sample.xlsx`, 715 filas del dataset real del bootcamp): NUNCA una
lista escrita a mano. Si el archivo cambia, la verdad cambia con él y los
tests de test_eval_cases.py la re-verifican antes de cualquier corrida.

Categorías:
  aggregate  — «¿cuántos...?» → entero exacto (scoring.number_in_answer).
  filter_set — «¿qué eventos...?» → set de Log_ID (precision@k o hit@k si estrecha).
  refusal    — consulta sin respuesta en los datos → el sistema debe negar
               con cero Log_ID citados (scoring.denial_ok).
  retrieval  — recuperación directa sin LLM (rag_store.query_events): mide el
               retriever por sí mismo, sin la redacción del modelo por medio.
  evidence_qa— respuesta del sistema citando IDs → precision@k sobre los
               relevantes del dataset (preguntas anchas: más de 2*k relevantes).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from main import normalize_level

FIXTURE = Path(__file__).parent / "fixtures" / "sample.xlsx"

K = 8   # n_results por defecto del endpoint /ask


def load_fixture() -> pd.DataFrame:
    """La muestra fija versionada (única fuente de verdad del eval)."""
    return pd.read_excel(FIXTURE)


@dataclass(frozen=True)
class Case:
    id: str
    categoria: str
    question: str
    expected: Callable[[pd.DataFrame], Any]
    unidades: str | None = None


def _ids(df: pd.DataFrame, mask) -> set[str]:
    return set(df.loc[mask, "Log_ID"].astype(str))


def _externa(df: pd.DataFrame) -> pd.Series:
    return ~df["Source_IP"].astype(str).str.startswith("10.")


def _critico(df: pd.DataFrame) -> pd.Series:
    return df["Severity"].map(normalize_level).eq("critico")


CASES: list[Case] = [
    # --- aggregate: la verdad es un entero del propio dataset ----------------
    Case("agg-criticos", "aggregate", "¿Cuántos eventos críticos hay?",
         lambda df: int(_critico(df).sum()), unidades="eventos"),
    Case("agg-fallos", "aggregate", "¿Cuántos intentos fallidos hay?",
         lambda df: int(df["Result"].astype(str).eq("Failure").sum()), unidades="intentos"),
    Case("agg-dc01", "aggregate", "¿Cuántos eventos hay en el host DC-01?",
         lambda df: int(df["Hostname"].astype(str).eq("DC-01").sum()), unidades="eventos"),
    Case("agg-externos", "aggregate", "¿Cuántos eventos vienen de IPs externas?",
         lambda df: int(_externa(df).sum()), unidades="eventos"),
    Case("agg-http500", "aggregate", "¿Cuántas peticiones devolvieron error 500?",
         lambda df: int(df["Result"].astype(str).eq("500").sum()), unidades="peticiones"),
    # --- filter_set: verdad = conjunto de Log_ID del dataset -----------------
    Case("fs-fail-ws018", "filter_set", "¿Qué eventos fallidos hay en WS-018?",
         lambda df: _ids(df, df["Result"].astype(str).eq("Failure")
                         & df["Hostname"].astype(str).eq("WS-018"))),
    Case("fs-tarea-4698", "filter_set", "¿Se creó alguna tarea programada?",
         lambda df: _ids(df, df["Event_ID"].eq(4698))),
    Case("fs-svc-7045", "filter_set", "¿Se instaló algún servicio nuevo en APP-SRV01?",
         lambda df: _ids(df, df["Event_ID"].eq(7045)
                         & df["Hostname"].astype(str).eq("APP-SRV01"))),
    Case("fs-admin-4625", "filter_set", "Intentos de logon fallido del usuario admin",
         lambda df: _ids(df, df["Event_ID"].eq(4625)
                         & df["User"].astype(str).eq(r"CORP\admin"))),
    Case("fs-crit-externos", "filter_set", "Eventos críticos desde IPs externas",
         lambda df: _ids(df, _critico(df) & _externa(df))),
    Case("fs-iis-errores", "filter_set", "Errores del servidor web",
         lambda df: _ids(df, df["Log_Source"].astype(str).str.contains("IIS", case=False)
                         & df["Result"].astype(str).isin(["500", "404"]))),
    Case("fs-priv-4672", "filter_set", "¿Hubo asignación de privilegios especiales?",
         lambda df: _ids(df, df["Event_ID"].eq(4672))),
    # --- refusal: no existe en los datos; el sistema debe negar ---------------
    # (expected -> True: el runner exige denial_ok ∧ cero IDs citados)
    Case("ref-terremoto", "refusal", "¿Hubo un terremoto en la oficina?", lambda df: True),
    Case("ref-malware", "refusal", "¿Hay muestras de malware en los registros?", lambda df: True),
    Case("ref-sqli", "refusal",
         "¿Hubo un ataque de inyección SQL contra la base de datos?", lambda df: True),
    # --- retrieval: puntuación directa del retriever (sin LLM) ---------------
    Case("ret-fuerza-bruta", "retrieval", "Fuerza bruta contra el controlador DC-01",
         lambda df: _ids(df, df["Event_ID"].eq(4625) & df["Hostname"].astype(str).eq("DC-01"))),
    Case("ret-ip-externa", "retrieval", "Eventos desde la IP 203.0.113.74",
         lambda df: _ids(df, df["Source_IP"].astype(str).eq("203.0.113.74"))),
    Case("ret-cierre-4634", "retrieval", "Cierres de sesión de cuenta",
         lambda df: _ids(df, df["Event_ID"].eq(4634))),
    Case("ret-proxy", "retrieval", "¿Qué peticiones permitió el proxy?",
         lambda df: _ids(df, df["Log_Source"].astype(str).str.contains("proxy", case=False)
                         & df["Result"].astype(str).eq("Allowed"))),
    Case("ret-powershell", "retrieval", "Actividad de PowerShell en los registros",
         lambda df: _ids(df, df["Log_Source"].astype(str).str.contains("powershell",
                                                                        case=False))),
    Case("ret-7045", "retrieval", "Servicio instalado con Event ID 7045",
         lambda df: _ids(df, df["Event_ID"].eq(7045))),
    # --- evidence_qa: el sistema cita IDs → precision@k sobre relevantes -----
    Case("eq-dc01", "evidence_qa", "¿Qué incidentes hay en DC-01?",
         lambda df: _ids(df, df["Hostname"].astype(str).eq("DC-01"))),
    Case("eq-jlopez", "evidence_qa", "¿Qué hizo el usuario CORP\\jlopez?",
         lambda df: _ids(df, df["User"].astype(str).eq(r"CORP\jlopez"))),
    Case("eq-ip-ext-34", "evidence_qa", "¿Qué hizo la IP externa 203.0.113.74?",
         lambda df: _ids(df, df["Source_IP"].astype(str).eq("203.0.113.74"))),
]
