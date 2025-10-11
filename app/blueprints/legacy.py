from ..services import rclone as rclone_svc, preview as preview_svc, player as player_svc, library as library_svc, settings as settings_svc, scheduler as scheduler_svc

# app/web.py
from flask import Blueprint, current_app, render_template, request, jsonify, send_from_directory
import os
import threading
import time
import subprocess, shutil  # (shlex supprimé : non utilisé)

# VLC requis côté Python
try:
    import vlc
except Exception as exc:
    raise RuntimeError("python-vlc requis. Installez : sudo apt install python3-vlc") from exc

from ..utils import generate_thumbnails, refresh_videos_list

bp = Blueprint('legacy', __name__)
# ==============================
# Config (chemins & VLC)
# ==============================
USER_HOME = os.path.expanduser("~")
VIDEO_DIR = os.path.join(USER_HOME, "Videos", "RPi-Autonomous-Video-Player")
THUMB_DIR = os.path.join(VIDEO_DIR, "thumbnails")
VLC_AUDIO_VOLUME_STEP = 10
VLC_START_AT = 5

# --- Aperçu HLS (flux web) ---
HLS_DIR = os.path.join(USER_HOME, ".local", "share", "rpi-avp", "hls")
HLS_INDEX = os.path.join(HLS_DIR, "index.m3u8")
os.makedirs(HLS_DIR, exist_ok=True)


# ==============================
# État global (liste vidéos, VLC, miniatures)
# ==============================
videos_lock = threading.RLock()  # RLock pour éviter auto-deadlock
videos = refresh_videos_list(VIDEO_DIR)
video_index = 0

# Snapshots rapides (pour /status sans bloquer)
_snapshot_lock = threading.Lock()
_snapshot_videos_count = len(videos)
_snapshot_current = videos[0] if videos else None

_thumb_thread_started = False
_thumb_thread_lock = threading.Lock()

# VLC : init paresseuse (ne bloque pas Flask)
_instance = None
_player = None
_last_vlc_error = None
_vlc_init_lock = threading.Lock()

# Lecture/loop
_end_event_attached = False  # évite de ré-attacher l'event de fin



# ==============================
# VLC : choix d’options
# ==============================
# moved: _vlc_opts_base
