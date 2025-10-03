# app/web.py
from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import threading

# try to import vlc; if not installed, raise helpful error
try:
    import vlc
except Exception as exc:
    raise RuntimeError("python-vlc is required. Install with: sudo apt install python3-vlc") from exc

from utils import generate_thumbnails, refresh_videos_list

app = Flask(__name__, template_folder="templates", static_folder="static")

# ==============================
# Config
# ==============================
USER_HOME = os.path.expanduser("~")
VIDEO_DIR = os.path.join(USER_HOME, "Videos", "RPi-Autonomous-Video-Player")
THUMB_DIR = os.path.join(VIDEO_DIR, "thumbnails")
VLC_AUDIO_VOLUME_STEP = 10
VLC_START_AT = 5

# ==============================
# Global state
# ==============================
videos_lock = threading.Lock()
videos = refresh_videos_list(VIDEO_DIR)
video_index = 0

_thumb_thread_started = False
_thumb_thread_lock = threading.Lock()

# VLC (lazy init pour éviter de bloquer Flask si VLC échoue)
_instance = None
_player = None
_last_vlc_error = None
_vlc_init_lock = threading.Lock()

def _vlc_opts_base():
    # Audio sur ALSA pour éviter PulseAudio
    return [
        "--no-video-title-show",
        "--fullscreen",
        "--aout=alsa",
        "--alsa-audio-device=default",
    ]

def _vlc_opts_candidates():
    # Choix de vout selon environnement (headless -> kmsdrm / fb)
    headless = not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if headless:
        # kmsdrm (KMS/DRM) puis fallback framebuffer
        return [
            ["--vout=kmsdrm"],
            ["--vout=fb"],
            [],  # laisse VLC choisir si les 2 ci-dessus échouent
        ]
    else:
        # En session graphique, laisse VLC choisir (gl/gles2/xcb)
        return [
            [],  # no explicit vout
            ["--vout=opengl"],
            ["--vout=xcb"],
        ]

def ensure_vlc_ready() -> bool:
    """
    Initialise VLC si nécessaire. N'échoue pas bruyamment :
    - Renvoie False si impossible; Flask continue de tourner.
    - Stocke le dernier message d'erreur dans _last_vlc_error.
    """
    global _instance, _player, _last_vlc_error
    if _player is not None:
        return True

    with _vlc_init_lock:
        if _player is not None:
            return True

        base = _vlc_opts_base()
        for extra in _vlc_opts_candidates():
            opts = base + extra
            try:
                app.logger.info("VLC init try: %s", " ".join(opts) or "(default)")
                inst = vlc.Instance(*opts)
                ply = inst.media_player_new()
                try:
                    ply.audio_set_volume(80)
                except Exception:
                    pass
                _instance = inst
                _player = ply
                app.logger.info("VLC init success with opts: %s", " ".join(opts) or "(default)")
                _last_vlc_error = None
                return True
            except Exception as e:
                _last_vlc_error = f"{type(e).__name__}: {e}"
                app.logger.warning("VLC init failed with opts %s -> %s", " ".join(opts) or "(default)", _last_vlc_error)

        app.logger.error("VLC could not be initialized with any option set.")
        return False

# ==============================
# Helpers
# ==============================
def set_media_by_index(idx: int) -> bool:
    """Charge la vidéo d'index idx dans le MediaPlayer (safe)."""
    global _player, videos, VIDEO_DIR
    if not ensure_vlc_ready():
        return False
    with videos_lock:
        if not videos or idx < 0 or idx >= len(videos):
            return False
        name = videos[idx]
    path = os.path.join(VIDEO_DIR, name)
    media = _instance.media_new(path)
    _player.set_media(media)
    return True

def safe_refresh_videos():
    """Recharge la liste des vidéos (thread-safe)."""
    global videos
    with videos_lock:
        videos = refresh_videos_list(VIDEO_DIR)

def ensure_thumbnails_background():
    """Lance la génération des miniatures en arrière-plan (une seule fois)."""
    global _thumb_thread_started
    with _thumb_thread_lock:
        if _thumb_thread_started:
            return
        _thumb_thread_started = True
    threading.Thread(
        target=generate_thumbnails, args=(VIDEO_DIR, THUMB_DIR, VLC_START_AT), daemon=True
    ).start()

def get_vlc_state_str():
    if _player is None:
        return "uninitialized"
    try:
        st = _player.get_state()
    except Exception:
        return "error"
    mapping = {
        vlc.State.NothingSpecial: "idle",
        vlc.State.Opening: "opening",
        vlc.State.Buffering: "buffering",
        vlc.State.Playing: "playing",
        vlc.State.Paused: "paused",
        vlc.State.Stopped: "stopped",
        vlc.State.Ended: "ended",
        vlc.State.Error: "error",
    }
    return mapping.get(st, str(st))

def get_current_video_name():
    with videos_lock:
        if not videos or not (0 <= video_index < len(videos)):
            return None
        return videos[video_index]

def ensure_media_loaded():
    """Si aucun média n'est chargé, charge la vidéo courante (si dispo)."""
    if not ensure_vlc_ready():
        return False
    if _player.get_media() is None:
        with videos_lock:
            if videos:
                return set_media_by_index(max(0, min(video_index, len(videos) - 1)))
    return True

# ==============================
# Routes
# ==============================
@app.route("/")
def index():
    safe_refresh_videos()
    ensure_thumbnails_background()
    with videos_lock:
        vlist = list(videos)
    return render_template("index.html", videos=vlist)

@app.route("/settings")
def settings_page():
    return render_template("settings.html")

@app.route("/favicon.ico")
def favicon():
    return ("", 204)

@app.route("/thumbnails/<filename>")
def thumbnails(filename):
    if not os.path.isdir(THUMB_DIR):
        return ("", 404)
    return send_from_directory(THUMB_DIR, filename)

@app.route("/control/<action>", methods=["POST"])
def control(action):
    global video_index
    action = action.lower()
    with videos_lock:
        count = len(videos)

    if action == "play":
        if not ensure_media_loaded():
            return jsonify(status="error", message=f"VLC not ready: {_last_vlc_error}"), 500
        _player.play()
    elif action == "pause":
        if not ensure_vlc_ready():
            return jsonify(status="error", message="VLC not ready"), 500
        _player.pause()
    elif action == "next":
        with videos_lock:
            if count == 0:
                return jsonify(status="error", message="No videos"), 400
            video_index = (video_index + 1) % count
        if not set_media_by_index(video_index):
            return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500
        _player.stop()
        _player.play()
    elif action == "prev":
        with videos_lock:
            if count == 0:
                return jsonify(status="error", message="No videos"), 400
            video_index = (video_index - 1) % count
        if not set_media_by_index(video_index):
            return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500
        _player.stop()
        _player.play()
    elif action == "volup":
        if not ensure_vlc_ready():
            return jsonify(status="error", message="VLC not ready"), 500
        try:
            vol = int(_player.audio_get_volume() or 0)
            _player.audio_set_volume(min(vol + VLC_AUDIO_VOLUME_STEP, 100))
        except Exception:
            pass
    elif action == "voldown":
        if not ensure_vlc_ready():
            return jsonify(status="error", message="VLC not ready"), 500
        try:
            vol = int(_player.audio_get_volume() or 0)
            _player.audio_set_volume(max(vol - VLC_AUDIO_VOLUME_STEP, 0))
        except Exception:
            pass
    else:
        return jsonify(status="error", message="Unknown action"), 400

    return jsonify(status="ok", action=action)

@app.route("/play-video", methods=["POST"])
def play_video():
    global video_index, videos
    data = request.get_json() or {}
    video_name = data.get("video")
    app.logger.info("POST /play-video %s", video_name)

    if not video_name:
        return jsonify(status="error", message="No video specified"), 400

    safe_refresh_videos()
    with videos_lock:
        if video_name not in videos:
            app.logger.warning("Video not found: %s", video_name)
            return jsonify(status="error", message="Video not found"), 404
        video_index = videos.index(video_name)

    if not set_media_by_index(video_index):
        return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500

    _player.stop()
    _player.play()
    app.logger.info("Now playing index=%d name=%s", video_index, video_name)
    return jsonify(status="playing", video=video_name)

@app.route("/status")
def status():
    payload = {
        "running": True,
        "videos": None,
        "volume": None,
        "state": None,
        "current": None,
        "vlc_ready": (_player is not None),
        "vlc_error": _last_vlc_error,
    }

    # Nombre de vidéos
    try:
        with videos_lock:
            payload["videos"] = len(videos)
            payload["current"] = get_current_video_name()
    except Exception as e:
        app.logger.exception("status: videos/count failed: %s", e)
        payload["videos"] = 0

    # Volume
    try:
        if _player is not None:
            payload["volume"] = _player.audio_get_volume()
    except Exception as e:
        app.logger.warning("status: volume read failed: %s", e)
        payload["volume"] = None

    # Etat VLC
    try:
        payload["state"] = get_vlc_state_str()
    except Exception as e:
        app.logger.warning("status: state read failed: %s", e)
        payload["state"] = "error"
        payload["vlc_error"] = str(e)

    return jsonify(payload), 200

# Petit endpoint de vie simple
@app.route("/health")
def health():
    return jsonify(ok=True)

@app.route("/status_min")
def status_min():
    with videos_lock:
        count = len(videos)
        current = videos[video_index] if (0 <= video_index < len(videos)) else None
    return jsonify(ok=True, videos=count, current=current), 200

# ==============================
# Main
# ==============================
if __name__ == "__main__":
    os.makedirs(VIDEO_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)

    # On NE pré-initialise PAS VLC ici (lazy). On précharge juste la 1ère vidéo si dispo
    safe_refresh_videos()
    with videos_lock:
        if videos:
            video_index = 0

    app.run(host="0.0.0.0", port=5000)

