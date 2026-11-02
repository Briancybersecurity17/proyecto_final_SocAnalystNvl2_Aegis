"""Correlaciones conductuales deterministas sobre los registros importados.

Motor ÚNICO detrás de dos consumidores: el campo «correlación» del informe
técnico (la IA lo redacta a partir de estos datos) y el panel de
correlaciones ATT&CK (lo pinta estructurado). Cruza IP ↔ Event_ID ↔
Hostname ↔ User ↔ Result para responder preguntas que el JOIN
Event_ID → MITRE ATT&CK no contesta:

    - ¿una IP aparece en dos o más tipos de evento?      → multi_event_ips
    - ¿qué combinación IP + host + evento se REPITE?     → repeated_actions
    - ¿una IP habla con muchos hosts?                    → multi_host_ips
    - ¿un usuario se usa en varios hosts?                → multi_host_users
    - ¿una IP junta fallos y éxitos de autenticación?    → brute_force

100% determinista: sin modelo, sin similitud y sin red — mismos datos,
mismos resultados. Cada entrada lleva sus cifras EXACTAS para que el
informe las cite (el validador las ancla contra el ctx) y el panel las
muestre sin recalcular nada.

Los umbrales están pensados para que un patrón sea accionable y no ruido:
una IP que solo aparece en un tipo de evento, o una combinación vista dos
veces, no es un hallazgo. Los valores son explícitos y compartidos con el
prompt del informe para que el modelo no tenga que adivinarlos.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

# --- Umbrales (documentados en el prompt del informe y en el panel) -------
MIN_EVENT_TYPES = 2     # IPs vistas en más de un tipo de evento
MIN_REPEAT = 5          # misma IP + host + evento repetido
MIN_HOSTS_PER_IP = 4    # IPs con alcance amplio (barrido / movimiento lateral)
MIN_HOSTS_PER_USER = 2  # usuarios usados en varios hosts
MIN_FAILURES = 3        # fallos mínimos de una IP para sospechar fuerza bruta
MIN_SUCCESSES = 1       # …con al menos un éxito posterior (¿contraseña adivinada?)
TOP_N = 8               # tope de entradas por lista (ctx del informe y panel)

# Valores que pandas deja en las columnas de texto y que no son un dato.
_EMPTY = ("", "nan", "None", "<NA>", "NaT", "—", "-")

_LISTS = (
    "multi_event_ips",
    "repeated_actions",
    "multi_host_ips",
    "multi_host_users",
    "brute_force",
)


def _clean(series: pd.Series) -> pd.Series:
    """Texto saneado: sin espacios sobrantes y sin los vacíos de pandas."""
    s = series.astype(str).str.strip()
    return s.where(~s.isin(_EMPTY))


def _column(df: pd.DataFrame, name: str) -> Optional[pd.Series]:
    """Columna saneada, o None si el archivo no la trae (no se inventa)."""
    if name not in df.columns:
        return None
    return _clean(df[name])


def _empty_patterns() -> Dict[str, Any]:
    return {name: [] for name in _LISTS} | {
        "totals": {name: 0 for name in _LISTS}
    }


def compute_behavior_patterns(df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Cruces conductuales del dataframe, con cifras exactas y umbral fijo.

    Devuelve SIEMPRE las 5 listas (vacías si la columna falta o no hay
    datos) más `totals` con el número de entradas que CUMPLEN el umbral —
    antes del corte a `TOP_N`, para que «7 IPs en ≥4 hosts» no diga 8
    cuando el listado viene truncado. Ninguna excepción se propaga: un
    dataframe raro degrada a patrones vacíos, igual que el resto del motor.
    """
    out = _empty_patterns()
    if df is None or not len(df):
        return out

    ip = _column(df, "Source_IP")
    event = _column(df, "Event_ID")
    host = _column(df, "Hostname")
    user = _column(df, "User")
    result = _column(df, "Result")

    # --- IPs presentes en más de un tipo de evento ------------------------
    # Una IP que solo dispara un tipo de evento es rutina; cruzar tipos
    # separa al actor que toca autenticación, procesos y recursos compartidos.
    if ip is not None and event is not None:
        base = pd.DataFrame({"ip": ip, "event": event})
        base["host"] = host if host is not None else ""
        base = base[(base["ip"] != "") & (base["event"] != "")]
        if len(base):
            grp = base.groupby("ip").agg(
                events=("ip", "size"),
                event_types=("event", "nunique"),
                hosts=("host", lambda s: s[s != ""].nunique()),
            )
            grp = grp[grp["event_types"] >= MIN_EVENT_TYPES]
            grp = grp.sort_values(
                ["event_types", "events", "ip"], ascending=[False, False, True]
            )
            out["totals"]["multi_event_ips"] = int(len(grp))
            for idx, row in grp.head(TOP_N).iterrows():
                entry = {
                    "ip": str(idx),
                    "event_types": int(row.event_types),
                    "events": int(row.events),
                }
                # Sin columna Hostname el cruce de hosts no existe: se omite
                # la clave en lugar de publicar «0 hosts» como si fuera dato.
                if host is not None:
                    entry["hosts"] = int(row.hosts)
                out["multi_event_ips"].append(entry)

    # --- Acciones repetidas: misma IP + host + mismo evento ---------------
    # El patrón conductual clásico: «203.0.113.74 contra DC-01, 4625, 33
    # veces» es un intento de fuerza bruta, no 33 eventos sueltos.
    if ip is not None and event is not None and host is not None:
        base = pd.DataFrame({"ip": ip, "host": host, "event": event})
        base = base[
            (base["ip"] != "") & (base["host"] != "") & (base["event"] != "")
        ]
        if len(base):
            counts = (
                base.value_counts()
                .rename("count")
                .reset_index()
                .sort_values(
                    ["count", "ip", "host", "event"],
                    ascending=[False, True, True, True],
                )
            )
            repeated = counts[counts["count"] >= MIN_REPEAT]
            out["totals"]["repeated_actions"] = int(len(repeated))
            out["repeated_actions"] = [
                {
                    "ip": str(row.ip),
                    "host": str(row.host),
                    "event_id": str(row.event),
                    "count": int(row["count"]),
                }
                for _, row in repeated.head(TOP_N).iterrows()
            ]

    # --- IPs presentes en muchos hosts distintos --------------------------
    # Alcance amplio desde un mismo origen: barrido o movimiento lateral.
    if ip is not None and host is not None:
        base = pd.DataFrame({"ip": ip, "host": host})
        base = base[(base["ip"] != "") & (base["host"] != "")]
        if len(base):
            grp = base.groupby("ip").agg(
                hosts=("host", "nunique"), events=("ip", "size")
            )
            grp = grp[grp["hosts"] >= MIN_HOSTS_PER_IP]
            grp = grp.sort_values(
                ["hosts", "events", "ip"], ascending=[False, False, True]
            )
            out["totals"]["multi_host_ips"] = int(len(grp))
            out["multi_host_ips"] = [
                {
                    "ip": str(idx),
                    "hosts": int(row.hosts),
                    "events": int(row.events),
                }
                for idx, row in grp.head(TOP_N).iterrows()
            ]

    # --- Usuarios autenticados en varios hosts ----------------------------
    # La misma cuenta en varios activos es movimiento lateral (o una cuenta
    # de servicio mal acotada): el cruce User × Hostname lo deja a la vista.
    if user is not None and host is not None:
        base = pd.DataFrame({"user": user, "host": host})
        base = base[(base["user"] != "") & (base["host"] != "")]
        if len(base):
            grp = base.groupby("user").agg(
                hosts=("host", "nunique"), events=("user", "size")
            )
            grp = grp[grp["hosts"] >= MIN_HOSTS_PER_USER]
            grp = grp.sort_values(
                ["hosts", "events", "user"], ascending=[False, False, True]
            )
            out["totals"]["multi_host_users"] = int(len(grp))
            out["multi_host_users"] = [
                {
                    "user": str(idx),
                    "hosts": int(row.hosts),
                    "events": int(row.events),
                }
                for idx, row in grp.head(TOP_N).iterrows()
            ]

    # --- Candidatos a fuerza bruta: fallos + al menos un éxito ------------
    # Fallos sueltos pasan (red, teclado, cuenta bloqueada); fallos JUNTO a
    # un éxito de la misma IP es el patrón de ataque por diccionario.
    if ip is not None and result is not None:
        low = result.str.lower()
        base = pd.DataFrame(
            {
                "ip": ip,
                "fail": low.str.contains("fail", na=False)
                | low.str.contains("denied", na=False),
                "succ": low.str.contains("success", na=False),
                "host": host if host is not None else "",
            }
        )
        base = base[base["ip"] != ""]
        if len(base):
            grp = base.groupby("ip").agg(
                failures=("fail", "sum"),
                successes=("succ", "sum"),
                events=("ip", "size"),
                hosts=("host", lambda s: s[s != ""].nunique()),
            )
            grp = grp[
                (grp["failures"] >= MIN_FAILURES) & (grp["successes"] >= MIN_SUCCESSES)
            ]
            grp = grp.sort_values(
                ["failures", "events", "ip"], ascending=[False, False, True]
            )
            out["totals"]["brute_force"] = int(len(grp))
            for idx, row in grp.head(TOP_N).iterrows():
                entry = {
                    "ip": str(idx),
                    "failures": int(row.failures),
                    "successes": int(row.successes),
                    "events": int(row.events),
                }
                if host is not None:      # igual que en multi_event_ips
                    entry["hosts"] = int(row.hosts)
                out["brute_force"].append(entry)

    return out
