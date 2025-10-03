# app/web.py
from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import threading
import time
import subprocess, shutil, shlex


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

# ---------- rclone helpers ----------
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(APP_ROOT, "settings.json")
RCLONE_LOG_DIR = os.path.join(USER_HOME, ".local", "share", "rpi-avp")
RCLONE_LOG = os.path.join(RCLONE_LOG_DIR, "rclone_sync.log")
os.makedirs(RCLONE_LOG_DIR, exist_ok=True)

def load_settings():
    try:
        import json
        if os.path.isfile(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception as e:
        app.logger.warning("load_settings error: %s", e)
    return {}

def save_settings(data: dict):
    try:
        import json
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        app.logger.warning("save_settings error: %s", e)

def get_setting(key, default=None):
    return load_settings().get(key, default)

def set_settings(**kwargs):
    cfg = load_settings()
    cfg.update(kwargs)
    save_settings(cfg)

def run_cmd(cmd_list, timeout=30, env=None):
    """Run and capture stdout/stderr."""
    try:
        p = subprocess.run(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, text=True, env=env)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return 124, f"Timeout: {' '.join(cmd_list)}"
    except Exception as e:
        return 1, f"Error: {type(e).__name__}: {e}"

def which_rclone():
    return shutil.which("rclone")

def rclone_conf_path():
    # ~/.config/rclone/rclone.conf
    return os.path.join(USER_HOME, ".config", "rclone", "rclone.conf")

def rclone_base_env():
    env = os.environ.copy()
    # make sure HOME is correct for non-interactive systemd
    env["HOME"] = USER_HOME
    return env


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

# ---------- rclone: UI page ----------
@app.route("/rclone")
def rclone_page():
    return render_template("rclone_setup.html")

# ---------- rclone: API ----------
@app.route("/api/rclone/check")
def api_rclone_check():
    rc = which_rclone()
    info = {"which": rc, "version": None, "remotes": []}
    if rc:
        code, out = run_cmd([rc, "version"], timeout=10, env=rclone_base_env())
        info["version"] = (out.splitlines()[0].strip() if out else None)
        code2, out2 = run_cmd([rc, "listremotes"], timeout=10, env=rclone_base_env())
        if out2:
            info["remotes"] = [x.strip().rstrip(":") for x in out2.splitlines() if x.strip()]
    return jsonify(info)

@app.route("/api/rclone/install", methods=["POST"])
def api_rclone_install():
    # tentative auto (nécessite sudo sans mot de passe)
    cmd = ["bash", "-lc", "curl -fsSL https://rclone.org/install.sh | sudo bash"]
    code, out = run_cmd(cmd, timeout=120, env=rclone_base_env())
    if code != 0:
        return jsonify(message="Échec auto. Exécutez manuellement : curl -fsSL https://rclone.org/install.sh | sudo bash",
                       output=out, code=code), 200
    return jsonify(message="rclone installé/mis à jour.", output=out, code=code)

@app.route("/api/rclone/settings", methods=["GET","POST"])
def api_rclone_settings():
    if request.method == "GET":
        return jsonify({
            "remote_name": get_setting("remote_name", "gdrive"),
            "remote_folder": get_setting("remote_folder", "VideosRPi")
        })
    data = request.get_json() or {}
    rn = (data.get("remote_name") or "gdrive").strip()
    rf = (data.get("remote_folder") or "VideosRPi").strip()
    set_settings(remote_name=rn, remote_folder=rf)
    return jsonify(ok=True)

@app.route("/api/rclone/config/create", methods=["POST"])
def api_rclone_config_create():
    """Création/MAJ non-interactive d'un remote Drive via token JSON (rclone authorize "drive")."""
    if not which_rclone():
        return jsonify(error="rclone non installé"), 400

    import json
    data = request.get_json() or {}
    rn = (data.get("remote_name") or get_setting("remote_name", "gdrive")).strip()
    scope = (data.get("drive_scope") or "drive").strip()
    client_id = (data.get("client_id") or "").strip()
    client_secret = (data.get("client_secret") or "").strip()
    token_raw = (data.get("token_json") or "").strip()
    if not token_raw:
        return jsonify(error='Token JSON manquant (utilisez rclone authorize "drive")'), 400

    # Valider + minifier le JSON du token (évite les soucis d’espaces/retours à la ligne)
    try:
        token_min = json.dumps(json.loads(token_raw), separators=(",", ":"))
    except Exception as e:
        return jsonify(error=f"Token JSON invalide: {e}"), 400

    rc = which_rclone()

    # Le remote existe déjà ?
    code_lr, out_lr = run_cmd([rc, "listremotes"], timeout=15, env=rclone_base_env())
    existing = [x.strip().rstrip(":") for x in (out_lr or "").splitlines() if x.strip()]
    exists = rn in existing

    # Construire la commande (create vs update) en mode non-interactif
    base = [rc, "config", "update" if exists else "create", "--non-interactive", "--auto-confirm", rn]
    if not exists:
        base.append("drive")  # type uniquement pour create

    kv = [f"scope={scope}", f"token={token_min}"]
    if client_id:
        kv.append(f"client_id={client_id}")
    if client_secret:
        kv.append(f"client_secret={client_secret}")

    code, out = run_cmd(base + kv, timeout=180, env=rclone_base_env())
    if code != 0:
        # Fallback: si "create" échoue car déjà présent, tenter "update"
        msg = (out or "").lower()
        if not exists and ("exist" in msg or "already" in msg):
            cmd2 = [rc, "config", "update", "--non-interactive", "--auto-confirm", rn] + kv
            code2, out2 = run_cmd(cmd2, timeout=120, env=rclone_base_env())
            if code2 == 0:
                if not get_setting("remote_name"):
                    set_settings(remote_name=rn)
                return jsonify(message=f"Remote '{rn}' mis à jour.", output=out2, code=code2)
        return jsonify(error="Échec création remote", output=out, code=code), 400

    if not get_setting("remote_name"):
        set_settings(remote_name=rn)

    return jsonify(message=f"Remote '{rn}' {'mis à jour' if exists else 'créé'}.", output=out, code=code)


@app.route("/api/rclone/config/test", methods=["POST"])
def api_rclone_config_test():
    if not which_rclone():
        return jsonify(error="rclone non installé"), 400
    data = request.get_json() or {}
    rn = (data.get("remote_name") or get_setting("remote_name", "gdrive")).strip()
    rf = (data.get("remote_folder") or get_setting("remote_folder", "VideosRPi")).strip()
    target = f"{rn}:{rf}" if rf else f"{rn}:"
    code, out = run_cmd([which_rclone(), "lsd", target], timeout=20, env=rclone_base_env())
    if code != 0:
        return jsonify(error=f"lsd {target} a échoué", output=out, code=code), 400
    return jsonify(message=f"Connexion OK sur {target}", output=out, code=0)

@app.route("/api/rclone/sync", methods=["POST"])
def api_rclone_sync():
    if not which_rclone():
        return jsonify(error="rclone non installé"), 400
    data = request.get_json() or {}
    rn = (data.get("remote_name") or get_setting("remote_name", "gdrive")).strip()
    rf = (data.get("remote_folder") or get_setting("remote_folder", "VideosRPi")).strip()
    target = f"{rn}:{rf}" if rf else f"{rn}:"

    def _run():
        try:
          with open(RCLONE_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"\n--- sync started {time.ctime()} ---\n")
            cmd = [which_rclone(), "sync", target, VIDEO_DIR, "--delete-during", "--fast-list"]
            p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=rclone_base_env())
            p.communicate()
            fh.write(f"--- sync finished {time.ctime()} exit={p.returncode} ---\n")
        except Exception as e:
            with open(RCLONE_LOG, "a", encoding="utf-8") as fh:
                fh.write(f"ERROR: {type(e).__name__}: {e}\n")
        # post-traitement: miniatures & refresh
        try:
            generate_thumbnails(VIDEO_DIR, THUMB_DIR, VLC_START_AT)
            # refresh complet (peut prendre un peu de temps mais on est hors requête)
            safe_refresh_videos(non_blocking=False)
        except Exception as e:
            app.logger.warning("post-sync error: %s", e)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify(message=f"Sync démarrée depuis {target} → {VIDEO_DIR} (log: {RCLONE_LOG})")

@app.route("/api/rclone/log")
def api_rclone_log():
    tail = int(request.args.get("tail", "200"))
    if not os.path.isfile(RCLONE_LOG):
        return "— (aucun log pour le moment)\n", 200, {"Content-Type":"text/plain; charset=utf-8"}
    try:
        with open(RCLONE_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()
        txt = "".join(lines[-tail:]) if tail > 0 else "".join(lines)
        return txt, 200, {"Content-Type":"text/plain; charset=utf-8"}
    except Exception as e:
        return f"Erreur lecture log: {e}\n", 200, {"Content-Type":"text/plain; charset=utf-8"}


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
