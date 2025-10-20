import os
import shutil
import logging
from typing import Tuple

try:
    from flask import current_app
    _svc_logger = current_app.logger
except Exception:
    _svc_logger = logging.getLogger('rpi_avp')


class PreviewService:
    """
    Controls the HLS preview flag and helpers to manage the HLS directory.
    """

    def __init__(self, settings_service, hls_dir: str, hls_index: str) -> None:
        self._settings = settings_service
        self.hls_dir = hls_dir
        self.hls_index = hls_index

    def is_enabled(self) -> bool:
        return bool(self._settings.get("preview_enabled", False))

    def set_enabled(self, value: bool) -> None:
        self._settings.set(preview_enabled=bool(value))

    def status(self) -> dict:
        return {"enabled": self.is_enabled(), "url": "/hls/index.m3u8"}

    def clear_hls_dir(self) -> None:
        os.makedirs(self.hls_dir, exist_ok=True)
        # Remove segments but keep directory
        try:
            for name in os.listdir(self.hls_dir):
                path = os.path.join(self.hls_dir, name)
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                    else:
                        shutil.rmtree(path)
                except Exception:
                    pass
        except FileNotFoundError:
            os.makedirs(self.hls_dir, exist_ok=True)

    def hls_paths(self) -> Tuple[str, str]:
        return self.hls_dir, self.hls_index

