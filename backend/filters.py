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
    #    `_result_label` es una función de `main` y se importa aquí dentro,
    #    con el mismo patrón dual paquete/suelto que kb_store:36-39, porque
    #    `main` importará `filters` a nivel de módulo (Tarea 4) y un import
    #    aquí arriba crearía el import circular main ↔ filters.
    if p.result.strip() and "Result" in df.columns:
        try:  # paquete (uvicorn backend.main:app) o módulo suelto (pytest)
            from backend.main import _result_label
        except ImportError:  # pragma: no cover
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
        # URL vieja o dataset que no coincide: la faceta se ignora en
        # silencio, nunca debe tumbar el request con un 500 (spec §8).
        if col not in df.columns:
            continue
        if not values:
            continue
        wanted_values = {str(v).lower() for v in values}
        df = df[df[col].astype(str).str.lower().isin(wanted_values)]

    return df


@dataclass
class FacetField:
    """Campo filtrable detectado en el archivo, listo para el panel de facetas.

    `label` es lo que ve el analista: las columnas del archivo muestran su
    propio nombre, pero los conceptos derivados llevan nombre de dashboard
    («Criticidad»), no el interno (`_level`).
    """
    name: str
    label: str
    distinct: int
    values: list[dict]        # [{"value": str, "count": int}, ...] hasta `top_n`, ordenados por conteo


def _facet_field(serie: pd.Series, name: str, label: str, top_n: int) -> FacetField:
    """Campo de faceta con sus valores más frecuentes y su cardinalidad real.

    `distinct` cuenta todos los valores (para que la UI diga «+ N valores
    más») mientras `values` se acota a `top_n`: enviar todos los valores de
    una columna de miles de filas encarecería la respuesta sin aportar nada.
    """
    conteos = serie.value_counts().head(top_n)
    return FacetField(
        name=name,
        label=label,
        distinct=int(serie.nunique()),
        values=[{"value": str(v), "count": int(c)} for v, c in conteos.items()],
    )


def detect_facets(df: pd.DataFrame, top_n: int = 12) -> list[FacetField]:
    """Columnas categóricas del archivo aptas para el panel de facetas (spec §4.4).

    La detección se hace sobre el dataset completo (no sobre el filtrado):
    así la lista de campos es estable y un campo no desaparece del panel solo
    porque el filtro actual lo dejó vacío. Los umbrales de cardinalidad y
    longitud existen para que identificadores y textos libres
    (p. ej. Event_Description) no se ofrezcan como facetas con miles de
    valores inútiles.
    """
    campos: list[FacetField] = []
    limite = max(30, int(len(df) * 0.05))   # 5% de las filas, con suelo de 30

    for col in df.columns:
        name = str(col)
        # Las derivadas (_level, _reason, _suspicious) y los campos de
        # posición del registro no son facetas del archivo; Timestamp y
        # Log_ID se descartan por nombre aunque pasen el resto de reglas.
        if name.startswith("_") or name in ("Timestamp", "Log_ID"):
            continue
        serie = df[col]
        # Un número con demasiados valores distintos es una medida o un
        # identificador (montos, puertos, duraciones), no una categoría:
        # por encima de 30 no se ofrece como faceta aunque quepa en el 5%
        # de las filas.
        if pd.api.types.is_numeric_dtype(serie) and serie.nunique() > 30:
            continue
        # Cardinalidad baja: por encima del límite cada fila tendría casi su
        # propio valor y el panel no serviría para filtrar.
        if serie.nunique() > limite:
            continue
        # No es texto libre: si el valor medio supera los 40 caracteres la
        # lista de valores no aporta nada (p. ej. Event_Description).
        if serie.astype(str).str.len().mean() > 40:
            continue
        # Al menos un valor no vacío (spec §4.4): una columna de solo NaN
        # o espacios no tiene nada que ofrecer en el panel. También cubre
        # el DataFrame vacío (la media de arriba es NaN y no filtra nada).
        if not serie.dropna().astype(str).str.strip().ne("").any():
            continue
        campos.append(_facet_field(serie, name, name, top_n))

    # _level y _suspicious son conceptos del dashboard, no del archivo:
    # van siempre, al final, con su etiqueta en español.
    for name, label in (("_level", "Criticidad"), ("_suspicious", "Solo sospechosos")):
        if name in df.columns:
            campos.append(_facet_field(df[name], name, label, top_n))

    return campos
