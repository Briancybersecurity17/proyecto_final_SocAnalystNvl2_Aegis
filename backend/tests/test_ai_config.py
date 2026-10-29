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
