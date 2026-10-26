"""Motor de filtros del dashboard: filtrado de DataFrames y detección de facetas.

Vive aparte de `main` para testear la lógica sin levantar la API y para que
todos los endpoints que muestran filas (`/rows`, `/summary`, `/facets`)
apliquen exactamente las mismas reglas desde un único lugar.
"""

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
# `Request` solo se usa para leer la query string: fastapi no importa a
# `filters`, así que aquí no puede haber import circular (a diferencia del
# dual con `main`, que sí se importa dentro de las funciones).
from fastapi import Request


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


# Parámetros con significado fijo para la API: nunca se tratan como faceta,
# aunque el archivo tenga una columna con ese nombre (R11).
_CANONICOS = frozenset({"from", "to", "level", "suspicious", "result", "q"})

# Valores canónicos de los booleanos de la URL: cubren todo lo que el antiguo
# `bool = Query` de FastAPI aceptaba (yes/no/on/off/1/0…), para que una URL
# vieja siga filtrando igual. Cualquier otro valor no vacío es 400: un
# booleano ilegible que se interpretara como falso enseñaría TODAS las filas
# con el filtro «puesto», y un engaño silencioso es peor que un error (R18).
_BOOLEANO_VERDADERO = frozenset({"true", "1", "yes", "on"})
_BOOLEANO_FALSO = frozenset({"false", "0", "no", "off", ""})


def _parse_ventana(valor: str, nombre: str) -> datetime | None:
    """Fecha de la query → datetime; vacía → None; ilegible → ValueError.

    El `ValueError` lo recoge el endpoint para responder 400 (R10): una URL
    con una fecha escrita mal es un error del cliente, no un 500 del servidor.
    """
    texto = str(valor).strip()
    if not texto:
        return None
    try:
        momento = pd.to_datetime(texto)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"El parámetro «{nombre}» no es una fecha válida: {texto!r}") from exc
    if pd.isna(momento):
        raise ValueError(f"El parámetro «{nombre}» no es una fecha válida: {texto!r}")
    return momento   # pd.Timestamp, subclase de datetime: compara bien con la columna Timestamp


def parse_filters(
    request: Request,
    df: pd.DataFrame,
    reserved: set[str] | None = None,
) -> FilterParams:
    """Traduce los parámetros de la query a `FilterParams` (spec §4.4).

    Separa los canónicos (ventana, nivel, sospechosos, result, q) del resto,
    que solo se aceptan como faceta si el nombre coincide exactamente con una
    columna del archivo y no empieza por «_»: así una URL vieja o un
    parámetro de más no cambian el resultado ni revientan la petición.

    `reserved` es un conjunto de nombres que se ignoran por completo (ni
    canónicos ni facetas): lo usa `/rows` con page/page_size, que viajan
    SIEMPRE en la URL de la tabla, para que un Excel con una columna
    «page» no quede filtrado en silencio (R17).

    Lanza `ValueError` con mensaje legible cuando el filtro no tiene
    sentido (nivel fuera de catálogo, booleano ilegible, ventana invertida
    o fechas imparseables); el endpoint lo convierte en un 400.
    """
    query = request.query_params
    params = FilterParams()

    # 1. Ventana temporal: se valida siempre (una fecha ilegible o una
    #    ventana invertida son 400 aunque el archivo no tenga Timestamp),
    #    pero solo se aplica si la columna existe: en un archivo mínimo sin
    #    Timestamp no hay nada que acotar (spec §8) y aplicarla reventaría
    #    contra una columna inexistente.
    desde = _parse_ventana(query.get("from", ""), "from")
    hasta = _parse_ventana(query.get("to", ""), "to")
    if desde is not None and hasta is not None and desde > hasta:
        raise ValueError(
            f"El parámetro «from» ({desde.isoformat()}) es posterior a «to» "
            f"({hasta.isoformat()}): la ventana no puede quedar vacía"
        )
    if "Timestamp" in df.columns:
        params.from_dt, params.to_dt = desde, hasta

    # 2. Nivel: catálogo fijo. Un valor fuera de él no filtra nada y el
    #    panel enseñaría «0 filas» sin motivo, mejor avisar con 400.
    #    LEVEL_ORDER vive en `main` y se importa aquí dentro con el mismo
    #    patrón dual paquete/suelto que `_result_label` (ver apply_filters),
    #    porque `main` importa `filters` a nivel de módulo y un import aquí
    #    arriba crearía el import circular main ↔ filters.
    nivel = str(query.get("level", "")).strip()
    if nivel:
        try:  # paquete (uvicorn backend.main:app) o módulo suelto (pytest)
            from backend.main import LEVEL_ORDER
        except ImportError:  # pragma: no cover
            from main import LEVEL_ORDER
        if nivel not in LEVEL_ORDER:
            raise ValueError(
                f"Nivel «{nivel}» no válido; debe ser uno de: {', '.join(LEVEL_ORDER)}"
            )
        params.level = nivel

    # 3. Solo sospechosos: la query llega como true/false en muchas formas
    #    (el antiguo `bool = Query` de FastAPI aceptaba yes/no/on/off/1/0… y
    #    la UI puede enviar cualquiera de esas). Cualquier forma canónica se
    #    traduce a su booleano; un valor ilegible levanta ValueError → 400
    #    (R18): interpretarlo como falso enseñaría todas las filas con el
    #    filtro «puesto», y un engaño silencioso es peor que un error.
    bruto = str(query.get("suspicious", ""))
    crudo = bruto.strip().lower()
    if crudo in _BOOLEANO_VERDADERO:
        params.suspicious = True
    elif crudo in _BOOLEANO_FALSO:
        params.suspicious = False
    else:
        raise ValueError(
            f"El parámetro «suspicious» no es un booleano válido: {bruto!r}; "
            "use true/false, 1/0, yes/no u on/off"
        )
    params.result = str(query.get("result", ""))
    params.q = str(query.get("q", ""))

    # 4. Facetas: `multi_items` conserva los parámetros repetidos
    #    (?Hostname=DC-01&Hostname=WEB-01 → OR dentro del mismo campo).
    for nombre, valor in query.multi_items():
        # Los canónicos ya se consumieron arriba, los derivados (_level…)
        # no son columnas del archivo y los nombres reservados (paginación)
        # nunca son faceta (R17): /rows manda page/page_size en cada
        # petición, así que con un Excel que tuviera una columna «page» la
        # tabla se filtraría en silencio por page="1" en cada recarga.
        if reserved and nombre in reserved:
            continue
        if nombre in _CANONICOS or nombre.startswith("_"):
            continue
        if nombre in df.columns:
            params.facets.setdefault(nombre, []).append(valor)

    return params


def apply_filters(
    df: pd.DataFrame,
    p: FilterParams,
    skip_fields: set[str] | None = None,
) -> pd.DataFrame:
    """Devuelve `df` con los filtros activos combinados con AND.

    Cada filtro replica la regla de `/rows` (main.py:809-826) para que la
    tabla, el resumen y el panel de facetas enseñen las mismas filas: si las
    reglas divergieran, los KPI no cuadrarían con lo que ve el analista.

    `skip_fields` es un conjunto de nombres de campo cuyo filtro NO se
    aplica, para que el panel de facetas recunte cada campo ignorando la
    selección de ese mismo campo (spec §4.4, «para que el panel permita
    cambiar de valor»): `_level` salta el canónico `level`, `_suspicious`
    salta el canónico `suspicious`, `Result` salta el canónico `result` y
    cualquier otro nombre salta la entrada homónima de `p.facets`. Así,
    con `?User=jsmith` activo la faceta User sigue ofreciendo el resto de
    usuarios y con `?level=critico` la faceta Criticidad sigue ofreciendo
    los cuatro niveles. El resto de filtros (ventana, q, otras facetas)
    sigue aplicando: saltar solo apaga el filtro «propio» del campo.
    """
    def saltar(campo: str) -> bool:
        """¿Se omite el filtro «propio» de `campo` en esta pasada?

        El filtro de una faceta no siempre vive en `p.facets`: los
        canónicos `level`, `suspicious` y `result` son el filtro «propio»
        de `_level`, `_suspicious` y `Result`. Cada punto de aplicación
        pregunta solo por el nombre de su campo, y esta función es el
        único sitio donde se consulta `skip_fields`.
        """
        return bool(skip_fields) and campo in skip_fields

    # 1. Ventana inclusiva sobre Timestamp. Con filtro activo las filas NaT
    #    caen fuera (comparar NaT da False); sin filtro no se tocan.
    #    La ventana no es el filtro «de» ninguna faceta: nunca se salta.
    if p.from_dt is not None:
        df = df[df["Timestamp"] >= p.from_dt]
    if p.to_dt is not None:
        df = df[df["Timestamp"] <= p.to_dt]

    # 2. Nivel exacto sobre la columna derivada _level. Su «propia» faceta
    #    es _level, cuyo filtro es el canónico `level` (ver saltar).
    if p.level and not saltar("_level"):
        df = df[df["_level"] == p.level]

    # 3. Solo sospechosos: el toggle en False no restringe nada. Su faceta
    #    derivada _suspicious tiene el canónico `suspicious` como filtro.
    if p.suspicious and not saltar("_suspicious"):
        df = df[df["_suspicious"]]

    # 4. Result con la misma guardia que /rows:813-815, para que un archivo
    #    sin columna Result se filtre en silencio en vez de reventar.
    #    Su faceta homónima tiene el canónico `result` como filtro propio
    #    (ver saltar): con ?result=Failure activo la faceta sigue ofreciendo
    #    Success y Failure, para poder cambiar de valor desde el panel.
    #    `_result_label` es una función de `main` y se importa aquí dentro,
    #    con el mismo patrón dual paquete/suelto que kb_store:36-39, porque
    #    `main` importa `filters` a nivel de módulo (Tarea 4) y un import
    #    aquí arriba crearía el import circular main ↔ filters.
    if not saltar("Result") and p.result.strip() and "Result" in df.columns:
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
        # El panel de facetas recuenta este campo sin su propia selección
        # (skip_fields): contar User=jsmith dentro de la faceta User solo
        # enseñaría el valor ya elegido y el analista no podría cambiar.
        if saltar(col):
            continue
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
