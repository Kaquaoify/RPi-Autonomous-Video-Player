# app/config.py
import os
from pathlib import Path

# --- Dossiers & chemins de base ---
USER_HOME = str(Path.home())

# Répertoire principal des vidéos (identique à l’actuel)
VIDEO_DIR = os.path.join(USER_HOME, "Videos", "RPi-Autonomous-Video-Player")
THUMB_DIR = os.path.join(VIDEO_DIR, "thumbnails")

# HLS (aperçu/monitoring)
HLS_DIR   = os.path.join(USER_HOME, ".local", "share", "rpi-avp", "hls")
HLS_INDEX = "index.m3u8"

# Racine du package app/ (là où vivent templates/ et static/)
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(APP_ROOT, "templates")
STATIC_DIR   = os.path.join(APP_ROOT, "static")

# Settings persistants
SETTINGS_PATH = os.path.join(os.path.dirname(APP_ROOT), "settings.json")

# Logs rclone
RCLONE_LOG_DIR = os.path.join(USER_HOME, ".local", "share", "rpi-avp", "logs")
RCLONE_LOG     = os.path.join(RCLONE_LOG_DIR, "rclone_sync.log")

# --- Paramètres VLC (conservés) ---
VLC_AUDIO_VOLUME_STEP = 10
# Décalage de départ (en secondes) pour éviter certains "black frames" au tout début
VLC_START_AT = 5

def ensure_dirs():
    """Crée les dossiers de travail si nécessaires (appelée au démarrage)."""
    for d in (VIDEO_DIR, THUMB_DIR, HLS_DIR, RCLONE_LOG_DIR):
        os.makedirs(d, exist_ok=True)
