# app/routes/api_rclone.py
from __future__ import annotations
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from ..services import rclone
from ..services.videos import refresh_videos
from ..services.thumbnails import generate_all as gen_thumbs

api_rclone_bp = Blueprint("api_rclone", __name__)

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


# ---------------------
# Infos / Disponibilité
# ---------------------

@api_rclone_bp.get("/version")
def api_version():
    return jsonify(rclone.rclone_version())

@api_rclone_bp.get("/remotes")
def api_list_remotes():
    return jsonify({"ok": True, "items": rclone.list_remotes(), "default": rclone.get_default_remote()})


# ---------------
# Config / Remote
# ---------------

@api_rclone_bp.post("/config/create")
def api_config_create():
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    provider = payload.get("provider")
    if not name or not provider:
        return _json_err("missing 'name' or 'provider'")
    kwargs = {k: v for k, v in payload.items() if k not in ("name", "provider")}
    return jsonify(rclone.config_create(str(name), str(provider), **kwargs))

@api_rclone_bp.post("/config/update")
def api_config_update():
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    if not name:
        return _json_err("missing 'name'")
    kwargs = {k: v for k, v in payload.items() if k != "name"}
    return jsonify(rclone.config_update(str(name), **kwargs))

@api_rclone_bp.post("/config/delete")
def api_config_delete():
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    if not name:
        return _json_err("missing 'name'")
    return jsonify(rclone.config_delete(str(name)))


# --------------
# Sync & statut
# --------------

@api_rclone_bp.post("/sync")
def api_sync():
    """
    Lance une synchronisation asynchrone:
      Body JSON (optionnel): {"remote": "gdrive", "folder": "VideosRPi", "dest_dir": "/path"}
    À la fin de la sync (dans le thread), le front peut:
      - lire /api/rclone/log
      - appeler /control/refresh pour rescanner les vidéos
      - éventuellement relancer la génération de miniatures
    """
    payload = request.get_json(silent=True) or {}
    remote = payload.get("remote")
    folder = payload.get("folder")
    dest_dir = payload.get("dest_dir")
    rep = rclone.sync_async(remote=remote, folder=folder, dest_dir=dest_dir)
    return jsonify(rep)

@api_rclone_bp.get("/sync_status")
def api_sync_status():
    return jsonify(rclone.sync_status())

@api_rclone_bp.get("/log")
def api_log_tail():
    # optionnel: ?lines=400
    try:
        lines = int(request.args.get("lines", "200"))
    except Exception:
        lines = 200
    return jsonify({"ok": True, "log": rclone.log_tail(lines=lines)})


# ---------------------
# Paramètre de dossier
# ---------------------

@api_rclone_bp.get("/remote_folder")
def api_get_remote_folder():
    return jsonify({"ok": True, "remote_folder": rclone.get_remote_folder_setting()})

@api_rclone_bp.post("/remote_folder")
def api_set_remote_folder():
    payload = request.get_json(silent=True) or {}
    folder = payload.get("remote_folder")
    if not folder:
        return _json_err("missing 'remote_folder'")
    return jsonify(rclone.set_remote_folder_setting(str(folder)))
