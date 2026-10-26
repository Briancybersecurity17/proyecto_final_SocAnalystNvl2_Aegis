"""Motor de filtros del dashboard: filtrado de DataFrames y detección de facetas.

Vive aparte de `main` para testear la lógica sin levantar la API y para que
todos los endpoints que muestran filas (`/rows`, `/summary`, `/facets`)
apliquen exactamente las mismas reglas desde un único lugar.
"""

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd


@dataclass
class FilterParams:
    """Parámetros de filtrado de una petición, tal como llegan de la URL.

    Los canónicos (ventana, nivel, sospechosos, result, q) son fijos; las
    facetas son pares columna → valores seleccionados, rellenados por
    `parse_filters` con los parámetros que no son canónicos.
    """
    from_dt: datetime | None = None
    to_dt: datetime | None = None
    level: str = ""            # ∈ LEVEL_ORDER
    suspicious: bool = False
    result: str = ""           # match exacto sobre _result_label
    q: str = ""                # texto libre en columnas originales
    facets: dict[str, list[str]] = field(default_factory=dict)   # OR dentro de un campo


def apply_filters(df: pd.DataFrame, p: FilterParams) -> pd.DataFrame:
    """Devuelve `df` con los filtros activos combinados con AND.

    Cada filtro replica la regla de `/rows` (main.py:809-826) para que la
    tabla, el resumen y el panel de facetas enseñen las mismas filas: si las
    reglas divergieran, los KPI no cuadrarían con lo que ve el analista.
    """
    # 1. Ventana inclusiva sobre Timestamp. Con filtro activo las filas NaT
    #    caen fuera (comparar NaT da False); sin filtro no se tocan.
    if p.from_dt is not None:
        df = df[df["Timestamp"] >= p.from_dt]
    if p.to_dt is not None:
        df = df[df["Timestamp"] <= p.to_dt]

    # 2. Nivel exacto sobre la columna derivada _level.
    if p.level:
        df = df[df["_level"] == p.level]

    # 3. Solo sospechosos: el toggle en False no restringe nada.
    if p.suspicious:
        df = df[df["_suspicious"]]

    # 4. Result con la misma guardia que /rows:813-815, para que un archivo
    #    sin columna Result se filtre en silencio en vez de reventar.
    #    `_result_label` es una función de `main` y se importa aquí dentro
    #    porque `main` importará `filters`: a nivel de módulo el import
    #    circular rompería el arranque.
    if p.result.strip() and "Result" in df.columns:
        from main import _result_label

        wanted = p.result.strip().lower()
        df = df[df["Result"].map(_result_label).str.lower() == wanted]

    # 5. Texto libre solo sobre columnas originales del Excel: las derivadas
    #    (_level, _reason, _suspicious) no son datos del archivo y harían
    #    match con textos que el analista no ve en la tabla (misma regla
    #    que /rows:816-826).
    if p.q.strip():
        needle = p.q.strip().lower()
        search_cols = [c for c in df.columns if not str(c).startswith("_")]
        if search_cols:
            mask = df[search_cols].astype(str).apply(
                lambda col: col.str.lower().str.contains(needle, regex=False, na=False)
            ).any(axis=1)
            df = df[mask]

    # 6. Facetas: OR dentro de un campo, AND entre campos (cada máscara se
    #    aplica sobre el resultado de la anterior). Igualdad exacta
    #    insensible a mayúsculas; una faceta sin valores no restringe.
    for col, values in p.facets.items():
        if not values:
            continue
        wanted_values = {str(v).lower() for v in values}
        df = df[df[col].astype(str).str.lower().isin(wanted_values)]

    return df
