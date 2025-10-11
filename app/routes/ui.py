# app/routes/ui.py
from __future__ import annotations
import threading
from typing import Any, Dict

from flask import Blueprint, render_template

from ..services import videos
from ..services.thumbnails import generate_all as gen_thumbs
from ..services.settings_store import load_settings
from ..services.hls import preview_status
from ..services.rclone import get_default_remote, get_remote_folder_setting

ui_bp = Blueprint("ui", __name__)

# --- Helpers ---------------------------------------------------------------

_thumb_thread: threading.Thread | None = None

def _ensure_thumbnails_background() -> None:
    """Démarre (une fois) la génération des miniatures en tâche de fond."""
    global _thumb_thread
    if _thumb_thread and _thumb_thread.is_alive():
        return
    # Rafraîchit d'abord la liste des vidéos
    videos.refresh_videos(force=False)
    # Lance la génération en arrière-plan (évite de bloquer le premier render)
    t = threading.Thread(target=lambda: gen_thumbs(parallel=True), daemon=True, name="thumbs-gen")
    _thumb_thread = t
    t.start()


# --- Routes pages ----------------------------------------------------------

@ui_bp.route("/")
def index():
    """
    Accueil: mini-explorateur + contrôles.
    En plus de 'initial', on passe 'videos' (liste des noms) pour
    permettre le rendu côté serveur comme dans la 0.8.0.
    """
    videos.refresh_videos(force=False)
    _ensure_thumbnails_background()

    snap = videos.snapshot()              # {count, index, current_name, video_dir}
    settings = load_settings()            # {preview_enabled, autoplay, ...}
    hls = preview_status()                # {enabled, index_url, ...}

    # Liste des vidéos (noms) pour compat legacy
    names = [v["name"] for v in videos.list_videos()]

    ctx = {
        "initial": {
            "videos": snap,
            "settings": settings,
            "hls": hls,
        },
        "videos": names,   # ← AJOUT
    }
    return render_template("index.html", **ctx)


@ui_bp.route("/settings")
def settings_page():
    """
    Page des réglages généraux (autoplay, loop, preview, etc.).
    """
    return render_template("settings.html", settings=load_settings())


@ui_bp.route("/rclone-setup")
def rclone_setup_page():
    """
    Assistant de configuration rclone (choix du remote, dossier, etc.).
    On envoie le remote par défaut et le dossier courant pour préremplir le formulaire.
    """
    ctx = {
        "default_remote": get_default_remote(),
        "remote_folder": get_remote_folder_setting(),
        "settings": load_settings(),
    }
    return render_template("rclone_setup.html", **ctx)
