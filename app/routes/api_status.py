# app/routes/api_status.py
from __future__ import annotations
from flask import Blueprint, jsonify

from ..services import videos
from ..services.vlc_player import status as vlc_status

api_status_bp = Blueprint("api_status", __name__)

@api_status_bp.get("/status")
def status_full():
    """
    Statut complet : état VLC + snapshot vidéos.
    Utilisé par l'UI pour rafraîchir les infos.
    """
    return jsonify(vlc_status())

@api_status_bp.get("/status_min")
def status_min():
    """
    Statut ultra-léger pour heartbeat UI.
    """
    snap = videos.snapshot()
    return jsonify({
        "count": snap["count"],
        "index": snap["index"],
        "current_name": snap["current_name"],
    })

@api_status_bp.get("/health")
def health():
    """
    Simple healthcheck pour systemd/nginx.
    """
    return jsonify({"ok": True})
