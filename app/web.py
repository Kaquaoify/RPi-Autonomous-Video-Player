# app/web.py
from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import threading
import time
import subprocess, shutil  # (shlex supprimé : non utilisé)

# VLC requis côté Python
try:
    import vlc
except Exception as exc:
    raise RuntimeError("python-vlc requis. Installez : sudo apt install python3-vlc") from exc

from utils import generate_thumbnails, refresh_videos_list

app = Flask(__name__, template_folder="templates", static_folder="static")

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

# VLC : instance + DEUX lecteurs (A/B) pour switch sans “flash”
_instance = None            # vlc.Instance
_player_a = None            # vlc.MediaPlayer
_player_b = None            # vlc.MediaPlayer
_active_is_a = True         # True -> on joue sur A, False -> sur B
_last_vlc_error = None
_vlc_init_lock = threading.Lock()

# Lecture/loop
_end_event_attached = False  # uniquement pour le player actif

# ==============================
# VLC : choix d’options (on garde ta base)
# ==============================
def _vlc_opts_base():
    # Audio ALSA par défaut (PulseAudio souvent absent en headless)
    # Important: on garde fbdev comme dans ta version stable (tu avais HDMI ok avec ça).
    return [
        "--no-video-title-show",
        "--fullscreen",
        "--aout=alsa",
        "--alsa-audio-device=default",
        "--fbdev=/dev/fb0",
    ]

def _vlc_opts_candidates():
    headless = not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if headless:
        # Même ordre qu’avant: fb -> kmsdrm -> défaut
        return [["--vout=fb"], ["--vout=kmsdrm"], []]
    return [[], ["--vout=opengl"], ["--vout=xcb"]]

# ==============================
# Helpers internes VLC (double player)
# ==============================
def _active_player():
    return _player_a if _active_is_a else _player_b

def _inactive_player():
    return _player_b if _active_is_a else _player_a

def _swap_players():
    global _active_is_a
    _active_is_a = not _active_is_a

def _attach_end_event(ply):
    global _end_event_attached
    try:
        em = ply.event_manager()
    except Exception as e:
        app.logger.warning("event_manager() failed: %s", e)
        return
    def _on_end(event):
        # Passe à la suivante sans relâcher la sortie vidéo
        threading.Thread(target=_play_next_crossfade, daemon=True).start()
    try:
        em.event_attach(vlc.EventType.MediaPlayerEndReached, _on_end)
        _end_event_attached = True
    except Exception as e:
        app.logger.warning("attach_end_reached failed: %s", e)

def ensure_vlc_ready() -> bool:
    """Init VLC + 2 MediaPlayer si besoin (idempotent)."""
    global _instance, _player_a, _player_b, _last_vlc_error, _end_event_attached
    if _player_a is not None and _player_b is not None:
        return True
    with _vlc_init_lock:
        if _player_a is not None and _player_b is not None:
            return True
        base = _vlc_opts_base()
        for extra in _vlc_opts_candidates():
            opts = base + extra
            try:
                app.logger.info("VLC init try: %s", " ".join(opts) or "(default)")
                inst = vlc.Instance(*opts)
                pa = inst.media_player_new()
                pb = inst.media_player_new()
                try:
                    pa.audio_set_volume(80)
                    pb.audio_set_volume(80)
                except Exception:
                    pass
                _instance = inst
                _player_a = pa
                _player_b = pb
                _last_vlc_error = None
                app.logger.info("VLC init success (dual players).")
                # attache l'event fin sur le player actif
                if not _end_event_attached:
                    _attach_end_event(_active_player())
                return True
            except Exception as e:
                _last_vlc_error = f"{type(e).__name__}: {e}"
                app.logger.warning(
                    "VLC init failed with opts %s -> %s",
                    " ".join(opts) or "(default)", _last_vlc_error
                )
        app.logger.error("VLC init impossible avec les options testées.")
        return False

# ==============================
# Helpers thread-safe & non bloquants
# ==============================
def _update_snapshot():
    """Met à jour le snapshot (count + nom courant)."""
    global _snapshot_videos_count, _snapshot_current
    with _snapshot_lock:
        _snapshot_videos_count = len(videos)
        _snapshot_current = videos[video_index] if 0 <= video_index < len(videos) else None

def _acquire(lock: threading.RLock, timeout: float) -> bool:
    """Acquire avec timeout (fallback pour anciennes versions)."""
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
    """Rafraîchit la liste sans bloquer indéfiniment."""
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

# --- Aperçu: helpers settings + nettoyage ---
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(APP_ROOT, "settings.json")
RCLONE_LOG_DIR = os.path.join(USER_HOME, ".local", "share", "rpi-avp")
RCLONE_LOG = os.path.join(RCLONE_LOG_DIR, "rclone_sync.log")
os.makedirs(RCLONE_LOG_DIR, exist_ok=True)

def is_preview_enabled() -> bool:
    return bool(get_setting("preview_enabled", False))

def set_preview_enabled(val: bool):
    set_settings(preview_enabled=bool(val))

def clear_hls_dir():
    try:
        shutil.rmtree(HLS_DIR, ignore_errors=True)
    finally:
        os.makedirs(HLS_DIR, exist_ok=True)

def _media_for_index(idx: int):
    """Crée un Media pour l’index donné (+ HLS si activé)."""
    path = os.path.join(VIDEO_DIR, videos[idx])
    m = _instance.media_new(path)
    if is_preview_enabled():
        # IMPORTANT: on ne vide pas HLS ici (on le fait seulement à enable/disable)
        index_path = HLS_INDEX
        seg_path_tmpl = os.path.join(HLS_DIR, "seg-########.ts")
        index_url = "/hls/seg-########.ts"
        sout = (
            f"#duplicate{{dst=display,"
            f"dst=std{{access=livehttp{{seglen=2,delsegs=true,numsegs=5,"
            f"index={index_path},index-url={index_url}}},"
            f"mux=ts{{use-key-frames}},dst={seg_path_tmpl}}}}}"
        )
        m.add_option(f":sout={sout}")
        m.add_option(":sout-all")
        m.add_option(":sout-keep")
    return m

def ensure_thumbnails_background():
    """Lance une unique génération de miniatures en arrière-plan."""
    global _thumb_thread_started
    with _thumb_thread_lock:
        if _thumb_thread_started:
            return
        _thumb_thread_started = True
    threading.Thread(
        target=generate_thumbnails, args=(VIDEO_DIR, THUMB_DIR, VLC_START_AT), daemon=True
    ).start()

def get_vlc_state_str():
    """Retourne l’état VLC (texte)."""
    ply = _active_player()
    if ply is None:
        return "uninitialized"
    try:
        st = ply.get_state()
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
    """Renvoie (count, nom_courant) sans lock long."""
    with _snapshot_lock:
        return _snapshot_videos_count, _snapshot_current

def ensure_media_loaded():
    """Charge une vidéo si aucune n’est prête."""
    if not ensure_vlc_ready():
        return False
    if not videos:
        return False
    # Si aucun des lecteurs n’a de média, charge l’index courant sur l’actif
    ply = _active_player()
    try:
        if ply.get_media() is None:
            m = _media_for_index(max(0, min(video_index, len(videos)-1)))
            ply.set_media(m)
    except Exception:
        pass
    return True

# ======== Switch sans flash (A/B) ========
def _play_on(ply, idx: int) -> bool:
    """Assigne le média idx sur ply et lance play (plein écran)"""
    try:
        m = _media_for_index(idx)
        ply.set_media(m)
        ok = ply.play()
        try:
            ply.set_fullscreen(True)
        except Exception:
            pass
        return bool(ok)
    except Exception as e:
        app.logger.warning(f"_play_on error: {e}")
        return False

def _play_next_crossfade():
    """Passe à la vidéo suivante en lançant d’abord sur le player inactif puis en stoppant l’actif."""
    global video_index, _end_event_attached
    if not ensure_vlc_ready():
        return
    cnt, _ = get_snapshot()
    if cnt == 0:
        return
    next_idx = (video_index + 1) % cnt
    act = _active_player()
    ina = _inactive_player()

    # 1) Lancer sur le lecteur inactif
    if not _play_on(ina, next_idx):
        return
    # 2) légère latence pour laisser le vout accrocher (évite le flash)
    time.sleep(0.05)
    # 3) Stop l’ancien lecteur
    try:
        act.stop()
    except Exception:
        pass
    # 4) bascule
    _swap_players()
    video_index = next_idx

    # 5) ré-attacher l’event fin sur le nouveau player actif (une seule fois)
    try:
        _attach_end_event(_active_player())
    except Exception:
        pass

def _play_current():
    """Lecture index courant via double player (sans libérer la sortie vidéo)."""
    if not ensure_vlc_ready() or not videos:
        return False
    # On lance sur inactif d’abord (pour éviter tout “trou”), puis on coupe l’actif
    act = _active_player()
    ina = _inactive_player()
    ok = _play_on(ina, video_index)
    if not ok:
        return False
    time.sleep(0.05)
    try:
        act.stop()
    except Exception:
        pass
    _swap_players()
    # s’assurer que l’event fin est bien posé sur le nouveau player actif
    try:
        _attach_end_event(_active_player())
    except Exception:
        pass
    return True

# ==============================
# Bootstrapping (sync + thumbs + autoplay)
# ==============================
def _bootstrap_startup():
    """
    Au premier démarrage de l'app:
    1) (optionnel) sync Drive -> VIDEO_DIR
    2) thumbnails + refresh
    3) (optionnel) autoplay première vidéo
    """
    try:
        # 1) Sync Drive si activé
        if setting_sync_on_boot():
            ok, msg = sync_from_settings_blocking()
            app.logger.info("boot sync: %s", msg)
        else:
            # même sans sync on assure une liste propre
            safe_refresh_videos(non_blocking=False)

        # 2) thumbnails en tâche de fond
        ensure_thumbnails_background()

        # 3) Autoplay si demandé
        if setting_autoplay() and get_snapshot()[0] > 0:
            time.sleep(0.5)  # petite respiration pour ALSA/VLC
            # play index 0 en “crossfade” (même si rien ne jouait avant)
            global video_index
            video_index = 0
            _play_current()
    except Exception as e:
        app.logger.warning("bootstrap startup error: %s", e)

_bootstrap_once = threading.Event()
def _start_bootstrap_once():
    if not _bootstrap_once.is_set():
        _bootstrap_once.set()
        threading.Thread(target=_bootstrap_startup, daemon=True).start()

# ==============================
# rclone : fichiers & utilitaires
# ==============================
def load_settings():
    """Lit settings.json (dict)."""
    try:
        import json
        if os.path.isfile(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception as e:
        app.logger.warning("load_settings error: %s", e)
    return {}

def save_settings(data: dict):
    """Écrit settings.json."""
    try:
        import json
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        app.logger.warning("save_settings error: %s", e)

def get_setting(key, default=None):
    """Raccourci lecture d’une clé settings."""
    return load_settings().get(key, default)

# Valeurs par défaut si absentes dans settings.json
def setting_sync_on_boot() -> bool:
    return bool(get_setting("sync_on_boot", True))

def setting_autoplay() -> bool:
    return bool(get_setting("autoplay", True))

def setting_loop_all() -> bool:
    return bool(get_setting("loop_all", True))

def set_settings(**kwargs):
    """Merge & sauvegarde des settings."""
    cfg = load_settings()
    cfg.update(kwargs)
    save_settings(cfg)

def run_cmd(cmd_list, timeout=30, env=None):
    """Exécute une commande et capture stdout/err."""
    try:
        p = subprocess.run(
            cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, text=True, env=env
        )
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return 124, f"Timeout: {' '.join(cmd_list)}"
    except Exception as e:
        return 1, f"Error: {type(e).__name__}: {e}"

def which_rclone():
    """Chemin du binaire rclone (ou None)."""
    return shutil.which("rclone")

def rclone_conf_path():
    """Chemin ~/.config/rclone/rclone.conf."""
    return os.path.join(USER_HOME, ".config", "rclone", "rclone.conf")

def rclone_base_env():
    """Env propre (HOME correct pour systemd)."""
    env = os.environ.copy()
    env["HOME"] = USER_HOME
    return env

def remove_remote_in_conf(remote_name: str):
    """Supprime [remote_name] dans rclone.conf (avec backup)."""
    path = rclone_conf_path()
    if not os.path.isfile(path):
        return False, "rclone.conf introuvable"
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        header = f"[{remote_name}]"
        new_lines, in_section = [], False

        for line in lines:
            s = line.strip()
            # Début de section ?
            if s.startswith("[") and s.endswith("]"):
                in_section = (s == header)
                if in_section:
                    continue  # on saute l’en-tête ciblé
            if not in_section:
                new_lines.append(line)

        if len(new_lines) == len(lines):
            return False, f"Remote '{remote_name}' non trouvé dans rclone.conf"

        backup = path + ".bak"
        shutil.copy(path, backup)
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return True, f"Section supprimée (backup: {backup})"
    except Exception as e:
        return False, f"Erreur édition conf: {type(e).__name__}: {e}"

# ==============================
# Routes UI
# ==============================
@app.route("/")
def index():
    """Page d’accueil : liste des vidéos + contrôles."""
    safe_refresh_videos(non_blocking=True, timeout=0.1)
    ensure_thumbnails_background()

    # Copie défensive sans retenir le lock
    lst = []
    if _acquire(videos_lock, 0.05):
        try:
            lst = list(videos)
        finally:
            videos_lock.release()
    return render_template("index.html", videos=lst)

@app.route("/settings")
def settings_page():
    """Page paramètres rapides."""
    return render_template("settings.html")

@app.route("/favicon.ico")
def favicon():
    """Pas de favicon dédiée."""
    return ("", 204)

@app.route("/thumbnails/<filename>")
def thumbnails(filename):
    """Servez une miniature si présente."""
    if not os.path.isdir(THUMB_DIR):
        return ("", 404)
    return send_from_directory(THUMB_DIR, filename)

# ==============================
# API VLC
# ==============================
@app.route("/control/<action>", methods=["POST"])
def control(action):
    """Actions VLC : play/pause/next/prev/vol."""
    global video_index
    action = action.lower()
    count, _ = get_snapshot()  # pas de lock long

    if action == "play":
        if not ensure_media_loaded():
            return jsonify(status="error", message=f"VLC not ready: {_last_vlc_error}"), 500
        # Reprend la lecture sur le player actif (ne repart pas de zéro)
        try:
            _active_player().play()
        except Exception:
            return jsonify(status="error", message="play failed"), 500
    elif action == "pause":
        if not ensure_vlc_ready():
            return jsonify(status="error", message="VLC not ready"), 500
        try:
            _active_player().pause()
        except Exception:
            return jsonify(status="error", message="pause failed"), 500
    elif action == "next":
        if count == 0:
            return jsonify(status="error", message="No videos"), 400
        _play_next_crossfade()
    elif action == "prev":
        if count == 0:
            return jsonify(status="error", message="No videos"), 400
        # recule l’index, puis lance via crossfade
        video_index = (video_index - 1) % max(1, count)
        # Pour forcer la lecture du bon index: on déclenche comme un “current”
        _play_current()
    elif action == "volup":
        if not ensure_vlc_ready():
            return jsonify(status="error", message="VLC not ready"), 500
        try:
            vol = int(_active_player().audio_get_volume() or 0)
            _active_player().audio_set_volume(min(vol + VLC_AUDIO_VOLUME_STEP, 100))
        except Exception:
            pass
    elif action == "voldown":
        if not ensure_vlc_ready():
            return jsonify(status="error", message="VLC not ready"), 500
        try:
            vol = int(_active_player().audio_get_volume() or 0)
            _active_player().audio_set_volume(max(vol - VLC_AUDIO_VOLUME_STEP, 0))
        except Exception:
            pass
    else:
        return jsonify(status="error", message="Unknown action"), 400

    return jsonify(status="ok", action=action)

@app.route("/play-video", methods=["POST"])
def play_video():
    """Lecture d’une vidéo précise (nom de fichier)."""
    global video_index, videos
    data = request.get_json() or {}
    video_name = data.get("video")
    app.logger.info("POST /play-video %s", video_name)

    if not video_name:
        return jsonify(status="error", message="No video specified"), 400

    safe_refresh_videos(non_blocking=True, timeout=0.1)

    # Recherche index (non bloquant)
    idx = -1
    if _acquire(videos_lock, 0.1):
        try:
            if video_name in videos:
                idx = videos.index(video_name)
        finally:
            videos_lock.release()
    else:
        # Fallback : si correspond au snapshot courant
        _, cur = get_snapshot()
        if cur == video_name:
            idx = video_index

    if idx < 0:
        app.logger.warning("Video not found (non-blocking): %s", video_name)
        return jsonify(status="error", message="Video not found"), 404

    if not ensure_media_loaded():
        return jsonify(status="error", message=f"VLC not ready: {_last_vlc_error}"), 500

    # Fixe l’index ciblé et lance via crossfade (sans flash)
    video_index = idx
    ok = _play_current()
    if not ok:
        return jsonify(status="error", message="Failed to start video"), 500

    app.logger.info("Now playing index=%d name=%s", video_index, video_name)
    return jsonify(status="playing", video=video_name)

@app.route("/status")
def status():
    """Statut complet (ne doit pas bloquer)."""
    cnt, cur = get_snapshot()
    try:
        vol = _active_player().audio_get_volume() if _active_player() is not None else None
    except Exception:
        vol = None
    return jsonify(
        running=True,
        videos=cnt,
        volume=vol,
        state=get_vlc_state_str(),
        current=cur,
        vlc_ready=(_active_player() is not None),
        vlc_error=_last_vlc_error,
    ), 200

@app.route("/status_min")
def status_min():
    """Statut minimal (léger)."""
    cnt, cur = get_snapshot()
    return jsonify(ok=True, videos=cnt, current=cur), 200

@app.route("/health")
def health():
    """Ping simple pour watchdogs."""
    return jsonify(ok=True)

# -------- Aperçu: serve HLS ----------
@app.route("/hls/<path:filename>")
def hls_files(filename):
    # Pas de cache côté client pour suivre la playlist
    if not os.path.isfile(os.path.join(HLS_DIR, filename)) and filename != "index.m3u8":
        pass
    resp = send_from_directory(HLS_DIR, filename)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp

# -------- Aperçu: API ----------
@app.route("/api/preview/status")
def api_preview_status():
    return jsonify(enabled=is_preview_enabled(), url="/hls/index.m3u8")

@app.route("/api/preview/enable", methods=["POST"])
def api_preview_enable():
    if not ensure_vlc_ready():
        return jsonify(error="VLC not ready"), 500
    set_preview_enabled(True)
    clear_hls_dir()
    # rien d’autre: les nouveaux médias auront le sout; l’ancien continue sans trou
    return jsonify(ok=True, url="/hls/index.m3u8")

@app.route("/api/preview/disable", methods=["POST"])
def api_preview_disable():
    if not ensure_vlc_ready():
        return jsonify(error="VLC not ready"), 500
    set_preview_enabled(False)
    clear_hls_dir()
    return jsonify(ok=True)

# ==============================
# UI rclone
# ==============================
@app.route("/rclone")
def rclone_page():
    """Assistant de configuration rclone."""
    return render_template("rclone_setup.html")

# ==============================
# API rclone
# ==============================
@app.route("/api/rclone/check")
def api_rclone_check():
    """Présence rclone, version, remotes existants."""
    rc = which_rclone()
    info = {"which": rc, "version": None, "remotes": []}
    if rc:
        code, out = run_cmd([rc, "version"], timeout=10, env=rclone_base_env())
        info["version"] = (out.splitlines()[0].strip() if out else None)
        _, out2 = run_cmd([rc, "listremotes"], timeout=10, env=rclone_base_env())
        if out2:
            info["remotes"] = [x.strip().rstrip(":") for x in out2.splitlines() if x.strip()]
    return jsonify(info)

@app.route("/api/rclone/install", methods=["POST"])
def api_rclone_install():
    """Tentative d’installation/MAJ rclone (sudo requis)."""
    cmd = ["bash", "-lc", "curl -fsSL https://rclone.org/install.sh | sudo bash"]
    code, out = run_cmd(cmd, timeout=120, env=rclone_base_env())
    if code != 0:
        return jsonify(
            message="Échec auto. Exécutez manuellement : curl -fsSL https://rclone.org/install.sh | sudo bash",
            output=out, code=code
        ), 200
    return jsonify(message="rclone installé/mis à jour.", output=out, code=code)

@app.route("/api/rclone/settings", methods=["GET", "POST"])
def api_rclone_settings():
    """Lecture/écriture de remote_name & remote_folder."""
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
    """Create/Update d’un remote Drive via token JSON (rclone authorize)."""
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
        return jsonify(error=f"Token JSON manquant (utilisez rclone authorize \"drive\")"), 400

    # Valide & minifie le token (évite CR/LF parasites)
    try:
        token_min = json.dumps(json.loads(token_raw), separators=(",", ":"))
    except Exception as e:
        return jsonify(error=f"Token JSON invalide: {e}"), 400

    rc = which_rclone()
    # Existant ?
    _, out_lr = run_cmd([rc, "listremotes"], timeout=15, env=rclone_base_env())
    existing = [x.strip().rstrip(":") for x in (out_lr or "").splitlines() if x.strip()]
    exists = rn in existing

    # create vs update
    base = [rc, "config", "update" if exists else "create", "--non-interactive", "--auto-confirm", rn]
    if not exists:
        base.append("drive")
    kv = [f"scope={scope}", f"token={token_min}"]
    if client_id:
        kv.append(f"client_id={client_id}")
    if client_secret:
        kv.append(f"client_secret={client_secret}")

    code, out = run_cmd(base + kv, timeout=180, env=rclone_base_env())
    if code != 0:
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
    """Test de connexion sur le dossier (lsd)."""
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
    """Lance un sync Drive → VIDEO_DIR (thread)."""
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
        # Post-traitement local
        try:
            generate_thumbnails(VIDEO_DIR, THUMB_DIR, VLC_START_AT)
            safe_refresh_videos(non_blocking=False)
        except Exception as e:
            app.logger.warning("post-sync error: %s", e)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify(message=f"Sync démarrée depuis {target} → {VIDEO_DIR} (log: {RCLONE_LOG})")

def sync_from_settings_blocking() -> tuple[bool, str]:
    """
    Lance un rclone sync BLOQUANT en lisant remote_name/remote_folder dans settings.json.
    Écrit la sortie dans RCLONE_LOG. Retourne (ok, message).
    """
    rc = which_rclone()
    if not rc:
        return False, "rclone non installé"

    rn = (get_setting("remote_name", "gdrive") or "gdrive").strip()
    rf = (get_setting("remote_folder", "VideosRPi") or "VideosRPi").strip()
    target = f"{rn}:{rf}" if rf else f"{rn}:"

    os.makedirs(RCLONE_LOG_DIR, exist_ok=True)
    banner = f"--- boot sync {time.ctime()} → {target} ---\n"

    try:
        with open(RCLONE_LOG, "a", encoding="utf-8") as fh:
            fh.write(banner)
            cmd = [rc, "sync", target, VIDEO_DIR, "--delete-during", "--fast-list"]
            # Bloquant, on capture la sortie et on la dump (plus simple au boot)
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, env=rclone_base_env())
            fh.write(p.stdout or "")
            fh.write(f"--- boot sync done rc={p.returncode} ---\n")
            ok = (p.returncode == 0)
    except Exception as e:
        ok = False
        with open(RCLONE_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"ERROR boot sync: {type(e).__name__}: {e}\n")

    # Post-traitement local (comme ton endpoint /api/rclone/sync)
    try:
        generate_thumbnails(VIDEO_DIR, THUMB_DIR, VLC_START_AT)
        safe_refresh_videos(non_blocking=False)
    except Exception as e:
        app.logger.warning("post-sync boot error: %s", e)

    return ok, ("OK" if ok else "échec")

@app.route("/api/rclone/config/delete", methods=["POST"])
def api_rclone_config_delete():
    """Supprime un remote rclone (sans toucher aux fichiers)."""
    if not which_rclone():
        return jsonify(error="rclone non installé"), 400

    data = request.get_json() or {}
    rn = (data.get("remote_name") or get_setting("remote_name", "")).strip()
    if not rn:
        return jsonify(error="Nom du remote manquant"), 400

    rc = which_rclone()
    # Présence du remote ?
    _, out_lr = run_cmd([rc, "listremotes"], timeout=15, env=rclone_base_env())
    existing = [x.strip().rstrip(":") for x in (out_lr or "").splitlines() if x.strip()]
    if rn not in existing:
        cfg = load_settings()
        if cfg.get("remote_name") == rn:
            cfg.pop("remote_name", None)
            save_settings(cfg)
        return jsonify(message=f"Remote '{rn}' inexistant (déjà supprimé).", code=0)

    # Tentative standard
    code, out = run_cmd([rc, "config", "delete", rn], timeout=60, env=rclone_base_env())
    if code != 0:
        # Fallback : édition directe du fichier .conf
        ok, msg = remove_remote_in_conf(rn)
        if ok:
            cfg = load_settings()
            if cfg.get("remote_name") == rn:
                cfg.pop("remote_name", None)
                save_settings(cfg)
            return jsonify(
                message=f"Remote '{rn}' supprimé (édition directe du fichier). {msg}",
                output=out, code=0
            )
        return jsonify(error=f"Échec suppression remote '{rn}'", output=out, details=msg, code=code), 400

    # Nettoyage settings
    try:
        cfg = load_settings()
        if cfg.get("remote_name") == rn:
            cfg.pop("remote_name", None)
            save_settings(cfg)
    except Exception as e:
        app.logger.warning("unset remote_name failed: %s", e)

    return jsonify(message=f"Remote '{rn}' supprimé.", output=out, code=code)

@app.route("/api/rclone/log")
def api_rclone_log():
    """Renvoie la fin du log rclone (texte brut)."""
    tail = int(request.args.get("tail", "200"))
    if not os.path.isfile(RCLONE_LOG):
        return "— (aucun log pour le moment)\n", 200, {"Content-Type": "text/plain; charset=utf-8"}
    try:
        with open(RCLONE_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()
        txt = "".join(lines[-tail:]) if tail > 0 else "".join(lines)
        return txt, 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as e:
        return f"Erreur lecture log: {e}\n", 200, {"Content-Type": "text/plain; charset=utf-8"}

# ==============================
# Main (lancement Flask)
# ==============================
if __name__ == "__main__":
    os.makedirs(VIDEO_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)

    # Précharge l’index (aucun appel VLC ici)
    safe_refresh_videos(non_blocking=False)
    if videos:
        video_index = 0

    # ⚡ Démarre la sync/boot immédiatement au lancement du service
    _start_bootstrap_once()

    app.run(host="0.0.0.0", port=5000)
