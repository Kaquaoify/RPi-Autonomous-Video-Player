# app/routes/files.py
from __future__ import annotations
import os
from flask import Blueprint, abort, send_from_directory

from ..config import THUMB_DIR, HLS_DIR

files_bp = Blueprint("files", __name__)

def _safe_path(root: str, filename: str) -> str:
    # Empêche toute traversée de répertoire.
    # On refuse les chemins absolus et les '..'
    if not filename or filename.startswith("/") or ".." in filename:
        abort(404)
    full = os.path.join(root, filename)
    # Normalisation + vérif qu'on reste bien sous root
    full = os.path.realpath(full)
    root_real = os.path.realpath(root)
    if not full.startswith(root_real + os.sep) and full != root_real:
        abort(404)
    return full

@files_bp.route("/thumbnails/<path:filename>")
def serve_thumbnail(filename: str):
    _safe_path(THUMB_DIR, filename)  # validation
    # Cache léger: 1 heure
    resp = send_from_directory(THUMB_DIR, filename, as_attachment=False, conditional=True)
    resp.cache_control.public = True
    resp.cache_control.max_age = 3600
    return resp

@files_bp.route("/hls/<path:filename>")
def serve_hls(filename: str):
    _safe_path(HLS_DIR, filename)  # validation
    # Pour HLS, on évite le cache agressif (segments tournants)
    resp = send_from_directory(HLS_DIR, filename, as_attachment=False, conditional=True)
    resp.cache_control.no_cache = True
    resp.cache_control.must_revalidate = True
    return resp
