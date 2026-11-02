"""Tarea: correlaciones conductuales (behavior_correlations.py).

Motor determinista que comparte el informe (campo «correlation») y el panel
ATT&CK: los tests fijan los UMBRALES y las cifras exactas de cada patrón
sobre un dataframe sintético, porque si el umbral se mueve sin querer el
informe empieza a citar patrones que los datos no sostienen.

El dataset sintético está construido a mano para que CADA patrón tenga un
contraejemplo: una IP que no debe aparecer, un usuario de un solo host, una
IP con fallos pero sin éxito. Un motor que lo acepta todo no está correlando
nada.
"""

import pandas as pd

from behavior_correlations import (
    MIN_EVENT_TYPES,
    MIN_FAILURES,
    MIN_HOSTS_PER_IP,
    MIN_HOSTS_PER_USER,
    MIN_REPEAT,
    TOP_N,
    compute_behavior_patterns,
)

COLUMNAS = [
    "Log_ID", "Timestamp", "Hostname", "Event_ID", "User", "Source_IP",
    "Destination", "Log_Source", "Event_Description", "Process_CommandLine",
    "Result", "Severity",
]


def _fila(log_id, host, event, user, ip, result, severity="Low"):
    return [log_id, "2026-01-15 10:00:00", host, event, user, ip,
            "-", "Security", "evento", "-", result, severity]


def _dataset() -> pd.DataFrame:
    """16 filas con los 5 patrones y sus contraejemplos.

    A · 203.0.113.74 → 6 fallos + 1 éxito en DC-01 (fuerza bruta, repetido)
    B · 10.10.1.31 → 4 tipos de evento en 4 hosts; PROXY×5 en WS-018
    C · 192.0.2.91 → 2 hosts pero UN solo tipo de evento (no es patrón)
    D · 198.51.100.77 → 3 fallos SIN éxito (fuerza bruta incompleta)
    """
    filas = []
    n = 0

    def add(host, event, user, ip, result, veces=1):
        nonlocal n
        for _ in range(veces):
            n += 1
            filas.append(_fila(f"LOG-{n:03d}", host, event, user, ip, result))

    # A · fuerza bruta + combinación repetida 6 veces
    add("DC-01", "4625", "CORP\\admin", "203.0.113.74", "Failure", veces=6)
    add("DC-01", "4624", "CORP\\admin", "203.0.113.74", "Success")

    # B · multi-host, multi-evento y repetición de proxy
    add("WS-018", "PROXY_ACCESS", "CORP\\helpdesk", "10.10.1.31", "Success", veces=5)
    add("WS-019", "4624", "CORP\\helpdesk", "10.10.1.31", "Success")
    add("DC-01", "4688", "CORP\\helpdesk", "10.10.1.31", "Success")
    add("FS-SRV01", "4663", "CORP\\helpdesk", "10.10.1.31", "Success")

    # C · mismo usuario en 2 hosts, pero la IP solo ve UN tipo de evento
    add("WS-019", "HTTP_ACCESS", "CORP\\jlopez", "192.0.2.91", "200")
    add("WEB-01", "HTTP_ACCESS", "CORP\\jlopez", "192.0.2.91", "200")

    # D · fallos sin ningún éxito: no es fuerza bruta todavía
    add("WEB-01", "4625", "CORP\\guest", "198.51.100.77", "Failure", veces=3)

    return pd.DataFrame(filas, columns=COLUMNAS)


def test_umbral_de_repeticion_es_el_acordado():
    """Los umbrales son contrato (prompt, panel y tests los citan)."""
    assert (MIN_REPEAT, MIN_EVENT_TYPES) == (5, 2)
    assert (MIN_HOSTS_PER_IP, MIN_HOSTS_PER_USER) == (4, 2)
    assert (MIN_FAILURES, TOP_N) == (3, 8)


def test_multi_event_ips_contraejemplo_de_un_solo_tipo():
    """Todas las IPs del dataset están en >1 Event ID salvo las de un solo tipo."""
    patrones = compute_behavior_patterns(_dataset())
    por_ip = {e["ip"]: e for e in patrones["multi_event_ips"]}

    assert set(por_ip) == {"203.0.113.74", "10.10.1.31"}
    # La IP con más tipos va primero (orden determinista: tipos, luego registros)
    assert patrones["multi_event_ips"][0]["ip"] == "10.10.1.31"
    assert por_ip["10.10.1.31"] == {
        "ip": "10.10.1.31", "event_types": 4, "events": 8, "hosts": 4,
    }
    assert por_ip["203.0.113.74"] == {
        "ip": "203.0.113.74", "event_types": 2, "events": 7, "hosts": 1,
    }
    # Contraejemplo: 192.0.2.91 solo ve HTTP_ACCESS → NO es multi-evento
    assert "192.0.2.91" not in por_ip
    assert patrones["totals"]["multi_event_ips"] == 2


def test_repeated_actions_solo_las_combinaciones_repetidas():
    patrones = compute_behavior_patterns(_dataset())
    combos = patrones["repeated_actions"]

    assert [(e["ip"], e["host"], e["event_id"], e["count"]) for e in combos] == [
        ("203.0.113.74", "DC-01", "4625", 6),
        ("10.10.1.31", "WS-018", "PROXY_ACCESS", 5),
    ]
    # Contraejemplo: 198.51.100.77 repite 4625 solo 3 veces (< MIN_REPEAT)
    assert patrones["totals"]["repeated_actions"] == 2


def test_multi_host_ips_exige_alcanzar_varios_hosts():
    patrones = compute_behavior_patterns(_dataset())
    ips = patrones["multi_host_ips"]

    assert [(e["ip"], e["hosts"]) for e in ips] == [("10.10.1.31", 4)]
    # Contraejemplos: 192.0.2.91 toca 2 hosts y 203.0.113.74 solo DC-01
    assert patrones["totals"]["multi_host_ips"] == 1


def test_multi_host_users_cruza_usuario_y_host():
    patrones = compute_behavior_patterns(_dataset())
    users = {e["user"]: e for e in patrones["multi_host_users"]}

    assert set(users) == {"CORP\\helpdesk", "CORP\\jlopez"}
    assert users["CORP\\helpdesk"]["hosts"] == 4
    assert users["CORP\\jlopez"]["hosts"] == 2
    # Contraejemplo: admin y guest solo se ven en un host
    assert "CORP\\admin" not in users and "CORP\\guest" not in users


def test_brute_force_exige_fallos_y_al_menos_un_exito():
    patrones = compute_behavior_patterns(_dataset())
    fuerza = patrones["brute_force"]

    assert len(fuerza) == 1
    assert fuerza[0] == {
        "ip": "203.0.113.74", "failures": 6, "successes": 1,
        "hosts": 1, "events": 7,
    }
    # Contraejemplo: 198.51.100.77 acumula 3 fallos pero NINGÚN éxito
    assert patrones["totals"]["brute_force"] == 1


def test_totales_cuentan_antes_del_corte_a_top_n():
    """`totals` refleja TODAS las entradas que cumplen umbral, no el top."""
    filas = []
    n = 0
    for i in range(TOP_N + 2):            # 10 IPs, cada una en 4 hosts
        ip = f"10.20.0.{i + 1}"
        for host in ("WS-001", "WS-002", "WS-003", "WS-004"):
            n += 1
            filas.append(_fila(f"LOG-{n:03d}", host, "4624", "CORP\\x", ip, "Success"))
    df = pd.DataFrame(filas, columns=COLUMNAS)

    patrones = compute_behavior_patterns(df)
    assert patrones["totals"]["multi_host_ips"] == TOP_N + 2   # 10 reales
    assert len(patrones["multi_host_ips"]) == TOP_N            # 8 en el listado


def test_dataframe_vacio_o_nulo_devuelve_patrones_vacios():
    for df in (None, pd.DataFrame(columns=COLUMNAS), pd.DataFrame()):
        patrones = compute_behavior_patterns(df)
        assert set(patrones) == {
            "multi_event_ips", "repeated_actions", "multi_host_ips",
            "multi_host_users", "brute_force", "totals",
        }
        assert all(patrones[k] == [] for k in patrones if k != "totals")
        assert all(v == 0 for v in patrones["totals"].values())


def test_columnas_ausentes_no_inventan_patrones():
    """Un archivo sin Source_IP/Result no fabrica cruces: listas vacías."""
    df = _dataset()[["Log_ID", "Hostname", "Event_ID", "User", "Severity"]]
    patrones = compute_behavior_patterns(df)

    assert patrones["multi_event_ips"] == []        # sin Source_IP
    assert patrones["multi_host_ips"] == []
    assert patrones["brute_force"] == []            # sin Source_IP ni Result
    # Sin Source_IP el cruce de usuario con host sigue siendo posible
    assert patrones["totals"]["multi_host_users"] == 2


def test_vacios_de_pandas_no_cuentan_como_dato():
    """«nan»/«» en IP o host no abren patrones (el saneado los descarta)."""
    df = _dataset()
    df.loc[df.index[:6], "Source_IP"] = float("nan")   # los 6 fallos de DC-01
    df.loc[df.index[6], "Hostname"] = ""               # y su éxito, sin host
    patrones = compute_behavior_patterns(df)

    assert "203.0.113.74" not in {e["ip"] for e in patrones["multi_event_ips"]}
    assert patrones["totals"]["brute_force"] == 0
    assert patrones["totals"]["repeated_actions"] == 1   # solo el proxy de WS-018
