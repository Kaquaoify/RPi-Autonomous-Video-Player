# app/tasks.py
from __future__ import annotations
import threading
import time
from typing import Optional

# Imports locaux (fait au module-level, c’est ok ici)
from .services.settings_store import setting_sync_on_boot, setting_autoplay
from .services import videos
from .services.thumbnails import generate_all as gen_thumbs
from .services.vlc_player import set_media_current, play_current
from .services import rclone

_BOOT_THREAD: Optional[threading.Thread] = None
_BOOT_LOCK = threading.RLock()


def _bootstrap_worker():
    """
    Tâche de démarrage:
      1) Sync rclone si activée
      2) Refresh liste vidéos
      3) Génération miniatures
      4) Prépare/relance la lecture si autoplay
    """
    try:
        # 1) Synchronisation initiale (optionnelle)
        if setting_sync_on_boot():
            print("[bootstrap] sync_on_boot = True → rclone sync (blocking)")
            ok, log_path = rclone.sync_blocking()
            print(f"[bootstrap] rclone sync done. ok={ok} log={log_path}")
        else:
            print("[bootstrap] sync_on_boot = False → skip rclone sync")

        # 2) Rafraîchir la liste des vidéos
        videos.refresh_videos(force=True)
        snap = videos.snapshot()
        print(f"[bootstrap] videos: count={snap['count']} index={snap['index']} current={snap['current_name']}")

        # 3) Générer les miniatures (en ligne droite au démarrage)
        if snap["count"] > 0:
            print("[bootstrap] generating thumbnails…")
            gen_thumbs(parallel=True)
            print("[bootstrap] thumbnails generated.")
        else:
            print("[bootstrap] no videos → skip thumbnails")

        # 4) Autoplay: charger et démarrer la vidéo courante
        if snap["count"] > 0 and setting_autoplay():
            print("[bootstrap] autoplay = True → prepare & play current")
            set_media_current()
            play_current()
        else:
            print("[bootstrap] autoplay disabled or no videos, nothing to play.")

    except Exception as e:
        # Ne jamais planter le process pour un souci de bootstrap
        print(f"[bootstrap] error: {e!r}")


def start_bootstrap(app=None) -> None:
    """
    Lance la tâche de bootstrap une seule fois.
    Idempotent: si déjà en cours → no-op.
    """
    global _BOOT_THREAD
    with _BOOT_LOCK:
        if _BOOT_THREAD and _BOOT_THREAD.is_alive():
            return
        t = threading.Thread(target=_bootstrap_worker, name="bootstrap", daemon=True)
        _BOOT_THREAD = t
        t.start()
