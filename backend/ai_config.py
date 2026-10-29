"""Configuración persistente de la IA y conexión con el LLM local (Ollama).

Panel «Opciones IA»: el modelo, la URL y los parámetros de generación viven en
un JSON en disco (``backend/data/ai_config.json``) y se cambian en caliente,
sin reiniciar el backend: Ollama recibe las opciones en cada request, así que
el próximo pedido ya usa los valores nuevos.

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

Dos dialectos de API, detectados por la URL:
    * Ollama nativo (``/api/chat``, por defecto ``localhost:11434``): admite
      ``options`` (num_ctx, num_predict, temperature), ``keep_alive`` y
      ``stream`` por request → es el que usa el panel.
    * Compatible con OpenAI (``/v1/chat/completions``): payload clásico, para
      un proveedor remoto si algún día se configura.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

# Se lee como atributo del módulo en cada llamada a propósito: los tests
# redirigen esta ruta con monkeypatch para no tocar la configuración real.
CONFIG_PATH = Path(__file__).resolve().parent / "data" / "ai_config.json"

OLLAMA_DEFAULT_URL = "http://localhost:11434/api/chat"

# keep_alive: «-1» (eterno), duración con unidad (30m, 10s…) o segundos puros.
_KEEP_ALIVE_RE = re.compile(r"^-1$|^[0-9]+(ns|us|µs|ms|s|m|h)?$")

DEFAULTS: Dict[str, Any] = {
    # El LLM local viene encendido: el proyecto está pensado para Ollama en la
    # máquina del analista. Si no responde, todo vuelve al motor determinista.
    "enabled": os.getenv("AI_ENABLED", "1").strip().lower() not in ("0", "false", "no"),
    "api_url": os.getenv("AI_API_URL", "").strip() or OLLAMA_DEFAULT_URL,
    "api_key": os.getenv("AI_API_KEY", "").strip(),
    # qwen2.5:3b Q4_K_M ≈ 1.9 GB: entra cómodo en la GTX 1060 de 6 GB y rinde
    # de sobra para respuestas de SOC con 8 evidencias de contexto.
    "model": os.getenv("AI_MODEL", "").strip() or "qwen2.5:3b",
    # num_ctx=4096. Medido con el prompt REAL (build_qa_context + _qa_messages,
    # el código de verdad, no una réplica) contra Ollama:
    #   • 1 consulta suelta: 1669 tok
    #   • 4 turnos de memoria: 2253 tok  → con 2048 SE PERDÍAN 292 tok
    #   • 4 turnos + 2 ejemplos few-shot: 2545 tok
    # Ollama recorta los mensajes MÁS VIEJOS al pasar de num_ctx, y los más
    # viejos son el system prompt (reglas de no inventar Log_ID): el modelo
    # dejaba de seguir las instrucciones justo en las conversaciones largas.
    # El rango 2048–4096 lo descarta este proyecto por diseño (prompt de
    # evidencia + 4 turnos), así que 4096 es el mínimo holgado, no un capricho.
    #
    # El prefill SÍ corre en GPU a este tamaño (medido: ~30 ms con prompts de
    # 1–2k tok incluso a num_ctx=16384); lo que cuesta ~10 s es CAMBIAR de
    # num_ctx, porque Ollama reasigna el KV cache al vuelo, una sola vez.
    "num_ctx": 4096,
    "temperature": 0.15,
    "keep_alive": "30m",
    # Tope de generación. Medido con done_reason de Ollama (la prueba
    # definitiva de si una respuesta se corta): 240 → done=length (cortada a
    # destallo, ~106 palabras), 384 → length, 512 → done=stop (termina sola,
    # ~178 palabras). El prompt pide ~150 palabras + citas de Log_ID, así que
    # 240 quedaba corto. Con 512 el modelo completa la respuesta y, contra lo
    # esperado, es MÁS rápido que 384 (para en stop en vez de rellenar):
    # ~9 s vs ~11 s de generación a 35 tok/s.
    "num_predict": 512,
    "timeout": float(os.getenv("AI_TIMEOUT", "60")),
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

    url = str(cfg.get("api_url", "") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("api_url debe comenzar con http:// o https://")
    out["api_url"] = url

    out["api_key"] = str(cfg.get("api_key", "") or "").strip()

    model = str(cfg.get("model", "") or "").strip()
    if not model:
        raise ValueError("El modelo no puede estar vacío")
    out["model"] = model

    num_ctx = _as_int(cfg.get("num_ctx", DEFAULTS["num_ctx"]), "num_ctx")
    if not 512 <= num_ctx <= 65536:
        raise ValueError("num_ctx debe estar entre 512 y 65536")
    out["num_ctx"] = num_ctx

    temperature = _as_float(cfg.get("temperature", DEFAULTS["temperature"]), "temperature")
    if not 0.0 <= temperature <= 2.0:
        raise ValueError("temperature debe estar entre 0 y 2")
    out["temperature"] = temperature

    keep_alive = cfg.get("keep_alive", DEFAULTS["keep_alive"])
    if isinstance(keep_alive, (int, float)) and not isinstance(keep_alive, bool):
        keep_alive = str(int(keep_alive))
    keep_alive = str(keep_alive).strip()
    if not _KEEP_ALIVE_RE.match(keep_alive):
        raise ValueError("keep_alive debe ser «-1», segundos o una duración como «30m»")
    out["keep_alive"] = keep_alive

    num_predict = _as_int(cfg.get("num_predict", DEFAULTS["num_predict"]), "num_predict")
    if not -1 <= num_predict <= 8192:
        raise ValueError("num_predict debe estar entre -1 (sin límite) y 8192")
    out["num_predict"] = num_predict

    timeout = _as_float(cfg.get("timeout", DEFAULTS["timeout"]), "timeout")
    if not 5.0 <= timeout <= 600.0:
        raise ValueError("timeout debe estar entre 5 y 600 segundos")
    out["timeout"] = timeout

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
# Dialecto de API y sonda de estado
# ---------------------------------------------------------------------------
def is_ollama(api_url: str) -> bool:
    """¿La URL apunta a un Ollama local (puerto 11434 o ruta /api/chat)?"""
    parts = urlsplit((api_url or "").strip())
    if parts.port == 11434:
        return True
    return parts.path.rstrip("/").endswith("/api/chat")


def ollama_base(api_url: str) -> Optional[str]:
    """Base del servidor Ollama (scheme://host:puerto) o None si no lo es."""
    if not is_ollama(api_url):
        return None
    parts = urlsplit(api_url.strip())
    if not parts.scheme or not parts.hostname:
        return None
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def _payload(cfg: Dict[str, Any], messages: List[Dict[str, str]], temperature: float) -> Dict[str, Any]:
    """Payload según el dialecto de la URL configurada."""
    if is_ollama(cfg["api_url"]):
        # Ollama nativo: las opciones viajan por request (por eso el panel no
        # necesita reiniciar nada para aplicar cambios).
        return {
            "model": cfg["model"],
            "messages": messages,
            "stream": False,
            "keep_alive": cfg["keep_alive"],
            "options": {
                "temperature": temperature,
                "num_ctx": cfg["num_ctx"],
                "num_predict": cfg["num_predict"],
            },
        }
    # Compatible con OpenAI (proveedor remoto).
    return {
        "model": cfg["model"],
        "temperature": temperature,
        "messages": messages,
    }


def _content(body: Any) -> str:
    """Extrae el texto de la respuesta en cualquiera de los dos dialectos."""
    if not isinstance(body, dict):
        return ""
    try:  # OpenAI: choices[0].message.content
        return str(body["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        pass
    message = body.get("message")  # Ollama nativo: message.content
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(body.get("content") or "")


async def chat(
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    cfg: Optional[Dict[str, Any]] = None,
    num_predict: Optional[int] = None,
) -> Optional[str]:
    """Un turno de chat contra el LLM. None si está apagado o falla (fallback).

    ``num_predict`` permite acotar/levantar el tope de generación SOLO para
    esta llamada (p. ej. el informe es largo y no cabe en el tope del chat)
    sin mutar la configuración guardada.
    """
    cfg = cfg or get()
    url = cfg["api_url"].strip()
    if not cfg["enabled"] or not url:
        return None
    if num_predict is not None:
        cfg = {**cfg, "num_predict": int(num_predict)}

    temp = cfg["temperature"] if temperature is None else float(temperature)
    payload = _payload(cfg, messages, temp)
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    try:
        async with httpx.AsyncClient(timeout=cfg["timeout"]) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            text = _content(resp.json()).strip()
    except Exception:  # noqa: BLE001 – cualquier fallo cae al modo local
        return None
    return text or None


async def chat_stream(
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    cfg: Optional[Dict[str, Any]] = None,
    num_predict: Optional[int] = None,
):
    """Versión en streaming del chat: va cediendo el texto generado por tokens.

    Ollama nativo responde NDJSON (un objeto JSON por línea, `message.content`
    por trozo y `done: true` al final); un endpoint compatible con OpenAI
    responde SSE (`data: {...}` con `choices[0].delta.content`). El llamador
    recibe cada trozo y, si la conexión cae a mitad, la excepción se propaga
    (él decide si muestra lo acumulado o cae al motor determinista).
    """
    cfg = cfg or get()
    url = cfg["api_url"].strip()
    if not cfg["enabled"] or not url:
        return
    if num_predict is not None:
        cfg = {**cfg, "num_predict": int(num_predict)}

    temp = cfg["temperature"] if temperature is None else float(temperature)
    payload = {**_payload(cfg, messages, temp), "stream": True}
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    native = is_ollama(url)
    async with httpx.AsyncClient(timeout=cfg["timeout"]) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = (line or "").strip()
                if not line:
                    continue
                if native:
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    if data.get("error"):
                        raise RuntimeError(str(data["error"]))
                    piece = (data.get("message") or {}).get("content")
                    if piece:
                        yield piece
                    if data.get("done"):
                        break
                else:  # SSE de OpenAI: «data: {...}» / «data: [DONE]»
                    if not line.startswith("data:"):
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


async def probe(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Sonda rápida de Ollama: modelos instalados (tags), cargados (ps) y latencia.

    Responde siempre 200: si no es Ollama o no responde, devuelve
    ``reachable: false`` con el motivo y la app sigue operativa.
    """
    cfg = cfg or get()
    base = ollama_base(cfg["api_url"])
    if not base:
        return {
            "kind": "remote" if cfg["api_url"] else "none",
            "reachable": None,
            "reason": None,
            "models": [],
            "loaded": [],
        }

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            tags = await client.get(f"{base}/api/tags")
            tags.raise_for_status()
            models = tags.json().get("models", []) or []
            try:
                ps = await client.get(f"{base}/api/ps")
                loaded = ps.json().get("models", []) or [] if ps.is_success else []
            except Exception:  # noqa: BLE001 – ps es informativo, no bloquea
                loaded = []
    except Exception as exc:  # noqa: BLE001
        return {
            "kind": "ollama",
            "reachable": False,
            "reason": str(exc) or "sin respuesta",
            "base": base,
            "models": [],
            "loaded": [],
        }

    return {
        "kind": "ollama",
        "reachable": True,
        "reason": None,
        "base": base,
        "latency_ms": int((time.perf_counter() - start) * 1000),
        "models": models,
        "loaded": loaded,
    }


async def test_connection(override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Prueba real: sonda de estado + un «ping» al modelo midiendo latencia.

    Acepta un parche de configuración para probar los valores del panel ANTES
    de guardarlos. El ping ignora ``enabled`` (probar es explícito) y recorta
    ``num_predict`` para que la respuesta sea instantánea.
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
            "error": f"Ollama no responde en {status.get('base')}: {status.get('reason')}",
            "status": status,
        }
    if status.get("reachable") is None:
        return {
            "ok": False,
            "error": "La URL configurada no es un Ollama local: la sonda de "
                     "estado solo aplica a localhost:11434",
            "status": status,
        }

    ping_cfg = {**cfg, "enabled": True, "num_predict": min(cfg["num_predict"], 16)}
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
            "error": f"El modelo «{cfg['model']}» no respondió. ¿Está instalado? "
                     "(lista: ollama list)",
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


async def warmup(timeout: float = 90.0) -> bool:
    """Precarga el modelo del LLM en Ollama (arranque en segundo plano).

    El arranque de llama-server y la carga del GGUF en la GPU cuestan ~4 s:
    se pagan aquí, mientras el usuario ve el Dashboard, y no en la primera
    pregunta del asistente. Es una llamada «OK» de8 tokens: solo fuerza a
    Ollama a dejar el modelo residente (keep_alive lo mantiene).

    Silencioso por contrato: si el LLM está apagado, Ollama no responde o el
    modelo no está instalado, devuelve False y la app sigue operativa con el
    motor determinista (mismo criterio que el resto de los accesos a la IA).
    """
    cfg = get()
    if not cfg["enabled"]:
        return False
    if (await probe(cfg)).get("reachable") is not True:
        return False
    try:
        reply = await asyncio.wait_for(
            chat(
                [{"role": "user", "content": "OK"}],
                temperature=0.0,
                cfg=cfg,
                num_predict=8,
            ),
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 – cualquier fallo no rompe el arranque
        return False
    return reply is not None
