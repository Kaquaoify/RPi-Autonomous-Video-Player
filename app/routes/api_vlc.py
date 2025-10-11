# app/routes/api_vlc.py
from __future__ import annotations
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from ..services import videos
from ..services.vlc_player import (
    set_media_by_index,
    set_media_current,
    play_current,
    pause,
    stop,
    next_track,
    prev_track,
    volume_up,
    volume_down,
    toggle_mute,
    status as vlc_status,
)
from ..services.settings_store import setting_autoplay

api_vlc_bp = Blueprint("api_vlc", __name__)

# ------------
# Helpers JSON
# ------------

def _json_ok(data: Dict[str, Any] | None = None):
    data = data or {}
    data.setdefault("ok", True)
    return jsonify(data)

def _json_err(msg: str, **extra):
    d = {"ok": False, "error": msg}
    d.update(extra)
    return jsonify(d), 400


# -------------
# Endpoints API
# -------------

@api_vlc_bp.post("/play-video")
def api_play_video():
    """
    Charge (par index ou nom) puis joue la vidéo.
    Body JSON:
      - {"index": 3}  OU  {"name": "file.mp4"}
    """
    payload = request.get_json(silent=True) or {}
    if "index" in payload:
        idx = int(payload["index"])
        rep = set_media_by_index(idx)
    elif "name" in payload:
        name = str(payload["name"])
        idx = videos.set_index_by_name(name)
        if idx < 0:
            return _json_err("file not found", name=name)
        rep = set_media_by_index(idx)
    else:
        # Si rien fourni: on rejoue l'actuelle (utile après reboot)
        rep = set_media_current()

    if not rep.get("ok"):
        return _json_err("unable to set media", **rep)

    # Joue immédiatement
    play_current()
    return _json_ok(vlc_status())


@api_vlc_bp.post("/select-index")
def api_select_index():
    """
    Charge une vidéo par index sans démarrer la lecture.
    Body: {"index": int}
    """
    payload = request.get_json(silent=True) or {}
    if "index" not in payload:
        return _json_err("index missing")
    idx = int(payload["index"])
    rep = set_media_by_index(idx)
    if not rep.get("ok"):
        return _json_err("unable to set media", **rep)
    return _json_ok(vlc_status())


@api_vlc_bp.post("/refresh")
def api_refresh_videos():
    """
    Rafraîchit la liste des vidéos (scan du dossier).
    Si autoplay est actif et qu'aucun média n'est chargé, tente de jouer la courante.
    """
    res = videos.refresh_videos(force=True)
    # Si on a des vidéos et autoplay activé, assure un média prêt
    if res.get("count", 0) > 0 and setting_autoplay():
        set_media_current()
    return _json_ok({"videos": videos.snapshot()})


@api_vlc_bp.get("/videos")
def api_list_videos():
    """Liste les vidéos {name, path}."""
    return jsonify({"ok": True, "items": videos.list_videos()})


@api_vlc_bp.post("/play")
def api_play():
    return _json_ok(play_current())


@api_vlc_bp.post("/pause")
def api_pause():
    return _json_ok(pause())


@api_vlc_bp.post("/stop")
def api_stop():
    return _json_ok(stop())


@api_vlc_bp.post("/next")
def api_next():
    return _json_ok(next_track())


@api_vlc_bp.post("/prev")
def api_prev():
    return _json_ok(prev_track())


@api_vlc_bp.post("/vol_up")
def api_vol_up():
    return _json_ok(volume_up())


@api_vlc_bp.post("/vol_down")
def api_vol_down():
    return _json_ok(volume_down())


@api_vlc_bp.post("/mute")
def api_mute():
    return _json_ok(toggle_mute())
