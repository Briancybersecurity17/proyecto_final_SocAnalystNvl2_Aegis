"""Panel «Opciones IA»: GET/PUT de la configuración del LLM y sonda de Ollama.

Los tests redirigen `ai_config.CONFIG_PATH` a un tmp para no tocar (ni leer)
el `data/ai_config.json` real del analista. Se importa el módulo desde `main`
para garantizar que es EXACTAMENTE el que usan los endpoints.
"""

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
    # en el tmp ⇒ defaults del módulo, incluido el modelo local qwen2.5:3b).
    assert set(cfg) == set(ai.DEFAULTS)
    assert cfg["model"] == ai.DEFAULTS["model"]
    assert cfg["enabled"] is True

    status = data["status"]
    assert status["kind"] in ("ollama", "remote", "none")
    assert isinstance(status["models"], list)
    assert isinstance(status["loaded"], list)
    # bool cuando es Ollama inspeccionable; None cuando la URL es remota.
    assert data["model_installed"] in (True, False, None)


def test_put_persiste_y_get_lo_refleja(client, ai):
    res = client.put(
        "/api/ai/config",
        json={"temperature": 0.2, "num_ctx": 2048, "keep_alive": "-1"},
    )
    assert res.status_code == 200
    cfg = res.json()["config"]
    assert cfg["temperature"] == 0.2
    assert cfg["num_ctx"] == 2048
    assert cfg["keep_alive"] == "-1"
    # El resto de los campos se conserva (merger con lo vigente).
    assert cfg["model"] == ai.DEFAULTS["model"]

    assert ai.CONFIG_PATH.exists(), "PUT debe persistir en disco"
    again = client.get("/api/ai/config").json()["config"]
    assert again["temperature"] == 0.2
    assert again["num_ctx"] == 2048


def test_put_enabled_false_no_se_pierde(client, ai):
    """False debe guardarse (no es «ausente»): apaga el LLM del panel."""
    res = client.put("/api/ai/config", json={"enabled": False})
    assert res.status_code == 200
    assert res.json()["config"]["enabled"] is False
    assert client.get("/api/ai/config").json()["config"]["enabled"] is False


@pytest.mark.parametrize(
    "patch",
    [
        {"temperature": 3.0},      # fuera de rango
        {"num_ctx": 64},           # por debajo del mínimo
        {"model": "   "},          # vacío
        {"api_url": "ftp://x"},    # no http(s)
        {"keep_alive": "para siempre"},  # formato ilegible
        {"num_predict": 99999},    # fuera de rango
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


def test_test_endpoint_responde_estructura(client, ai):
    """POST /api/ai/test: siempre 200 con ok/status; el ping usa keep_alive=0
    para no dejar el modelo residente en VRAM tras el test."""
    res = client.post("/api/ai/test", json={"keep_alive": "0"})
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["ok"], bool)
    if data["ok"]:
        assert isinstance(data["latency_ms"], int)
        assert data["reply"]
        assert data["model"]
    else:
        # Ollama caído o modelo ausente: hay motivo legible, nunca un 500.
        assert data["error"]
    assert data["status"]["kind"] == "ollama"


def test_sonda_detecta_modelos_instalados(client, ai):
    """Si Ollama responde, la sonda lista los modelos con nombre y tamaño."""
    status = client.get("/api/ai/config").json()["status"]
    if status.get("reachable"):
        assert status["models"], "Ollama arriba debe listar al menos un modelo"
        assert all("name" in m for m in status["models"])
    else:
        assert status.get("reason"), "si no responde, debe venir el motivo"


def test_endpoints_existen_en_openapi(client):
    """Las rutas del panel forman parte del contrato de la API."""
    rutas = client.get("/openapi.json").json()["paths"]
    assert "/api/ai/config" in rutas
    assert set(rutas["/api/ai/config"]) >= {"get", "put"}
    assert "/api/ai/test" in rutas
    assert "post" in rutas["/api/ai/test"]


def test_app_es_importable():  # guarda contra imports rotos en main/ai_service
    assert app.title.startswith("Comunidad Dojo")
    assert callable(ai_config.get) and callable(ai_config.probe)


# ---------------------------------------------------------------------------
# Proveedor remoto compatible con OpenAI (p. ej. Groq)
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
    """URL compatible OpenAI: la sonda hace GET {base}/models con Authorization.

    Sin esto el panel remoto vive en `reachable: null` («sin inspección») y no
    puede ofrecer el datalist de modelos ni saber si la API key es válida.
    """
    ai.update(_cfg_remota())
    http, calls = _fake_http(models={"data": [
        {"id": "openai/gpt-oss-120b"},
        {"id": "qwen/qwen3.8-27b"},
    ]})
    monkeypatch.setattr(ai, "httpx", http)

    status = client.get("/api/ai/config").json()["status"]

    assert status["kind"] == "remote"
    assert status["reachable"] is True
    names = [m.get("name") for m in status["models"]]
    assert "openai/gpt-oss-120b" in names

    gets = [c for c in calls if c[0] == "GET"]
    assert gets, "la sonda debe consultar el listado de modelos"
    assert gets[0][1] == "https://api.groq.com/openai/v1/models"
    assert gets[0][2]["Authorization"] == "Bearer gsk_test_key"


def test_test_connection_remoto_hace_ping(client, ai, monkeypatch):
    """POST /api/ai/test con URL remota debe probar el chat, no rechazarla.

    Hoy devuelve «la sonda solo aplica a localhost:11434»: imposible validar
    un proveedor remoto desde el panel aunque la API key y el modelo sean
    correctos.
    """
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


def test_payload_openai_no_acota_por_num_predict(ai):
    """El tope `num_predict` NO viaja al dialecto OpenAI/compatible.

    Es un límite pensado para Ollama local (VRAM y generación a ~35 tok/s).
    En un proveedor remoto acortaba las salidas: los modelos con reasoning
    interno consumen tokens ANTES de redactar, así que el JSON del informe
    llegaba truncado a mitad de string y se descartaba entero.
    """
    cfg = {**ai.DEFAULTS, **_cfg_remota(), "num_predict": 1600}
    mensajes = [{"role": "user", "content": "hola"}]

    for tope in (1600, 512, -1):
        payload = ai._payload({**cfg, "num_predict": tope}, mensajes, 0.2)
        assert "max_tokens" not in payload, (
            f"num_predict={tope} no debe acotar la salida de un proveedor remoto"
        )


def test_ping_remoto_no_acota_la_salida(client, ai, monkeypatch):
    """El ping a un proveedor remoto no debe llevar tope de generación.

    Modelos con reasoning interno (p. ej. gpt-oss) consumen tokens en
    razonamiento ANTES de redactar: cualquier `max_tokens` recortado —el de
    16 del ping de Ollama— devolvía `content` vacío y el panel informaba
    «no respondió» con la API key correcta. En dialecto remoto la salida la
    acota el proveedor, no el panel.
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
        "el ping remoto no debe acotar la salida: el proveedor aplica su tope"
    )
