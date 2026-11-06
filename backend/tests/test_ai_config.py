"""Panel «Opciones IA»: GET/PUT de la configuración del LLM y sonda del proveedor.

El proyecto es 100% cloud: un único dialecto compatible con OpenAI (Groq por
defecto). No hay soporte de Ollama local, así que tampoco `keep_alive`,
`num_ctx`, `num_predict` ni `warmup`.

Los tests redirigen `ai_config.CONFIG_PATH` a un tmp para no tocar (ni leer)
el `data/ai_config.json` real del analista. Se importa el módulo desde `main`
para garantizar que es EXACTAMENTE el que usan los endpoints.
"""

import asyncio

import httpx
import pytest

from main import app, ai_config


@pytest.fixture
def ai(tmp_path, monkeypatch):
    """Configuración aislada en tmp: los tests no tocan el archivo real."""
    monkeypatch.setattr(ai_config, "CONFIG_PATH", tmp_path / "ai_config.json")
    return ai_config


def test_get_devuelve_config_completa_y_sonda(client, ai):
    res = client.get("/api/ai/config")
    assert res.status_code == 200
    data = res.json()

    cfg = data["config"]
    # Todas las claves del panel, con los defaults vigentes (archivo inexistente
    # en el tmp ⇒ defaults del módulo: proveedor sin configurar todavía).
    assert set(cfg) == set(ai.DEFAULTS)
    assert cfg["model"] == ai.DEFAULTS["model"]
    assert cfg["enabled"] is True
    # Sin parámetros locales heredados de Ollama.
    assert not {"num_ctx", "keep_alive", "num_predict"} & set(cfg)

    status = data["status"]
    assert status["kind"] in ("remote", "none")
    assert isinstance(status["models"], list)
    # bool cuando el proveedor lista modelos; None si aún no hay URL.
    assert data["model_installed"] in (True, False, None)


def test_get_sin_url_no_toca_la_red(client, ai):
    """Sin proveedor configurado la sonda es `none`: nada de llamadas externas."""
    status = client.get("/api/ai/config").json()["status"]
    assert status["kind"] == "none"
    assert status["reachable"] is None
    assert status["models"] == []


def test_put_persiste_y_get_lo_refleja(client, ai):
    res = client.put("/api/ai/config", json={"temperature": 0.2, "timeout": 30.0})
    assert res.status_code == 200
    cfg = res.json()["config"]
    assert cfg["temperature"] == 0.2
    assert cfg["timeout"] == 30.0
    # El resto de los campos se conserva (merger con lo vigente).
    assert cfg["model"] == ai.DEFAULTS["model"]

    assert ai.CONFIG_PATH.exists(), "PUT debe persistir en disco"
    again = client.get("/api/ai/config").json()["config"]
    assert again["temperature"] == 0.2
    assert again["timeout"] == 30.0


def test_put_enabled_false_no_se_pierde(client, ai):
    """False debe guardarse (no es «ausente»): apaga el LLM del panel."""
    res = client.put("/api/ai/config", json={"enabled": False})
    assert res.status_code == 200
    assert res.json()["config"]["enabled"] is False
    assert client.get("/api/ai/config").json()["config"]["enabled"] is False


def test_put_ignora_campos_solo_ollama(client, ai):
    """Claves de la época de Ollama local: se descartan, nunca se guardan."""
    res = client.put(
        "/api/ai/config",
        json={"num_ctx": 8192, "keep_alive": "-1", "num_predict": 4096},
    )
    assert res.status_code == 200
    cfg = res.json()["config"]
    assert not {"num_ctx", "keep_alive", "num_predict"} & set(cfg)
    assert cfg == ai.DEFAULTS


@pytest.mark.parametrize(
    "patch",
    [
        {"temperature": 3.0},      # fuera de rango
        {"model": "   "},          # vacío
        {"api_url": "ftp://x"},    # no http(s)
        {"timeout": 1},            # demasiado corto
    ],
)
def test_put_rechaza_valores_invalidos(client, ai, patch):
    res = client.put("/api/ai/config", json=patch)
    assert res.status_code == 400
    assert "detail" in res.json()
    # Nada quedó guardado: GET sigue devolviendo los defaults intactos.
    assert client.get("/api/ai/config").json()["config"] == ai.DEFAULTS


def test_config_invalida_en_disco_cae_a_defaults(client, ai):
    """Un archivo editado a mano con basura no debe tirar 500 en el GET."""
    ai.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ai.CONFIG_PATH.write_text("{esto no es json", encoding="utf-8")
    res = client.get("/api/ai/config")
    assert res.status_code == 200
    assert res.json()["config"]["model"] == ai.DEFAULTS["model"]


def test_config_guardada_con_claves_ollama_se_ignoran(client, ai):
    """Un ai_config.json heredado (num_ctx/keep_alive/num_predict) no rompe nada.

    Es el caso real del analista: su archivo de la época de Ollama sigue en
    disco. Las claves ajenas a DEFAULTS se descartan al leer y no vuelven a
    guardarse.
    """
    ai.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ai.CONFIG_PATH.write_text(
        '{"enabled": true, "api_url": "https://api.groq.com/openai/v1/chat/completions",'
        ' "api_key": "gsk_test", "model": "openai/gpt-oss-120b", "num_ctx": 4096,'
        ' "keep_alive": "30m", "num_predict": 512, "temperature": 0.15,'
        ' "timeout": 60.0}',
        encoding="utf-8",
    )
    cfg = client.get("/api/ai/config").json()["config"]
    assert cfg["model"] == "openai/gpt-oss-120b"
    assert cfg["temperature"] == 0.15
    assert not {"num_ctx", "keep_alive", "num_predict"} & set(cfg)


def test_test_endpoint_responde_estructura(client, ai):
    """POST /api/ai/test: siempre 200 con ok/status (nunca un 500)."""
    res = client.post("/api/ai/test")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["ok"], bool)
    if data["ok"]:
        assert isinstance(data["latency_ms"], int)
        assert data["reply"]
        assert data["model"]
    else:
        # Sin URL cargada o proveedor caído: hay motivo legible.
        assert data["error"]
    assert data["status"]["kind"] in ("remote", "none")


def test_endpoints_existen_en_openapi(client):
    """Las rutas del panel forman parte del contrato de la API."""
    rutas = client.get("/openapi.json").json()["paths"]
    assert "/api/ai/config" in rutas
    assert set(rutas["/api/ai/config"]) >= {"get", "put"}
    assert "/api/ai/test" in rutas
    assert "post" in rutas["/api/ai/test"]


def test_app_es_importable():  # guarda contra imports rotos en main/ai_service
    assert app.title.startswith("AEGIS")
    assert callable(ai_config.get) and callable(ai_config.probe)


def test_dialecto_ollama_fuera_del_codigo():
    """La purga cloud debe ser completa: ni funciones ni constants locales."""
    for name in ("is_ollama", "ollama_base", "warmup", "OLLAMA_DEFAULT_URL"):
        assert not hasattr(ai_config, name), f"{name} sigue en ai_config"
    assert "OLLAMA" not in open(ai_config.__file__, encoding="utf-8").read()


# ---------------------------------------------------------------------------
# Proveedor compatible con OpenAI (p. ej. Groq)
# ---------------------------------------------------------------------------
class _FakeResponse:
    """Respuesta httpx falsa con la interfaz mínima que consume ai_config."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _fake_http(models=None, chat_reply="OK", models_status=200, models_error=None):
    """Transporte httpx falso: GET {base}/models y POST de chat (dialecto OpenAI).

    Devuelve (namespace para monkeypatchear ai_config.httpx, lista de llamadas).
    Cada llamada queda registrada como (método, url, headers) para poder
    asertar contra la URL y la cabecera Authorization.
    """
    calls: list = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs.get("headers"), None))
            if models_error is not None:
                raise models_error
            return _FakeResponse(models if models is not None else {"data": []},
                                 models_status)

        async def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs.get("headers"), kwargs.get("json")))
            return _FakeResponse(
                {"choices": [{"message": {"content": chat_reply}}]}
            )

    from types import SimpleNamespace
    return SimpleNamespace(AsyncClient=_FakeAsyncClient), calls


def _cfg_remota():
    """Config apuntando a un endpoint compatible OpenAI (forma de Groq)."""
    return {
        "api_url": "https://api.groq.com/openai/v1/chat/completions",
        "api_key": "gsk_test_key",
        "model": "openai/gpt-oss-120b",
    }


def test_probe_remoto_lista_modelos_con_bearer(client, ai, monkeypatch):
    """La sonda hace GET {base}/models con Authorization.

    Sin esto el panel vive en `reachable: null` («sin inspección») y no puede
    ofrecer la lista de modelos ni saber si la API key es válida.
    """
    ai.update(_cfg_remota())
    http, calls = _fake_http(models={"data": [
        {"id": "openai/gpt-oss-120b"},
        {"id": "qwen/qwen3.8-27b"},
    ]})
    monkeypatch.setattr(ai, "httpx", http)

    data = client.get("/api/ai/config").json()
    status = data["status"]

    assert status["kind"] == "remote"
    assert status["reachable"] is True
    names = [m.get("name") for m in status["models"]]
    assert "openai/gpt-oss-120b" in names
    # El modelo configurado está en la lista ⇒ el panel puede marcarlo.
    assert data["model_installed"] is True

    gets = [c for c in calls if c[0] == "GET"]
    assert gets, "la sonda debe consultar el listado de modelos"
    assert gets[0][1] == "https://api.groq.com/openai/v1/models"
    assert gets[0][2]["Authorization"] == "Bearer gsk_test_key"


def test_probe_remoto_con_key_mala_marca_model_ausente(client, ai, monkeypatch):
    """El proveedor responde pero no ofrece el modelo configurado."""
    ai.update(_cfg_remota())
    http, _ = _fake_http(models={"data": [{"id": "otro/modelo"}]})
    monkeypatch.setattr(ai, "httpx", http)

    data = client.get("/api/ai/config").json()
    assert data["status"]["reachable"] is True
    assert data["model_installed"] is False


def test_test_connection_remoto_hace_ping(client, ai, monkeypatch):
    """POST /api/ai/test con URL remota debe probar el chat, no rechazarla."""
    ai.update(_cfg_remota())
    http, calls = _fake_http(models={"data": [{"id": "openai/gpt-oss-120b"}]},
                             chat_reply="OK")
    monkeypatch.setattr(ai, "httpx", http)

    data = client.post("/api/ai/test", json={}).json()

    assert data["ok"] is True, f"esperaba ok=True, llegó: {data.get('error')}"
    assert data["reply"]
    assert data["status"]["kind"] == "remote"
    posts = [c for c in calls if c[0] == "POST"]
    assert posts and posts[0][1] == _cfg_remota()["api_url"]
    assert posts[0][2]["Authorization"] == "Bearer gsk_test_key"


def test_payload_openai_no_lleva_topes_ni_opciones_locales(ai):
    """El payload va pelado (modelo, temperatura, mensajes).

    Sin `max_tokens`: los modelos con reasoning interno (p. ej. gpt-oss)
    consumen tokens ANTES de redactar, así que cualquier tope recortaba el
    JSON del informe a mitad de string y se descartaba entero. Sin
    `options`/`keep_alive`/`num_ctx`: eran del dialecto Ollama local.
    """
    cfg = {**ai.DEFAULTS, **_cfg_remota()}
    mensajes = [{"role": "user", "content": "hola"}]

    payload = ai._payload(cfg, mensajes, 0.2)
    assert set(payload) == {"model", "temperature", "messages"}
    assert payload["model"] == "openai/gpt-oss-120b"

    # Con restos de la config vieja en la entrada, tampoco se cuelan.
    legacy = {**cfg, "num_ctx": 8192, "keep_alive": "-1", "num_predict": 1600}
    assert set(ai._payload(legacy, mensajes, 0.2)) == {"model", "temperature", "messages"}


def test_ping_remoto_no_acota_la_salida(client, ai, monkeypatch):
    """El ping al proveedor no lleva tope de generación.

    Modelos con reasoning interno (p. ej. gpt-oss) consumen tokens en
    razonamiento ANTES de redactar: cualquier `max_tokens` recortado
    devolvía `content` vacío y el panel informaba «no respondió» con la API
    key correcta. La salida la acota el proveedor, no el panel.
    """
    ai.update(_cfg_remota())
    http, calls = _fake_http(models={"data": [{"id": "openai/gpt-oss-120b"}]},
                             chat_reply="OK")
    monkeypatch.setattr(ai, "httpx", http)

    data = client.post("/api/ai/test", json={}).json()
    assert data["ok"] is True, data.get("error")

    posts = [c for c in calls if c[0] == "POST"]
    payload = posts[0][3]
    assert "max_tokens" not in payload, (
        "el ping no debe acotar la salida: el proveedor aplica su tope"
    )


# ---------------------------------------------------------------------------
# Reintentos ante un 429 (rate limit del proveedor cloud)
# ---------------------------------------------------------------------------
def _fake_http_retry(secuencias, headers=None):
    """Transporte httpx falso que aplica una secuencia de respuestas HTTP.

    Cada `post` consume la posición siguiente de `secuencias` (códigos 429/200)
    y devuelve un `httpx.Response` REAL, de modo que `raise_for_status()` lanza
    el `httpx.HTTPStatusError` auténtico — no un doble — y los reintentos de
    `chat()` se ejercitan de verdad. Devuelve (namespace, llamadas).
    """
    from types import SimpleNamespace

    calls: list = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            status = secuencias[min(len(calls), len(secuencias) - 1)]
            calls.append(("POST", url, kwargs.get("headers"), status))
            return httpx.Response(
                status,
                request=httpx.Request("POST", url),
                headers=headers or {},
                json={"choices": [{"message": {"content": "OK"}}]}
                if status == 200 else {},
            )

    return SimpleNamespace(AsyncClient=_FakeAsyncClient), calls


def _sin_dormir(monkeypatch):
    """Los reintentos NO esperan de verdad en el test: solo se registran."""
    esperas: list = []

    async def _sleep(delay):
        esperas.append(delay)

    monkeypatch.setattr(ai_config.asyncio, "sleep", _sleep)
    return esperas


def test_chat_reintenta_tras_429_y_recupera(ai, monkeypatch):
    """Un 429 del proveedor no debe arrastrar el informe al motor local.

    Antes del reintento, `chat()` devolvía None al primer tope de cuota y las
    cuatro secciones de prosa caían al texto determinista (medido: 1 de 6
    informes llegaba con LLM). Con reintento, el segundo intento devuelve 200
    y el texto llega entero.
    """
    http, calls = _fake_http_retry([429, 200])
    monkeypatch.setattr(ai, "httpx", http)
    esperas = _sin_dormir(monkeypatch)
    cfg = {**ai.DEFAULTS, **_cfg_remota()}

    texto = asyncio.run(ai.chat([{"role": "user", "content": "hola"}], cfg=cfg))

    assert texto == "OK", "tras un 429 seguido de un 200 el texto debe llegar"
    assert len(calls) == 2, "debe reintentar una sola vez y aceptar el 200"
    assert esperas, "debe esperar (backoff) antes del reintento"


def test_chat_429_persistente_cae_al_local_sin_bucle(ai, monkeypatch):
    """Si el proveedor sigue devolviendo 429, se agota el tope y vuelve el local."""
    http, calls = _fake_http_retry([429])
    monkeypatch.setattr(ai, "httpx", http)
    esperas = _sin_dormir(monkeypatch)
    cfg = {**ai.DEFAULTS, **_cfg_remota()}

    texto = asyncio.run(ai.chat([{"role": "user", "content": "hola"}], cfg=cfg))

    assert texto is None, "sin 200, chat() sigue devolviendo None (fallback local)"
    assert len(calls) == ai._MAX_RETRIES, "el tope de reintentos es finito, sin bucle"
    assert len(esperas) == ai._MAX_RETRIES - 1, "espera entre intentos, nunca tras el último"


def test_chat_otro_error_no_reintenta(ai, monkeypatch):
    """Solo el 429 merece reintento: un 500 o un 404 vuelven al local ya."""
    http, calls = _fake_http_retry([500])
    monkeypatch.setattr(ai, "httpx", http)
    esperas = _sin_dormir(monkeypatch)
    cfg = {**ai.DEFAULTS, **_cfg_remota()}

    texto = asyncio.run(ai.chat([{"role": "user", "content": "hola"}], cfg=cfg))

    assert texto is None
    assert len(calls) == 1, "los errores que no son 429 no se reintentan"
    assert esperas == []


def test_retry_delay_respeta_retry_after_y_lo_tapa(ai):
    """El backoff manda en `Retry-After` si el proveedor lo envía, con tope."""
    respuesta = httpx.Response(
        429, request=httpx.Request("POST", "https://x"), headers={"retry-after": "7"}
    )
    assert ai._retry_delay(1, respuesta) == 7.0

    # Un `Retry-After` desmesurado no congela la petición del analista.
    gigante = httpx.Response(
        429, request=httpx.Request("POST", "https://x"), headers={"retry-after": "9999"}
    )
    assert ai._retry_delay(1, gigante) == ai._RETRY_AFTER_MAX

    # Sin cabecera (o ilegible) cae al backoff exponencial.
    assert ai._retry_delay(1, httpx.Response(429, request=httpx.Request("POST", "https://x"))) \
        == ai._BACKOFF_BASE
    assert ai._retry_delay(2, httpx.Response(429, request=httpx.Request("POST", "https://x"))) \
        == ai._BACKOFF_BASE * 2
    assert ai._retry_delay(1, None) == ai._BACKOFF_BASE
