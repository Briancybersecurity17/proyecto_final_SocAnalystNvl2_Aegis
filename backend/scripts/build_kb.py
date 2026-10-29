"""
build_kb.py
-----------
Construye `backend/knowledge/mitre_attack.json`, la base de conocimiento de
técnicas MITRE ATT&CK usada por el asistente RAG.

FUENTE DE DATOS: bundle STIX 2.1 oficial del MITRE CTI
    https://github.com/mitre/cti  (enterprise-attack.json)
Se descarga una sola vez y se cachea en `backend/data/kb_cache/` (ignorado por
git). De todo el ATT&CK se quedan SOLO las técnicas curadas en `CURATED`,
relevantes para telemetría de Windows/enterprise (los logs que analiza el SOC).

CAMPOS EN ESPAÑOL (`descripcion_es`, `se_detecta`, `eventos`) los aporta este
proyecto como guía de detección; `description` se mantiene EN oficial de MITRE
para poder verificarla contra attack.mitre.org. `eventos` son los Event IDs de
Windows que suelen reflejar la técnica — referencia, NO una afirmación sobre
los datos importados.

Uso:
    python backend/scripts/build_kb.py            # descarga (si hace falta) y genera
    python backend/scripts/build_kb.py --force    # fuerza re-descarga del bundle
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

# La consola de Windows (cp1252) no imprime flechas/acentos: forzamos UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]           # backend/
OUT_PATH = BASE / "knowledge" / "mitre_attack.json"
CACHE_PATH = BASE / "data" / "kb_cache" / "enterprise-attack.json"
STIX_URL = "https://raw.githubusercontent.com/mitre/cti/master/enterprise-attack/enterprise-attack.json"

TACTICS_ES = {
    "initial-access": "Acceso inicial",
    "execution": "Ejecución",
    "persistence": "Persistencia",
    "privilege-escalation": "Elevación de privilegios",
    "defense-evasion": "Evasión de defensas",   # nombre histórico
    "defense-impairment": "Neutralización de defensas",
    "stealth": "Sigilo",
    "credential-access": "Acceso a credenciales",
    "discovery": "Descubrimiento",
    "lateral-movement": "Movimiento lateral",
    "collection": "Recopilación",
    "command-and-control": "Control y mando",
    "exfiltration": "Exfiltración",
    "impact": "Impacto",
    "reconnaissance": "Reconocimiento",
    "resource-development": "Desarrollo de recursos",
}

# ---------------------------------------------------------------------------
# Curaduría: técnicas relevantes para los logs del ejercicio
#   id -> (descripcion_es, se_detecta, [event_ids])
# ---------------------------------------------------------------------------
CURATED: dict[str, tuple[str, str, list[str]]] = {
    # ---------------- Acceso inicial ----------------
    "T1190": (
        "Explota una aplicación expuesta a Internet para entrar a la red.",
        "Patrones anómalos en logs de proxy/IIS: respuestas 4xx/5xx repetidas, "
        "payloads con caracteres escapados o rutas de traversal desde una IP.",
        ["HTTP_ACCESS", "PROXY_ACCESS"],
    ),
    "T1566": (
        "Envía adjuntos o enlaces maliciosos para que el usuario ejecute código.",
        "Dominios de correo/descarga recién registrados o desconocidos en el "
        "proxy, clics a acortadores desde estaciones de usuario.",
        ["PROXY_ACCESS", "HTTP_ACCESS"],
    ),
    "T1133": (
        "Abre un servicio de acceso remoto legítimo (VPN, RDP, correo) con "
        "credenciales válidas.",
        "Inicios de sesión desde geolocalizaciones o horarios inusuales, "
        "LogonType 10 (RDP) o 3 (red) desde orígenes no habitualmente.",
        ["4624", "4768"],
    ),
    "T1078": (
        "Usa cuentas legítimas (creadas o comprometidas) para mantener el acceso.",
        "Autenticaciones exitosas con cuentas nuevas, sin MFA o fuera de horario; "
        "correlacionar 4624 con 4720 (creación de cuenta).",
        ["4624", "4720", "4768"],
    ),
    "T1078.002": (
        "Cuentas de dominio válidas usadas por el atacante.",
        "Logons de cuentas de dominio desde hosts que normalmente no las usan.",
        ["4624", "4768", "4769"],
    ),
    "T1078.003": (
        "Cuentas locales válidas usadas para evadir controles de dominio.",
        "4624/4672 con cuentas locales en estaciones o servidores donde no deberían.",
        ["4624", "4672"],
    ),
    # ---------------- Ejecución ----------------
    "T1059": (
        "Ejecuta intérpretes de comandos o scripts para controlar la máquina.",
        "Procesos hijos inusuales de cmd/powershell/wscript y líneas de comienzo "
        "de sesión anómalas en 4688.",
        ["4688", "4104"],
    ),
    "T1059.001": (
        "PowerShell para ejecutar código, a menudo ofuscado o descargado.",
        "Bloques 4104 con `enc`/codificación, `DownloadString` o IEX; procesos "
        "powershell.exe -enc desde estaciones sin administración remota.",
        ["4104", "4103", "4688"],
    ),
    "T1059.003": (
        "Intérprete de comandos de Windows (cmd.exe).",
        "cmd.exe lanzado por procesos no habituales (Office, instaladores, servicios).",
        ["4688"],
    ),
    "T1047": (
        "Ejecuta comandos vía WMI, también para movimiento lateral remoto.",
        "WmiPrvSE.exe como padre de procesos; 4624 LogonType 3 hacia otros hosts "
        "acompañado de 4688 remoto.",
        ["4688", "4624"],
    ),
    "T1053": (
        "Crea o modifica tareas programadas para ejecución recurrente.",
        "4698 (tarea creada) y 4702 (tarea actualizada) con rutas o acciones sospechosas.",
        ["4698", "106"],
    ),
    "T1053.005": (
        "Tareas programadas de Windows como mecanismo de persistencia.",
        "Tareas nuevas apuntando a binarios en Temp/AppData o a scripts descargados.",
        ["4698", "4699", "106"],
    ),
    "T1569": (
        "Ejecuta servicios del sistema para correr con privilegios.",
        "Servicios que arrancan binarios en rutas de usuario o temporales.",
        ["7045", "7036"],
    ),
    "T1569.001": (
        "Creación de servicio para ejecutar con SYSTEM.",
        "7045 (servicio nuevo) con imagen fuera de rutas estándar de Windows.",
        ["7045", "7036", "4688"],
    ),
    "T1105": (
        "Descarga herramientas o payloads desde Internet hacia la víctima.",
        "Transferencias grandes por proxy/firewall desde dominios nuevos; "
        "4688 con certutil/mshta bitsadmin.",
        ["4688", "PROXY_ACCESS", "HTTP_ACCESS"],
    ),
    "T1218": (
        "Usa binarios firmados de Windows para ejecutar código y evadir control.",
        "rundll32, regsvr32, msiexec, mshta o verisign.dll invocados con argumentos "
        "sospechosos (http, /sc:, dll descargada).",
        ["4688"],
    ),
    "T1218.011": (
        "rundll32.exe para ejecutar código en DLL.",
        "rundll32 con rutas a Temp/AppData o con parámetros de exportación raros.",
        ["4688"],
    ),
    # ---------------- Persistencia ----------------
    "T1543": (
        "Crea o modifica procesos de sistema (servicios) para persistir.",
        "7045 + 7036 en hosts donde no hay despliegues; imagen del servicio "
        "en rutas de usuario.",
        ["7045", "7036", "7040"],
    ),
    "T1543.003": (
        "Servicio de Windows creado o modificado por el atacante.",
        "Servicio nuevo con start type automático y binario no firmado.",
        ["7045", "7036"],
    ),
    "T1547": (
        "Ejecuta al arrancar o al iniciar sesión para sobrevivir reinicios.",
        "Modificaciones de claves de ejecución automática (Run, Winlogon) — 4657 "
        "sobre HKCU/HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.",
        ["4657", "4688"],
    ),
    "T1547.001": (
        "Claves de registro Run o carpeta Startup como arranque automático.",
        "4657 sobre valores Run apuntando a AppData, Temp o scripts.",
        ["4657", "4688"],
    ),
    "T1136": (
        "Crea cuentas nuevas para tener acceso persistente.",
        "4720 seguido de 4728/4732 (agregado a grupos) y de 4722 (habilitación).",
        ["4720", "4728", "4732"],
    ),
    "T1136.001": (
        "Cuenta local creada fuera del ciclo normal de altas.",
        "4720 en hosts sin gestión de identidad, con nombre genérico o administrador.",
        ["4720", "4732"],
    ),
    "T1136.002": (
        "Cuenta de dominio creada para persistencia o movimiento lateral.",
        "4720/4728 en el controlador de dominio con usuarios que no existen en RRHH/IT.",
        ["4720", "4728", "4732"],
    ),
    "T1098": (
        "Modifica cuentas existentes (grupos, contraseñas, atributos) para escalar.",
        "4738/4742 con cambios de atributos, 4728/4732 con agregados a grupos "
        "administradores.",
        ["4738", "4728", "4732"],
    ),
    # ---------------- Elevación de privilegios ----------------
    "T1548": (
        "Abusa de mecanismos de control de elevación para obtener más privilegios.",
        "Procesos elevados inesperados; 4672 (permisos especiales) asociado a "
        "sesiones no administrativas.",
        ["4672", "4688"],
    ),
    "T1548.002": (
        "Bypass de UAC para obtener token de administrador sin consentimiento.",
        "fodhelper.exe / sdclt / eventvwr.exe lanzados por procesos de usuario "
        "comunes; 4672 justo después.",
        ["4672", "4688"],
    ),
    "T1055": (
        "Inyecta código en procesos legítimos para ocultarse y persistir.",
        "Procesos con módulos no firmados; comportamientos que en Windows no "
        "generan log propio — se sospecha por padres/hijos anómalos en 4688.",
        ["4688"],
    ),
    "T1134": (
        "Manipula tokens de acceso para impersonar a otros usuarios o SYSTEM.",
        "4672 (privilegios especiales) en sesiones que no deberían tenerlos.",
        ["4672", "4624"],
    ),
    "T1134.001": (
        "Robo o duplicación de tokens para impersonación.",
        "Sesiones con privilegios anómalos (4672) tras procesos no elevados.",
        ["4672"],
    ),
    # ---------------- Acceso a credenciales ----------------
    "T1110": (
        "Fuerza bruta: prueba masiva de contraseñas contra cuentas.",
        "Ráfagas de 4625 sobre la misma cuenta o desde la misma IP; escalada a "
        "4740 (lockout) y picos de 4624 tardíos tras muchos fallos.",
        ["4625", "4740", "4771"],
    ),
    "T1110.001": (
        "Fuerza bruta por diccionario contra una sola cuenta.",
        "Muchos 4625 consecutivos con el mismo User y distinta Source_IP.",
        ["4625", "4740"],
    ),
    "T1110.003": (
        "Password spraying: una contraseña contra muchas cuentas.",
        "4625 con el mismo patrón temporal desde una IP sobre cientos de usuarios "
        "distintos (baja tasa por cuenta, alta tasa por origen).",
        ["4625"],
    ),
    "T1003": (
        "Volca credenciales del sistema operativo (LSASS, SAM, NTDS).",
        "Accesos de lectura a lsass.exe (4663/4656) y volcados de memoria; "
        "herramientas conocidas (mimikatz) invocadas desde 4688.",
        ["4663", "4688"],
    ),
    "T1003.001": (
        "Volcado del proceso LSASS para extraer credenciales en claro.",
        "4663 sobre lsass.exe con AccessMask de lectura desde procesos no legítimos.",
        ["4663", "4656"],
    ),
    "T1558": (
        "Roba o forja tickets Kerberos (Golden/Silver Ticket).",
        "4769 con encriptación inusual (0x17 RC4) o tickets con vida larga; "
        "servicios solicitados que no existen.",
        ["4768", "4769"],
    ),
    "T1558.003": (
        "Kerberoasting: solicita tickets de servicio para crackear el hash.",
        "4769 de servicios con encriptación RC4 (0x17) desde una cuenta sin "
        "relación con el servicio.",
        ["4769", "4768"],
    ),
    "T1550": (
        "Usa material de autenticación alternativo (hash, ticket) sin contraseña.",
        "4624 con LogonType 9/3 y sesiones que no siguen el flujo 4768→4769.",
        ["4624", "4648"],
    ),
    "T1550.002": (
        "Pass the Hash: autenticación con el hash NTLM en lugar de la contraseña.",
        "4624 LogonType 3/9 con NTLM en hosts donde no hay administración remota.",
        ["4624", "4648"],
    ),
    "T1550.003": (
        "Pass the Ticket: reutiliza tickets Kerberos robados.",
        "4769 sin 4768 previo en la misma sesión o desde otro host.",
        ["4769", "4768"],
    ),
    # ---------------- Evasión de defensas ----------------
    "T1070": (
        "Borra o altera rastros para dificultar la investigación.",
        "1102 (registro de seguridad borrado), 4699 (tarea eliminada) y "
        "discontinuidades en la secuencia de eventos.",
        ["1102", "4699"],
    ),
    "T1070.001": (
        "Borra el registro de seguridad de Windows.",
        "1102 en cualquier host: es una alerta de por sí, revisar quién lo generó.",
        ["1102"],
    ),
    "T1112": (
        "Modifica el registro para cambiar configuraciones de seguridad.",
        "4657 sobre claves de auditoría, firewall o ejecución automática.",
        ["4657", "4688"],
    ),
    "T1562": (
        "Debilita herramientas y controles de seguridad del entorno.",
        "Servicio de Defender detenido, reglas de firewall cambiadas (4946/4950) "
        "o eventos 5156 que permiten tráfico antes bloqueado.",
        ["5156", "1116"],
    ),
    "T1562.001": (
        "Deshabilita o modifica antivirus/EDR.",
        "1116/1117 de Defender con desactivación, servicio MsMpEng detenido (7036).",
        ["1116", "7036"],
    ),
    "T1562.004": (
        "Deshabilita o modifica el firewall de Windows.",
        "4946 (regla de firewall añadida/cambiada), 5156 con Allow-any, "
        "perfiles de firewall cambiados (4950).",
        ["5156", "4946"],
    ),
    "T1036": (
        "Se hace pasar por software o rutas legítimas para no ser detectado.",
        "4688 con nombres de proceso parecidos a sistemas (svchost, explorer) "
        "en rutas que no son System32.",
        ["4688"],
    ),
    "T1036.005": (
        "Coincide con nombre o ubicación legítima (falso svchost, falsa ruta).",
        "4688 con Image fuera de %SystemRoot%\\System32 pero nombre idéntico.",
        ["4688"],
    ),
    "T1027": (
        "Ofusca scripts o binarios para evadir detección por firma.",
        "4104 con bloques ofuscados o decodificaciones largas; 4688 con "
        "powershell -enc / base64.",
        ["4104", "4688"],
    ),
    "T1140": (
        "Decodifica o deobfusca contenido para ejecutarlo en memoria.",
        "cmd/powershell que decodifica base64 justo antes de ejecutar (4688+4104).",
        ["4688", "4104"],
    ),
    # ---------------- Descubrimiento ----------------
    "T1082": (
        "Recoge información del sistema para planificar el siguiente paso.",
        "Procesos systeminfo, hostname, systeminfo o scripting de inventario "
        "fuera de ventanas de mantenimiento.",
        ["4688"],
    ),
    "T1083": (
        "Explora directorios y archivos buscando datos sensibles.",
        "5145/5140 sobre rutas de Shares sensibles (SYSVOL, backups) y accesos "
        "4663 masivos a carpetas nuevas.",
        ["4663", "5140", "5145"],
    ),
    "T1087": (
        "Enumera cuentas de usuario para elegir objetivos.",
        "Enumeración de grupos (4799) o consultas de directorio desde estaciones "
        "comunes.",
        ["4799", "4728"],
    ),
    "T1087.002": (
        "Enumera cuentas de dominio (LDAP, grupos de seguridad).",
        "4799/4728 frecuentes desde un host que no es controlador de dominio.",
        ["4799", "4728"],
    ),
    "T1069": (
        "Enumera permisos y grupos para saber a quién puede apoderarse.",
        "4799 (grupos con seguridad) y 4732 fuera de operaciones de administración.",
        ["4799", "4732"],
    ),
    "T1046": (
        "Escanea la red buscando servicios abiertos (reconocimiento interno).",
        "Origen con muchos destinos/puertos distintos en 5156 o en el firewall; "
        "patrón de barrido.",
        ["5156", "FIREWALL"],
    ),
    "T1033": (
        "Determina qué usuario y privilegios tiene la sesión actual.",
        "whoami / query user ejecutados (4688) al inicio de una sesión.",
        ["4688"],
    ),
    "T1007": (
        "Enumera servicios del sistema instalados.",
        "sc query / net start fuera de ventanas de mantenimiento (4688) y "
        "consultas frecuentes de 7036.",
        ["4688", "7036"],
    ),
    "T1057": (
        "Enumera procesos en ejecución para localizar seguridad o datos.",
        "tasklist / ps / Get-Process lanzados en hosts sin operación de soporte.",
        ["4688"],
    ),
    "T1016": (
        "Recolecta configuración de red (interfaces, rutas, DNS).",
        "ipconfig / route print / netsh desde sesiones interactivas inusuales.",
        ["4688"],
    ),
    "T1012": (
        "Consulta el registro para leer configuraciones o claves guardadas.",
        "reg query por procesos no habituales (4688).",
        ["4688"],
    ),
    "T1049": (
        "Revisa conexiones de red activas del host.",
        "netstat / Get-NetTCPConnection ejecutados fuera de mantenimiento.",
        ["4688", "5156"],
    ),
    # ---------------- Movimiento lateral ----------------
    "T1021": (
        "Usa servicios remotos (RDP, SMB, WinRM) para moverse entre hosts.",
        "4624 LogonType 3/10 hacia otros servidores y 5140/5145 sobre Shares "
        "administrativos (C$, ADMIN$).",
        ["4624", "5140", "5145"],
    ),
    "T1021.001": (
        "RDP para acceder remotamente a otra máquina.",
        "4624 LogonType 10 y 1149 (sesión RDP autenticada) desde hosts no "
        "incluidos en la matriz de administración.",
        ["4624", "1149"],
    ),
    "T1021.002": (
        "Shares administrativos SMB (C$, ADMIN$, IPC$) para copiar ejecutables.",
        "5140/5145 en \\ADMIN$ o \\C$ seguido de 4688 en el host destino.",
        ["5140", "5145", "4624"],
    ),
    "T1074": (
        "Deja datos recopilados en un directorio de trabajo antes de exfiltrar.",
        "Creación de carpetas en Temp/AppData con accesos 4663 repetidos.",
        ["4663", "5145"],
    ),
    # ---------------- Control y mando ----------------
    "T1071": (
        "Se comunica con el comando y mando usando protocolos habituales.",
        "Dominios desconocidos en el proxy con periodos regulares (beacon) "
        "y tamaños de respuesta similares.",
        ["PROXY_ACCESS", "HTTP_ACCESS"],
    ),
    "T1071.001": (
        "C2 sobre HTTP/HTTPS enmascarado en tráfico web normal.",
        "User-Agents extraños, peticiones POST periódicas a un solo destino.",
        ["HTTP_ACCESS", "PROXY_ACCESS"],
    ),
    "T1090": (
        "Usa un proxy (o cadena de ellos) para ocultar su origen real.",
        "Destinos de proxy coincidentes con listas de proxies públicos; "
        "saltos encadenados en las cabeceras de acceso.",
        ["PROXY_ACCESS"],
    ),
    "T1573": (
        "Cifra el canal de comunicación para evitar inspección.",
        "TLS con SNI no coincidente con el certificado o tráfico cifrado a "
        "puertos inusuales en 5156.",
        ["PROXY_ACCESS", "5156"],
    ),
    "T1571": (
        "Usa puertos no estándar para evadir filtrados por puerto.",
        "5156 o firewall con sesiones a puertos fuera del inventario autorizado.",
        ["5156", "FIREWALL"],
    ),
    "T1095": (
        "Protocolos de capa de red no aplicativos (ICMP, TCP crudo) como C2.",
        "ICMP con carga útil o conexiones TCP directas sin servicio conocido.",
        ["FIREWALL", "5156"],
    ),
    # ---------------- Exfiltración ----------------
    "T1041": (
        "Envía datos robados por el mismo canal de C2.",
        "Salidas sostenidas de volumen alto hacia un destino ya visto en C2.",
        ["PROXY_ACCESS", "HTTP_ACCESS"],
    ),
    "T1048": (
        "Exfiltra por protocolos alternativos al C2 (DNS, correo, nube).",
        "Consultas DNS atípicas por tamaño o volumen; adjuntos salientes grandes.",
        ["PROXY_ACCESS", "FIREWALL"],
    ),
    "T1567": (
        "Exfiltra a servicios web o nube públicos.",
        "Subidas a almacenamiento en nube desde estaciones que no lo usan.",
        ["PROXY_ACCESS", "HTTP_ACCESS"],
    ),
    "T1567.002": (
        "Exfiltra a almacenamiento en la nube (Drive, Dropbox, S3…).",
        "POST de gran tamaño a dominios de proveedores de nube conocidos.",
        ["PROXY_ACCESS", "HTTP_ACCESS"],
    ),
    # ---------------- Impacto ----------------
    "T1486": (
        "Cifra archivos exigiendo rescate (ransomware).",
        "Explosión de accesos 4663/5145 a muchos archivos en poco tiempo, "
        "cambios de extensión y 4688 con vssadmin/bcdedit.",
        ["4663", "4688", "5145"],
    ),
    "T1490": (
        "Impide la recuperación borrando copias de seguridad y instantáneas.",
        "4688 con `vssadmin delete shadows` / `bcdedit /set recoveryenabled no`.",
        ["4688"],
    ),
    "T1489": (
        "Detiene servicios clave (bases de datos, correo) para forzar impacto.",
        "7036/7040 con paradas de servicios críticos fuera de mantenimiento.",
        ["7036", "7040", "4688"],
    ),
    "T1485": (
        "Destruye datos para causar daño aunque no haya rescate.",
        "Borrados masivos (4663 con DELETE) y formatos fuera de procedimiento.",
        ["4663", "4688"],
    ),
}


def _fetch_bundle(force: bool = False) -> dict:
    """Descarga (o reutiliza en caché) el bundle STIX enterprise-attack."""
    if CACHE_PATH.exists() and not force:
        print(f"[cache] usando {CACHE_PATH}")
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"[descarga] {STIX_URL}")
    req = urllib.request.Request(STIX_URL, headers={"User-Agent": "soc-kb-builder"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    CACHE_PATH.write_bytes(raw)
    print(f"[cache] {len(raw) // 1_000_000} MB guardados")
    return json.loads(raw.decode("utf-8"))


def _clean_description(text: str) -> str:
    """Quita sintaxis markdown de las descripciones STIX y acorta con criterio."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text or "")   # [texto](url)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= 480:
        return text
    cut = text[:480]
    point = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: point + 1] if point > 240 else cut).strip()


def _revoked_map(objects: list[dict]) -> dict[str, str]:
    """Mapa ext_id → ext_id sucesor para técnicas revocadas por MITRE.

    ATT&CK renumera de vez en cuando (p. ej. T1562 → T1685). Seguimos la
    relación `revoked-by` para indexar SIEMRE el ID vigente y conservamos el
    antiguo como alias, así sigue encontrándose «T1562.004» en una búsqueda.
    """
    by_stix_id = {o["id"]: o for o in objects if "id" in o}

    def ext_id(stix_ref: str) -> str | None:
        obj = by_stix_id.get(stix_ref, {})
        for ref in obj.get("external_references", []):
            if ref.get("source_name") == "mitre-attack" and ref.get("external_id"):
                return ref["external_id"]
        return None

    mapping: dict[str, str] = {}
    for obj in objects:
        if obj.get("type") != "relationship" or obj.get("relationship_type") != "revoked-by":
            continue
        source, target = ext_id(obj.get("source_ref", "")), ext_id(obj.get("target_ref", ""))
        if source and target:
            mapping[source] = target
    return mapping


def _resolve(tid: str, revoked: dict[str, str]) -> tuple[str, list[str]]:
    """Sigue la cadena de revocación hasta el ID vigente."""
    aliases: list[str] = []
    seen: set[str] = set()
    current = tid
    while current in revoked and current not in seen:
        seen.add(current)
        aliases.append(current)
        current = revoked[current]
    return current, aliases


def build(force: bool = False) -> dict:
    bundle = _fetch_bundle(force)
    objects = bundle.get("objects", [])

    techniques: dict[str, dict] = {}
    for obj in objects:
        if obj.get("type") != "attack-pattern" or obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue
        tid = next((r.get("external_id") for r in obj.get("external_references", [])
                    if r.get("source_name") == "mitre-attack" and r.get("external_id")), None)
        if not tid:
            continue
        techniques[tid] = obj

    revoked = _revoked_map(objects)
    entries: dict[str, dict] = {}
    missing = []

    def _entry(canonical: str, aliases: list[str], desc_es: str, detecta: str,
               eventos: list[str], curated: bool) -> dict:
        """Entrada de KB para una técnica vigente (campo `kind` = «technique»)."""
        obj = techniques[canonical]
        phases = [p for p in obj.get("kill_chain_phases", [])
                  if p.get("kill_chain_name") == "mitre-attack"]
        tactic_key = phases[0].get("phase_name", "") if phases else ""
        subs = sorted({
            other_id for other_id in techniques
            if other_id.startswith(canonical + ".")
        })
        return {
            "kind": "technique",
            "id": canonical,
            "name": obj.get("name", canonical),
            "tactic": tactic_key.replace("-", " ").title(),
            "tactic_es": TACTICS_ES.get(tactic_key, tactic_key.replace("-", " ").title()),
            "url": f"https://attack.mitre.org/techniques/{canonical.replace('.', '/')}",
            "description": _clean_description(obj.get("description", "")),
            "descripcion_es": desc_es,
            "se_detecta": detecta,
            "eventos": eventos,
            "curated": curated,
            "subtechniques": subs,
            # IDs antiguos revocados por MITRE (p. ej. T1562 → T1685); nunca el
            # propio `canonical`, que ya encabeza la entrada.
            "aliases": [a for a in aliases if a and a != canonical],
        }

    def _merge_aliases(canonical: str, aliases: list[str]) -> None:
        # `alias != canonical`: sin este filtro, técnicas no revocadas acaban
        # listándose a sí mismas como alias propias (ruido en la UI y en el
        # texto indexado, donde ya aparecen como `[TÉCNICA] T1685 …`).
        for alias in aliases:
            if alias and alias != canonical and alias not in entries[canonical]["aliases"]:
                entries[canonical]["aliases"].append(alias)

    for tid, (desc_es, detecta, eventos) in CURATED.items():
        canonical, aliases = _resolve(tid, revoked)
        if canonical in entries:
            # Dos curadurías cayeron sobre la misma técnica vigente → fusionar.
            _merge_aliases(canonical, aliases + [tid])
            continue
        if canonical not in techniques:
            missing.append(tid)
            continue
        entries[canonical] = _entry(canonical, aliases, desc_es, detecta, eventos, True)

    if missing:
        print(f"[aviso] no encontradas en el bundle: {', '.join(missing)}")

    # Cobertura completa de ATT&CK: además de la curaduría en español se vuelcan
    # TODAS las técnicas vigentes del bundle (~620). Las no curadas quedan con
    # `descripcion_es` y `se_detecta` vacíos y `curated: false`, de modo que la
    # UI y el prompt del asistente usan en su lugar `description` (inglés, la
    # fuente oficial): traducir ~540 técnicas a mano no es realista y una
    # traducción automática distorsiona tecnicismos de detección.
    expanded = 0
    for tid in sorted(techniques):
        canonical, aliases = _resolve(tid, revoked)
        if canonical in entries:
            _merge_aliases(canonical, aliases + [tid])
            continue
        if canonical not in techniques:
            continue
        entries[canonical] = _entry(canonical, aliases, "", "", [], False)
        expanded += 1

    ordered = sorted(entries.values(), key=lambda e: e["id"])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    renamed = sum(1 for e in ordered if e["aliases"])
    curated = sum(1 for e in ordered if e.get("curated"))
    print(f"[ok] {len(ordered)} técnicas ({curated} curadas en español, "
          f"{expanded} completadas desde la fuente oficial, "
          f"{renamed} renumeradas por MITRE) -> {OUT_PATH}")
    return {"written": len(ordered), "curated": curated,
            "expanded": expanded, "missing": missing}


if __name__ == "__main__":
    build(force="--force" in sys.argv)
