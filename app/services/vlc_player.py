# app/services/vlc_player.py
from __future__ import annotations
import os
import threading
from typing import Dict, Optional

import vlc  # python-vlc

from ..config import VLC_AUDIO_VOLUME_STEP, VLC_START_AT
from . import videos
from .hls import vlc_sout_args_for_preview
from .settings_store import setting_loop_all, setting_autoplay

_LOCK = threading.RLock()

_instance: Optional[vlc.Instance] = None
_player: Optional[vlc.MediaPlayer] = None
_events_bound = False

# Valeurs mémorisées pour l'UI
_last_loaded_name: Optional[str] = None
_muted: bool = False


def _create_instance() -> vlc.Instance:
    """
    Crée une instance VLC adaptée au RPi (reste minimaliste pour compatibilité).
    Tu pourras injecter des options vidéo spécifiques si besoin (vout/aout).
    """
    # Options utiles:
    #  - --no-video-title-show : évite l'overlay du nom de fichier
    #  - --quiet ou --intf dummy : moins de bruit
    #  - --avcodec-hw=any : laisser VLC choisir l'accélération
    #  - --no-xlib : si pas d'X11 (server/headless) et on s'appuie sur DRM/KMS par défaut
    opts = [
        "--no-video-title-show",
        "--quiet",
        "--intf", "dummy",
        "--no-xlib",
    ]
    return vlc.Instance(" ".join(opts))


def _create_player(inst: vlc.Instance) -> vlc.MediaPlayer:
    p = inst.media_player_new()
    # Volume “raisonnable”
    try:
        p.audio_set_volume(100)
    except Exception:
        pass
    return p


def ensure_ready() -> None:
    """S'assure que l'instance et le player VLC existent, et que les events sont bindés."""
    global _instance, _player, _events_bound
    with _LOCK:
        if _instance is None:
            _instance = _create_instance()
        if _player is None:
            _player = _create_player(_instance)
            _bind_events_unlocked()
        elif not _events_bound:
            _bind_events_unlocked()


def _bind_events_unlocked() -> None:
    """Bind les événements du MediaPlayer (à appeler sous _LOCK)."""
    global _events_bound
    if _player is None:
        return
    try:
        em = _player.event_manager()
        em.event_attach(vlc.EventType.MediaPlayerEndReached, _on_end_reached)
        _events_bound = True
    except Exception:
        # Ne casse pas l'app si les events ne peuvent pas être attachés
        _events_bound = False


# -----------------
# Gestion des médias
# -----------------

def _apply_media_options(m: vlc.Media) -> None:
    """Ajoute dynamiquement les options sout (HLS preview) si activé."""
    try:
        for opt in vlc_sout_args_for_preview():
            # opt doit commencer par ":" (c'est déjà le cas dans hls.vlc_sout_args_for_preview)
            m.add_option(opt)
    except Exception:
        pass

    # Décalage de lecture au démarrage (évite frames noires sur certains encodages)
    if VLC_START_AT and VLC_START_AT > 0:
        try:
            m.add_option(f":start-time={int(VLC_START_AT)}")
        except Exception:
            pass


def set_media_by_index(index: int) -> Dict[str, object]:
    """
    Charge la vidéo à l'index demandé (borné), prépare les options (HLS), et ne la démarre pas.
    Retourne un petit statut (name/index/count).
    """
    ensure_ready()
    with _LOCK:
        count_before = videos.get_count()
        idx = videos.set_index(index)
        path = videos.get_video_path(idx)
        name = videos.get_video_name(idx)

        if path is None:
            return {"ok": False, "error": "no video at index", "index": idx, "count": count_before}

        media = _instance.media_new(path)  # type: ignore
        _apply_media_options(media)
        _player.set_media(media)          # type: ignore

        global _last_loaded_name
        _last_loaded_name = name

        return {"ok": True, "index": idx, "count": count_before, "name": name, "path": path}


def set_media_current() -> Dict[str, object]:
    """Recharge la vidéo de l’index courant (utile après toggle preview)."""
    ensure_ready()
    with _LOCK:
        idx = videos.get_current_index()
        return set_media_by_index(idx)


def play_current() -> Dict[str, object]:
    """Démarre la lecture de la vidéo actuellement chargée. Si rien n'est chargé, tente de charger l’index courant."""
    ensure_ready()
    with _LOCK:
        if _player.get_media() is None:  # type: ignore
            set_media_current()
        _player.play()  # type: ignore
        return status()


def pause() -> Dict[str, object]:
    ensure_ready()
    with _LOCK:
        _player.pause()  # type: ignore
        return status()


def stop() -> Dict[str, object]:
    ensure_ready()
    with _LOCK:
        _player.stop()  # type: ignore
        return status()


def next_track() -> Dict[str, object]:
    """Passe à la vidéo suivante (en respectant loop_all), charge et joue si autoplay."""
    ensure_ready()
    with _LOCK:
        idx = videos.select_next(loop_all=setting_loop_all())
        rep = set_media_by_index(idx)
        if setting_autoplay():
            _player.play()  # type: ignore
        return rep


def prev_track() -> Dict[str, object]:
    ensure_ready()
    with _LOCK:
        idx = videos.select_prev(loop_all=setting_loop_all())
        rep = set_media_by_index(idx)
        if setting_autoplay():
            _player.play()  # type: ignore
        return rep


# -------------
# Audio & volume
# -------------

def volume_up() -> Dict[str, object]:
    ensure_ready()
    with _LOCK:
        cur = _player.audio_get_volume()  # type: ignore
        _player.audio_set_volume(min(200, (cur or 0) + VLC_AUDIO_VOLUME_STEP))  # type: ignore
        return status()

def volume_down() -> Dict[str, object]:
    ensure_ready()
    with _LOCK:
        cur = _player.audio_get_volume()  # type: ignore
        _player.audio_set_volume(max(0, (cur or 0) - VLC_AUDIO_VOLUME_STEP))  # type: ignore
        return status()

def toggle_mute() -> Dict[str, object]:
    ensure_ready()
    global _muted
    with _LOCK:
        _muted = not _muted
        _player.audio_set_mute(_muted)  # type: ignore
        return status()


# -------------
# Statut & état
# -------------

_STATE_MAP = {
    vlc.State.NothingSpecial: "idle",
    vlc.State.Opening: "opening",
    vlc.State.Buffering: "buffering",
    vlc.State.Playing: "playing",
    vlc.State.Paused: "paused",
    vlc.State.Stopped: "stopped",
    vlc.State.Ended: "ended",
    vlc.State.Error: "error",
}

def status() -> Dict[str, object]:
    ensure_ready()
    with _LOCK:
        st_vlc = _player.get_state() if _player else vlc.State.NothingSpecial  # type: ignore
        st = _STATE_MAP.get(st_vlc, "unknown")
        vol = _player.audio_get_volume() if _player else 0  # type: ignore

        snap = videos.snapshot()
        snap.update({
            "state": st,
            "volume": vol,
            "muted": _muted,
            "loaded_name": _last_loaded_name,
        })
        return snap


# -------------------------
# Gestion d'événement VLC
# -------------------------

def _on_end_reached(event) -> None:  # signature imposée par python-vlc
    """
    Appelé quand la lecture se termine. Selon loop_all → on avance.
    On rejoue si autoplay est actif.
    """
    try:
        if setting_loop_all():
            rep = next_track()
            if setting_autoplay() and rep.get("ok"):
                # player.play() déjà fait par next_track si autoplay True
                return
        # Si pas de loop_all: laisse l'état “ended”, pas d’action.
    except Exception:
        # Ne jamais planter sur un handler d’événement
        pass
