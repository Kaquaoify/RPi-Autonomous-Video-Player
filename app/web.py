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
# Configurables (change per RPi)
# ==============================
USER_HOME = os.path.expanduser("~")                   # home de l'utilisateur courant
VIDEO_DIR = os.path.join(USER_HOME, "Videos", "RPi-Autonomous-Video-Player")
THUMB_DIR = os.path.join(VIDEO_DIR, "thumbnails")
VLC_AUDIO_VOLUME_STEP = 10                            # incrément volume
VLC_START_AT = 5                                      # seconde utilisée pour thumbnail capture

# ==============================
# Global state
# ==============================
videos_lock = threading.Lock()
videos = refresh_videos_list(VIDEO_DIR)
video_index = 0

# Un seul thread de génération de miniatures
_thumb_thread_started = False
_thumb_thread_lock = threading.Lock()

# VLC player (single instance)
_instance = vlc.Instance()
_player = vlc.MediaPlayer()
# Ensure volume starts at 80 if possible
try:
    _player.audio_set_volume(80)
except Exception:
    pass

# ==============================
# Helpers
# ==============================
def set_media_by_index(idx: int) -> bool:
    """Charge la vidéo d'index idx dans le MediaPlayer (safe)."""
    global _player, videos, VIDEO_DIR
    with videos_lock:
        if not videos:
            return False
        if idx < 0 or idx >= len(videos):
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

    t = threading.Thread(
        target=generate_thumbnails,
        args=(VIDEO_DIR, THUMB_DIR, VLC_START_AT),
        daemon=True,
    )
    t.start()

def get_vlc_state_str():
    """Retourne l'état VLC en chaîne lisible."""
    try:
        st = _player.get_state()
    except Exception:
        return None
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
        if not videos:
            return None
        if video_index < 0 or video_index >= len(videos):
            return None
        return videos[video_index]

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
    # évite 404/bruit dans les logs quand le navigateur demande un favicon
    return ("", 204)

@app.route("/thumbnails/<filename>")
def thumbnails(filename):
    # Serve thumbnail file if exists; else return 404
    if not os.path.isdir(THUMB_DIR):
        return ("", 404)
    return send_from_directory(THUMB_DIR, filename)

@app.route("/control/<action>", methods=["POST"])
def control(action):
    global video_index, videos, _player
    action = action.lower()
    with videos_lock:
        count = len(videos)

    if action == "play":
        _player.play()
    elif action == "pause":
        _player.pause()
    elif action == "next":
        with videos_lock:
            if count == 0:
                return jsonify(status="error", message="No videos"), 400
            video_index = (video_index + 1) % count
        if set_media_by_index(video_index):
            _player.play()
    elif action == "prev":
        with videos_lock:
            if count == 0:
                return jsonify(status="error", message="No videos"), 400
            video_index = (video_index - 1) % count
        if set_media_by_index(video_index):
            _player.play()
    elif action == "volup":
        try:
            vol = _player.audio_get_volume()
            _player.audio_set_volume(min(int(vol) + VLC_AUDIO_VOLUME_STEP, 100))
        except Exception:
            pass
    elif action == "voldown":
        try:
            vol = _player.audio_get_volume()
            _player.audio_set_volume(max(int(vol) - VLC_AUDIO_VOLUME_STEP, 0))
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
        return jsonify(status="error", message="Failed to set media"), 500

    _player.play()
    app.logger.info("Now playing index=%d name=%s", video_index, video_name)
    return jsonify(status="playing", video=video_name)

# simple status endpoint
@app.route("/status")
def status():
    try:
        vol = _player.audio_get_volume()
    except Exception:
        vol = None
    with videos_lock:
        count = len(videos)
    return jsonify(
        running=True,
        videos=count,
        volume=vol,
        state=get_vlc_state_str(),
        current=get_current_video_name(),
    )

# ==============================
# Main
# ==============================
if __name__ == "__main__":
    # ensure directories exist
    os.makedirs(VIDEO_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=5000)
