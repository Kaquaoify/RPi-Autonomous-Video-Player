# app/routes/api_preview.py
from __future__ import annotations
from flask import Blueprint, jsonify

from ..services.hls import preview_status, enable_preview, disable_preview
from ..services.vlc_player import set_media_current, play_current
from ..services.settings_store import setting_autoplay

api_preview_bp = Blueprint("api_preview", __name__)

@api_preview_bp.get("/status")
def api_preview_status():
    return jsonify({"ok": True, "preview": preview_status()})

@api_preview_bp.post("/enable")
def api_preview_enable():
    st = enable_preview()
    # On recharge le média courant pour que les options HLS s'appliquent
    set_media_current()
    if setting_autoplay():
        play_current()
    return jsonify({"ok": True, "preview": st})

@api_preview_bp.post("/disable")
def api_preview_disable():
    st = disable_preview()
    # On recharge le média courant pour enlever les options HLS
    set_media_current()
    if setting_autoplay():
        play_current()
    return jsonify({"ok": True, "preview": st})
