import json
import os
import threading
import logging
from typing import Any, Dict

try:
    from flask import current_app
    _svc_logger = current_app.logger
except Exception:
    _svc_logger = logging.getLogger('rpi_avp')


class SettingsService:
    """
    Simple JSON-backed settings store.

    File format: settings.json at project root, with keys used by the app:
      - remote_name: str (optional)
      - remote_folder: str (default 'VideosRPi')
      - preview_enabled: bool
      - autoplay: bool
      - loop_all: bool
      - sync_on_boot: bool
    """

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self._lock = threading.RLock()

    def load(self) -> Dict[str, Any]:
        with self._lock:
            try:
                if not os.path.isfile(self.file_path):
                    return {}
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
            except Exception as e:
                _svc_logger.warning("settings load failed: %s", e)
                return {}

    def save(self, data: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.file_path) or ".", exist_ok=True)
        with self._lock:
            try:
                tmp = self.file_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data or {}, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.file_path)
            except Exception as e:
                _svc_logger.error("settings save failed: %s", e)
                raise

    def get(self, key: str, default: Any = None) -> Any:
        cfg = self.load()
        return cfg.get(key, default)

    def set(self, **kwargs: Any) -> Dict[str, Any]:
        with self._lock:
            cfg = self.load()
            cfg.update({k: v for k, v in kwargs.items() if v is not None})
            self.save(cfg)
            return cfg

