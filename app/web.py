# app/web.py
from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import threading
import time

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
videos_lock = threading.RLock()  # RLock pour éviter l'auto-deadlock
videos = refresh_videos_list(VIDEO_DIR)
video_index = 0

# Snapshots non bloquants (pour /status même si lock occupé)
_snapshot_lock = threading.Lock()
_snapshot_videos_count = len(videos)
_snapshot_current = videos[0] if videos else None

_thumb_thread_started = False
_thumb_thread_lock = threading.Lock()

# VLC lazy init (ne bloque jamais le démarrage de Flask)
_instance = None
_player = None
_last_vlc_error = None
_vlc_init_lock = threading.Lock()

def _vlc_opts_base():
    return ["--no-video-title-show", "--fullscreen", "--aout=alsa", "--alsa-audio-device=default"]

def _vlc_opts_candidates():
    headless = not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if headless:
        return [
            ["--vout=kmsdrm"],
            ["--vout=fb"],
            [],  # dernier fallback: laisser VLC choisir
        ]
    else:
        return [
            [],               # par défaut (gl/gles2/xcb)
            ["--vout=opengl"],
            ["--vout=xcb"],
        ]

def ensure_vlc_ready() -> bool:
    """Initialise VLC si nécessaire; n'empêche jamais Flask de tourner."""
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
                _last_vlc_error = None
                app.logger.info("VLC init success.")
                return True
            except Exception as e:
                _last_vlc_error = f"{type(e).__name__}: {e}"
                app.logger.warning("VLC init failed with opts %s -> %s", " ".join(opts) or "(default)", _last_vlc_error)
        app.logger.error("VLC could not be initialized with any option set.")
        return False

# ------------------------------
# Helpers non-bloquants
# ------------------------------
def _update_snapshot():
    global _snapshot_videos_count, _snapshot_current
    with _snapshot_lock:
        _snapshot_videos_count = len(videos)
        if 0 <= video_index < len(videos):
            _snapshot_current = videos[video_index]
        else:
            _snapshot_current = None

def _acquire(lock: threading.RLock, timeout: float) -> bool:
    # Python 3: acquire(timeout=...) existe; sur vieilles versions: fallback
    try:
        return lock.acquire(timeout=timeout)
    except TypeError:
        start = time.time()
        while time.time() - start < timeout:
            if lock.acquire(False):
                return True
            time.sleep(0.01)
        return False

def safe_refresh_videos(non_blocking: bool = True, timeout: float = 0.2):
    """Recharge la liste des vidéos sans bloquer indéfiniment."""
    global videos
    if non_blocking:
        got = _acquire(videos_lock, timeout)
        if not got:
            app.logger.debug("safe_refresh_videos: skipped (lock busy)")
            return
    else:
        videos_lock.acquire()

    try:
        videos = refresh_videos_list(VIDEO_DIR)
        _update_snapshot()
    finally:
        videos_lock.release()

def set_media_by_index(idx: int) -> bool:
    """Charge la vidéo d'index idx dans le MediaPlayer."""
    global _player, videos, VIDEO_DIR
    if not ensure_vlc_ready():
        return False
    if not _acquire(videos_lock, 0.2):
        app.logger.warning("set_media_by_index: lock busy, abort")
        return False
    try:
        if not videos or idx < 0 or idx >= len(videos):
            return False
        name = videos[idx]
        path = os.path.join(VIDEO_DIR, name)
        media = _instance.media_new(path)
        _player.set_media(media)
        return True
    finally:
        _update_snapshot()
        videos_lock.release()

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

def get_snapshot():
    with _snapshot_lock:
        return _snapshot_videos_count, _snapshot_current

def ensure_media_loaded():
    """Charge une vidéo si rien n'est prêt, sans bloquer."""
    if not ensure_vlc_ready():
        return False
    if _player.get_media() is None:
        # tente de lire l'index courant
        return set_media_by_index(max(0, min(video_index, len(videos) - 1))) if videos else False
    return True

# ==============================
# Routes
# ==============================
@app.route("/")
def index():
    # rafraîchit sans bloquer (si lock occupé, on sert quand même la page)
    safe_refresh_videos(non_blocking=True, timeout=0.1)
    ensure_thumbnails_background()
    # on lit la liste SANS lock pour éviter les blocages (copie défensive)
    lst = []
    if _acquire(videos_lock, 0.05):
        try:
            lst = list(videos)
        finally:
            videos_lock.release()
    else:
        # fallback rapide: aucune liste (la page se charge quand même)
        lst = []
    return render_template("index.html", videos=lst)

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
    # Pas de lock long ici; on lit juste la taille en snapshot
    count, _ = get_snapshot()

    if action == "play":
        if not ensure_media_loaded():
            return jsonify(status="error", message=f"VLC not ready: {_last_vlc_error}"), 500
        _player.play()
    elif action == "pause":
        if not ensure_vlc_ready():
            return jsonify(status="error", message="VLC not ready"), 500
        _player.pause()
    elif action == "next":
        if count == 0:
            return jsonify(status="error", message="No videos"), 400
        # on ajuste l'index de manière optimiste, puis on charge
        video_index = (video_index + 1) % max(1, count)
        if not set_media_by_index(video_index):
            return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500
        _player.stop(); _player.play()
    elif action == "prev":
        if count == 0:
            return jsonify(status="error", message="No videos"), 400
        video_index = (video_index - 1) % max(1, count)
        if not set_media_by_index(video_index):
            return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500
        _player.stop(); _player.play()
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

    # rafraîchit sans bloquer
    safe_refresh_videos(non_blocking=True, timeout=0.1)

    # tente de trouver l'index sans verrou bloquant
    idx = -1
    if _acquire(videos_lock, 0.1):
        try:
            if video_name in videos:
                idx = videos.index(video_name)
        finally:
            videos_lock.release()
    else:
        # en dernier recours, on compare au snapshot courant
        cnt, cur = get_snapshot()
        # si le nom demandé correspond au courant snapshot, on l'assume
        if cur == video_name:
            idx = video_index  # best effort

    if idx < 0:
        app.logger.warning("Video not found (non-blocking): %s", video_name)
        return jsonify(status="error", message="Video not found"), 404

    video_index = idx
    if not set_media_by_index(video_index):
        return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500

    _player.stop()
    _player.play()
    app.logger.info("Now playing index=%d name=%s", video_index, video_name)
    return jsonify(status="playing", video=video_name)

@app.route("/status")
def status():
    # NE DOIT JAMAIS BLOQUER
    cnt, cur = get_snapshot()
    try:
        vol = _player.audio_get_volume() if _player is not None else None
    except Exception:
        vol = None
    payload = dict(
        running=True,
        videos=cnt,
        volume=vol,
        state=get_vlc_state_str(),
        current=cur,
        vlc_ready=(_player is not None),
        vlc_error=_last_vlc_error,
    )
    return jsonify(payload), 200

@app.route("/status_min")
def status_min():
    # NE DOIT JAMAIS BLOQUER
    cnt, cur = get_snapshot()
    return jsonify(ok=True, videos=cnt, current=cur), 200

@app.route("/health")
def health():
    return jsonify(ok=True)

# ==============================
# Main
# ==============================
if __name__ == "__main__":
    os.makedirs(VIDEO_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    # précharge juste l'index (pas de VLC ici)
    safe_refresh_videos(non_blocking=False)
    if videos:
        video_index = 0
    app.run(host="0.0.0.0", port=5000)
