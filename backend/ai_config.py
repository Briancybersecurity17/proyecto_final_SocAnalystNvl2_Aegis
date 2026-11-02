"""Configuración persistente de la IA y conexión con el proveedor cloud.

Panel «Opciones IA»: el modelo, la URL y los parámetros de generación viven en
un JSON en disco (``backend/data/ai_config.json``) y se cambian en caliente,
sin reiniciar el backend: el proveedor recibe las opciones en cada request, así
que el próximo pedido ya usa los valores nuevos.

Diseño:
    * ``get()`` es la única fuente de verdad en tiempo de ejecución; cada
      llamada relee el archivo (es diminuto), de modo que un guardado desde el
      panel se nota al instante y los tests pueden redirigir la ruta con
      ``monkeypatch``.
    * Las variables de entorno (``AI_API_URL`` / ``AI_API_KEY`` / ``AI_MODEL``
      / ``AI_ENABLED``) aportan los valores por defecto la primera vez, para
      quien prefiera configurar por entorno como antes.
    * Si el LLM está apagado o no responde, ``chat()`` devuelve ``None`` y el
      servicio de IA cae al motor determinista: nunca se queda sin respuesta
      (degradación elegente, igual que siempre).
    * Este módulo es dueño también de la conexión (``chat`` / ``probe`` /
      ``test_connection``) para no duplicar el armado de payloads ni crear
      imports circulares con ``ai_service``.

Un único dialecto: compatible con OpenAI (``/v1/chat/completions``), que es lo
que hablan Groq y el resto de los proveedores cloud. El proyecto ya no soporta
Ollama local, así que no existen ``keep_alive`` / ``num_ctx`` / ``num_predict``:
la ventana de contexto y el tope de generación los aplica el proveedor.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

# Se lee como atributo del módulo en cada llamada a propósito: los tests
# redirigen esta ruta con monkeypatch para no tocar la configuración real.
CONFIG_PATH = Path(__file__).resolve().parent / "data" / "ai_config.json"

# Reintentos de chat() ante un 429 (rate limit del proveedor). Sin esta espera
# un solo tope puntual arrastraba el informe entero al motor local: el LLM
# devolvía None y las cuatro secciones caían al texto determinista. Medido con
# Groq: los 429 llegan en ráfaga (hasta 4 seguidos), así que hacen falta
# varios intentos, no uno solo.
_MAX_RETRIES = 4
# Tapa de la espera pedida por el proveedor (segundos), por si envía un
# Retry-After desmesurado; con tope realista el informe no tarda minutos.
_RETRY_AFTER_MAX = 30.0
_BACKOFF_BASE = 1.5  # backoff exponencial: 1.5s, 3s, 6s entre los 4 intentos

# La clase se ata aquí (y no en el `except`): los tests sustituyen `ai.httpx`
# por un doble mínimo que solo expone `AsyncClient`, y evaluar
# `httpx.HTTPStatusError` en tiempo de captura reventaría con AttributeError.
_HTTP_STATUS_ERROR = httpx.HTTPStatusError


def _retry_delay(attempt: int, response: Any = None) -> float:
    """Espera antes del intento n+1: manda ``Retry-After`` si el proveedor lo trae.

    Un 429 sin cabecera (o con un valor ilegible) cae al backoff exponencial;
    el tope evita que un ``Retry-After`` enorme congele la petición del usuario.
    """
    header = getattr(response, "headers", None) or {}
    try:
        after = float(header.get("retry-after"))
    except (TypeError, ValueError):
        after = 0.0
    if after > 0:
        return min(after, _RETRY_AFTER_MAX)
    return _BACKOFF_BASE * (2 ** (attempt - 1))


DEFAULTS: Dict[str, Any] = {
    # El LLM viene encendido: si no responde, todo vuelve al motor determinista.
    "enabled": os.getenv("AI_ENABLED", "1").strip().lower() not in ("0", "false", "no"),
    # Vacío = «sin configurar»: la app sigue operativa con el motor
    # determinista y el panel pide la URL del proveedor compatible con OpenAI.
    "api_url": os.getenv("AI_API_URL", "").strip(),
    "api_key": os.getenv("AI_API_KEY", "").strip(),
    # openai/gpt-oss-120b en Groq: 131072 tokens de ventana, gratuito y rápido
    # (medido: 2,1 s por turno de chat y 7,6 s de generación larga).
    "model": os.getenv("AI_MODEL", "").strip() or "openai/gpt-oss-120b",
    # En SOC, evidencia y no ficción ⇒ 0–0.2 lo habitual.
    "temperature": 0.15,
    # Medido contra Groq: chat 2,1 s y generación larga 7,6 s ⇒ 60 s es un
    # margen holgado. Si se supera, chat() devuelve None y cae el fallback.
    "timeout": float(os.getenv("AI_TIMEOUT", "60")),
    # Retención de análisis en disco (spec A): días antes de borrarlos
    # automáticamente. 0 = conservar para siempre.
    "retention_days": 30,
}


# ---------------------------------------------------------------------------
# Validación y persistencia
# ---------------------------------------------------------------------------
def _as_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} debe ser un número entero") from None


def _as_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} debe ser numérico") from None


def validate(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza y valida la configuración completa. ValueError con mensaje UI."""
    out: Dict[str, Any] = {}

    out["enabled"] = bool(cfg.get("enabled", DEFAULTS["enabled"]))

    # Vacío permitido: significa «todavía sin proveedor» y la app degrada al
    # motor determinista. Si hay URL, debe ser http(s) como cualquier cliente.
    url = str(cfg.get("api_url", "") or "").strip()
    if url and not url.startswith(("http://", "https://")):
        raise ValueError("api_url debe comenzar con http:// o https://")
    out["api_url"] = url

    out["api_key"] = str(cfg.get("api_key", "") or "").strip()

    model = str(cfg.get("model", "") or "").strip()
    if not model:
        raise ValueError("El modelo no puede estar vacío")
    out["model"] = model

    temperature = _as_float(cfg.get("temperature", DEFAULTS["temperature"]), "temperature")
    if not 0.0 <= temperature <= 2.0:
        raise ValueError("temperature debe estar entre 0 y 2")
    out["temperature"] = temperature

    timeout = _as_float(cfg.get("timeout", DEFAULTS["timeout"]), "timeout")
    if not 5.0 <= timeout <= 600.0:
        raise ValueError("timeout debe estar entre 5 y 600 segundos")
    out["timeout"] = timeout

    # Retención: entero de días, 0 = nunca borrar. _as_int acepta 7.0 pero
    # rechaza "muchos"; el negativo se descarta aquí.
    retention = _as_int(cfg.get("retention_days", DEFAULTS["retention_days"]), "retention_days")
    if retention < 0:
        raise ValueError("retention_days no puede ser negativo (0 = conservar para siempre)")
    out["retention_days"] = retention

    return out


def get() -> Dict[str, Any]:
    """Configuración vigente: por defecto + lo guardado en disco (disco gana)."""
    saved: Dict[str, Any] = {}
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                saved = data
    except (OSError, json.JSONDecodeError):
        saved = {}  # archivo ilegible → se ignoran los valores guardados

    # Si el archivo guardado ya no valida (p. ej. se editó a mano), se cae a
    # los defaults en lugar de tirar 500 en cada consulta.
    # Claves de versiones anteriores (num_ctx/keep_alive/num_predict, herencia
    # de Ollama local) no entran en DEFAULTS: validate() las descarta sola.
    try:
        return validate({**DEFAULTS, **saved})
    except ValueError:
        return dict(DEFAULTS)


def update(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Valida el parche, lo combina con lo vigente y persiste. ValueError si es inválido."""
    allowed = {k: v for k, v in (patch or {}).items() if k in DEFAULTS}
    merged = validate({**get(), **allowed})
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return merged


# ---------------------------------------------------------------------------
# Sonda de estado y conexión
# ---------------------------------------------------------------------------
def openai_base(api_url: str) -> Optional[str]:
    """Base del endpoint compatible con OpenAI, sin el sufijo de chat.

    ``https://api.groq.com/openai/v1/chat/completions`` →
    ``https://api.groq.com/openai/v1`` (de donde cuelga ``/models``).
    """
    parts = urlsplit((api_url or "").strip())
    if not parts.scheme or not parts.hostname:
        return None
    path = parts.path.rstrip("/")
    for sufijo in ("/chat/completions", "/completions"):
        if path.endswith(sufijo):
            path = path[: -len(sufijo)]
            break
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}{path}"


def _payload(cfg: Dict[str, Any], messages: List[Dict[str, str]], temperature: float) -> Dict[str, Any]:
    """Payload compatible con OpenAI: modelo, temperatura y mensajes.

    Sin tope de generación (``max_tokens``): los modelos con reasoning interno
    (p. ej. gpt-oss) consumen tokens ANTES de redactar, así que cualquier tope
    recortaba el JSON del informe a mitad de string. La salida la acota el
    proveedor.
    """
    return {
        "model": cfg["model"],
        "temperature": temperature,
        "messages": messages,
    }


def _content(body: Any) -> str:
    """Extrae el texto de la respuesta (formato OpenAI: choices[0].message)."""
    if not isinstance(body, dict):
        return ""
    try:
        return str(body["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return ""


async def chat(
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Un turno de chat contra el LLM. None si está apagado o falla (fallback)."""
    cfg = cfg or get()
    url = cfg["api_url"].strip()
    if not cfg["enabled"] or not url:
        return None

    temp = cfg["temperature"] if temperature is None else float(temperature)
    payload = _payload(cfg, messages, temp)
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    try:
        async with httpx.AsyncClient(timeout=cfg["timeout"]) as client:
            resp = None
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                except _HTTP_STATUS_ERROR as exc:
                    # Solo el 429 (rate limit) merece esperar: los demás 4xx/5xx
                    # son error del pedido o del proveedor y vuelven al local.
                    if exc.response.status_code != 429 or attempt >= _MAX_RETRIES:
                        raise
                    await asyncio.sleep(_retry_delay(attempt, exc.response))
                    continue
                break  # 2xx: sale del bucle de reintentos
            text = _content(resp.json()).strip()
    except Exception:  # noqa: BLE001 – cualquier fallo cae al modo local
        return None
    return text or None


async def chat_stream(
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    cfg: Optional[Dict[str, Any]] = None,
):
    """Versión en streaming del chat: va cediendo el texto generado por tokens.

    Un endpoint compatible con OpenAI responde SSE (``data: {...}`` con
    ``choices[0].delta.content`` y ``data: [DONE]`` al terminar). El llamador
    recibe cada trozo y, si la conexión cae a mitad, la excepción se propaga
    (él decide si muestra lo acumulado o cae al motor determinista).
    """
    cfg = cfg or get()
    url = cfg["api_url"].strip()
    if not cfg["enabled"] or not url:
        return

    temp = cfg["temperature"] if temperature is None else float(temperature)
    payload = {**_payload(cfg, messages, temp), "stream": True}
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    async with httpx.AsyncClient(timeout=cfg["timeout"]) as client:
        # Reintento solo en la APERTURA (antes del primer token): una vez que
        # el proveedor empezó a emitir, reabrir repetiría texto ya enviado.
        resp = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                request = client.build_request("POST", url, json=payload, headers=headers)
                resp = await client.send(request, stream=True)
                resp.raise_for_status()
            except _HTTP_STATUS_ERROR as exc:
                if resp is not None:
                    await resp.aclose()
                    resp = None
                if exc.response.status_code != 429 or attempt >= _MAX_RETRIES:
                    raise
                await asyncio.sleep(_retry_delay(attempt, exc.response))
                continue
            break
        try:
            async for line in resp.aiter_lines():
                line = (line or "").strip()
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
                try:
                    piece = data["choices"][0]["delta"].get("content")
                except (KeyError, IndexError, TypeError):
                    piece = None
                if piece:
                    yield piece
        finally:
            await resp.aclose()


async def probe(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Sonda rápida del proveedor: modelos disponibles y latencia.

    Consulta ``GET {base}/models`` con Bearer — la API key inválida o la cuota
    agotada aparecen como ``reachable: false`` con el motivo, en vez de fingir
    «sin inspección».

    Responde siempre 200: si no hay URL configurada devuelve
    ``kind: "none"``, y si nada responde, ``reachable: false`` con el motivo.
    En cualquier caso la app sigue operativa.
    """
    cfg = cfg or get()
    url = (cfg.get("api_url") or "").strip()
    if not url:
        return {"kind": "none", "reachable": None, "reason": None, "models": []}
    base = openai_base(url)
    if not base:
        return {"kind": "remote", "reachable": None, "reason": None, "models": []}

    headers = {"Accept": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {
            "kind": "remote",
            "reachable": False,
            "reason": str(exc) or "sin respuesta",
            "base": base,
            "models": [],
        }

    # Formato OpenAI: {"data": [{"id": "..."}]}. Se normaliza a {name} para
    # que el panel pueda comprobar si el modelo configurado está disponible.
    raw = data.get("data") if isinstance(data, dict) else None
    models = [
        {"name": str(m.get("id", ""))}
        for m in (raw if isinstance(raw, list) else [])
        if isinstance(m, dict) and m.get("id")
    ]
    return {
        "kind": "remote",
        "reachable": True,
        "reason": None,
        "base": base,
        "latency_ms": int((time.perf_counter() - start) * 1000),
        "models": models,
    }


async def test_connection(override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Prueba real: sonda de estado + un «ping» al modelo midiendo latencia.

    Acepta un parche de configuración para probar los valores del panel ANTES
    de guardarlos. El ping ignora ``enabled`` (probar es explícito): la sonda
    lista los modelos y el ping mide latencia real contra el mismo endpoint
    que usaría la app.
    """
    allowed = {k: v for k, v in (override or {}).items() if k in DEFAULTS}
    try:
        cfg = validate({**get(), **allowed})
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "status": None}

    status = await probe(cfg)
    if status.get("reachable") is False:
        return {
            "ok": False,
            "error": f"El proveedor no responde en {status.get('base')}: {status.get('reason')}",
            "status": status,
        }
    if status.get("reachable") is None:
        return {
            "ok": False,
            "error": "Falta la URL del proveedor: cargá una URL compatible con "
                     "OpenAI (…/v1/chat/completions) en el panel.",
            "status": status,
        }

    # Ping corto: una sola palabra basta para medir latencia real. Sin tope
    # de generación: la salida la acota el proveedor (los modelos con
    # reasoning devolverían content vacío si lo acotáramos nosotros).
    ping_cfg = {**cfg, "enabled": True}
    start = time.perf_counter()
    reply = await chat(
        [{"role": "user", "content": "Responde únicamente con la palabra: OK"}],
        temperature=0.0,
        cfg=ping_cfg,
    )
    latency_ms = int((time.perf_counter() - start) * 1000)
    if reply is None:
        return {
            "ok": False,
            "error": f"El modelo «{cfg['model']}» no respondió. El proveedor "
                     f"rechazó la llamada: revisá la API key y el nombre del modelo.",
            "latency_ms": latency_ms,
            "status": status,
        }
    return {
        "ok": True,
        "error": None,
        "latency_ms": latency_ms,
        "reply": reply[:200],
        "model": cfg["model"],
        "status": status,
    }
