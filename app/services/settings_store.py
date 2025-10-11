# app/services/settings_store.py
import json
import threading
from pathlib import Path
from typing import Any, Dict

from ..config import SETTINGS_PATH

# Valeurs par défaut (doivent rester en phase avec settings.json)
_DEFAULTS: Dict[str, Any] = {
    "remote_folder": "VideosRPi",
    "preview_enabled": False,
    "autoplay": True,
    "loop_all": True,
    "sync_on_boot": True,
}

_lock = threading.RLock()
_settings_cache: Dict[str, Any] | None = None


def _ensure_file_exists() -> None:
    """Crée le fichier settings s'il n'existe pas, avec les défauts."""
    p = Path(SETTINGS_PATH)
    if not p.exists():
        p.write_text(json.dumps(_DEFAULTS, indent=2), encoding="utf-8")


def _load_from_disk() -> Dict[str, Any]:
    p = Path(SETTINGS_PATH)
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            # En cas de fichier corrompu → on repart sur les defaults
            data = {}
    # Merge defaults → settings manquants complétés
    merged = {**_DEFAULTS, **data}
    # Optionnel: ré-écrit pour normaliser l'ordre/format
    p.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def load_settings() -> Dict[str, Any]:
    """Charge (et met en cache) les settings. Thread-safe."""
    global _settings_cache
    with _lock:
        if _settings_cache is None:
            _ensure_file_exists()
            _settings_cache = _load_from_disk()
        return dict(_settings_cache)  # copie défensive


def save_settings(new_settings: Dict[str, Any]) -> Dict[str, Any]:
    """Écrit les settings (merge avec defaults) et met en cache. Thread-safe."""
    global _settings_cache
    with _lock:
        merged = {**_DEFAULTS, **(new_settings or {})}
        Path(SETTINGS_PATH).write_text(json.dumps(merged, indent=2), encoding="utf-8")
        _settings_cache = dict(merged)
        return dict(_settings_cache)


def get_setting(key: str, default: Any = None) -> Any:
    with _lock:
        s = load_settings()
        return s.get(key, default)


def set_setting(key: str, value: Any) -> Dict[str, Any]:
    with _lock:
        s = load_settings()
        s[key] = value
        return save_settings(s)


# --- Helpers spécifiques utilisés partout dans l'app ---

def get_remote_folder() -> str:
    return str(get_setting("remote_folder", _DEFAULTS["remote_folder"]))

def set_remote_folder(folder: str) -> Dict[str, Any]:
    return set_setting("remote_folder", str(folder))

def is_preview_enabled() -> bool:
    return bool(get_setting("preview_enabled", _DEFAULTS["preview_enabled"]))

def set_preview_enabled(enabled: bool) -> Dict[str, Any]:
    return set_setting("preview_enabled", bool(enabled))

def setting_autoplay() -> bool:
    return bool(get_setting("autoplay", _DEFAULTS["autoplay"]))

def set_autoplay(enabled: bool) -> Dict[str, Any]:
    return set_setting("autoplay", bool(enabled))

def setting_loop_all() -> bool:
    return bool(get_setting("loop_all", _DEFAULTS["loop_all"]))

def set_loop_all(enabled: bool) -> Dict[str, Any]:
    return set_setting("loop_all", bool(enabled))

def setting_sync_on_boot() -> bool:
    return bool(get_setting("sync_on_boot", _DEFAULTS["sync_on_boot"]))

def set_sync_on_boot(enabled: bool) -> Dict[str, Any]:
    return set_setting("sync_on_boot", bool(enabled))
