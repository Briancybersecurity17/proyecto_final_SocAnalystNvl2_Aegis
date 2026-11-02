"""Persistencia de análisis en disco (spec arquitectónico A).

Cada análisis vive en `ANALYSES_DIR/{analysis_id}/` con tres piezas:

  archivo.xlsx   el Excel original tal cual se subió (fuente de verdad)
  summary.json   status + error + summary + chat_history + rag (lo calculado)
  report.json    el informe técnico, generado UNA sola vez

Al arrancar, `main.rehidratar_desde_disco()` vuelve a leer estos archivos y
rellena el `analysis_store` en memoria: reiniciar el backend ya no pierde los
análisis. La retención (borrado automático) la aplica `main` con el campo
`retention_days` de ai_config; este módulo solo sabe borrar por antigüedad.

Todos los helpers leen `ANALYSES_DIR` en el momento de la llamada (no capturan
el valor al importar): los tests de suite redirigen esa global al igual que
hacen con el índice RAG y la config de IA.
"""

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Datos de análisis persistidos en backend/data/analyses/
ANALYSES_DIR = Path(__file__).resolve().parent / "data" / "analyses"

_ARCHIVO = "archivo.xlsx"
_ESTADO = "summary.json"
_INFORME = "report.json"


def dir_analisis(analysis_id: str) -> Path:
    """Carpeta del análisis (sin crearla)."""
    return ANALYSES_DIR / analysis_id


def guardar_excel(analysis_id: str, content: bytes) -> Path:
    """Guarda el Excel original. Crea la carpeta del análisis."""
    destino = dir_analisis(analysis_id)
    destino.mkdir(parents=True, exist_ok=True)
    (destino / _ARCHIVO).write_bytes(content)
    return destino / _ARCHIVO


def leer_excel(analysis_id: str) -> Optional[bytes]:
    """Bytes del Excel original o None si no existe."""
    ruta = dir_analisis(analysis_id) / _ARCHIVO
    if not ruta.is_file():
        return None
    return ruta.read_bytes()


def guardar_estado(analysis_id: str, entry: Dict[str, Any]) -> None:
    """Serializa lo calculado del análisis (summary/chat/rag/status/error).

    El dataframe NO entra: se reconstruye del Excel al rehidratar. Si la
    carpeta no existe (análisis sintético en memoria, nunca subido) no se
    persiste nada — la memoria sigue siendo su única fuente.
    """
    destino = dir_analisis(analysis_id)
    if not destino.is_dir():
        return
    payload = {
        "status": entry.get("status"),
        "error": entry.get("error"),
        "summary": entry.get("summary"),
        "chat_history": entry.get("chat_history") or [],
        "rag": entry.get("rag"),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (destino / _ESTADO).write_text(
        json.dumps(payload, ensure_ascii=False, default=str, indent=2),
        encoding="utf-8",
    )


def cargar_estado(analysis_id: str) -> Optional[Dict[str, Any]]:
    """summary.json leído del disco, o None si no existe/corrupto."""
    ruta = dir_analisis(analysis_id) / _ESTADO
    if not ruta.is_file():
        return None
    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def guardar_informe(analysis_id: str, narrative: Dict[str, Any]) -> None:
    """Escribe report.json (informe generado: se genera UNA vez)."""
    destino = dir_analisis(analysis_id)
    destino.mkdir(parents=True, exist_ok=True)
    (destino / _INFORME).write_text(
        json.dumps(narrative, ensure_ascii=False, default=str, indent=2),
        encoding="utf-8",
    )


def cargar_informe(analysis_id: str) -> Optional[Dict[str, Any]]:
    """report.json leído del disco, o None si no existe/corrupto."""
    ruta = dir_analisis(analysis_id) / _INFORME
    if not ruta.is_file():
        return None
    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def listar_ids() -> List[str]:
    """Ids con carpeta en disco (orden alfabético; el orden lo da el caller)."""
    if not ANALYSES_DIR.is_dir():
        return []
    return sorted(p.name for p in ANALYSES_DIR.iterdir() if p.is_dir())


def borrar(analysis_id: str) -> bool:
    """Elimina la carpeta completa del análisis. True si había algo que borrar."""
    destino = dir_analisis(analysis_id)
    if not destino.is_dir():
        return False
    shutil.rmtree(destino, ignore_errors=True)
    return True


def aplicar_retencion(retention_days: int) -> List[str]:
    """Borra análisis cuya carpeta no se ha tocado en `retention_days` días.

    `retention_days = 0` (o negativo) significa «nunca borrar». Devuelve los
    ids borrados para poder limpiar también su entrada en memoria y su índice
    RAG. Se mide por mtime de la carpeta: cada escritura (summary, informe,
    chat) lo refresca, así que un análisis en uso nunca caduca.
    """
    if retention_days <= 0:
        return []
    if not ANALYSES_DIR.is_dir():
        return []
    limite = time.time() - retention_days * 24 * 3600
    borrados: List[str] = []
    for carpeta in ANALYSES_DIR.iterdir():
        if not carpeta.is_dir():
            continue
        try:
            mtime = carpeta.stat().st_mtime
        except OSError:
            continue
        if mtime < limite:
            shutil.rmtree(carpeta, ignore_errors=True)
            borrados.append(carpeta.name)
    return borrados
