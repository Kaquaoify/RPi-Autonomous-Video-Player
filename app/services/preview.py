import os, subprocess, shutil, threading, time
from ..utils import generate_thumbnails, refresh_videos_list

def is_preview_enabled() -> bool:
    return bool(get_setting("preview_enabled", False))

def set_preview_enabled(val: bool):
    set_settings(preview_enabled=bool(val))

def clear_hls_dir():
    try:
        shutil.rmtree(HLS_DIR, ignore_errors=True)
    finally:
        os.makedirs(HLS_DIR, exist_ok=True)



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
        _player.play()
    elif action == "pause":
        if not ensure_vlc_ready():
            return jsonify(status="error", message="VLC not ready"), 500
        _player.pause()
    elif action == "next":
        if count == 0:
            return jsonify(status="error", message="No videos"), 400
        video_index = (video_index + 1) % max(1, count)
        if not set_media_by_index(video_index):
            return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500
        _play_current()
    elif action == "prev":
        if count == 0:
            return jsonify(status="error", message="No videos"), 400
        video_index = (video_index - 1) % max(1, count)
        if not set_media_by_index(video_index):
            return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500
        _play_current()
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

    video_index = idx
    if not set_media_by_index(video_index):
        return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500

    _play_current()
    app.logger.info("Now playing index=%d name=%s", video_index, video_name)
    return jsonify(status="playing", video=video_name)


@app.route("/status")
def status():
    """Statut complet (ne doit pas bloquer)."""
    cnt, cur = get_snapshot()
    try:
        vol = _player.audio_get_volume() if _player is not None else None
    except Exception:
        vol = None
    return jsonify(
        running=True,
        videos=cnt,
        volume=vol,
        state=get_vlc_state_str(),
        current=cur,
        vlc_ready=(_player is not None),
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
    from flask import make_response, send_from_directory
    if not os.path.isfile(os.path.join(HLS_DIR, filename)) and filename != "index.m3u8":
        # on laisse VLC créer les fichiers; si absent -> 404
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
    set_preview_enabled(True)
    clear_hls_dir()
    # recharge le média courant pour (ré)appliquer le sout
    if get_snapshot()[0] > 0:
        try:
            _player.stop()
        except Exception:
            pass
        if set_media_by_index(max(0, min(video_index, len(videos)-1))):
            _player.play()
    return jsonify(ok=True, url="/hls/index.m3u8")

@app.route("/api/preview/disable", methods=["POST"])
def api_preview_disable():
    set_preview_enabled(False)
    # recharge le média courant pour retirer le sout
    if get_snapshot()[0] > 0:
        try:
            _player.stop()
        except Exception:
            pass
        if set_media_by_index(max(0, min(video_index, len(videos)-1))):
            _player.play()
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
        return jsonify(error='Token JSON manquant (utilisez rclone authorize "drive")'), 400

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


def set_preview_enabled(val: bool):
    set_settings(preview_enabled=bool(val))

def clear_hls_dir():
    try:
        shutil.rmtree(HLS_DIR, ignore_errors=True)
    finally:
        os.makedirs(HLS_DIR, exist_ok=True)



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
        _player.play()
    elif action == "pause":
        if not ensure_vlc_ready():
            return jsonify(status="error", message="VLC not ready"), 500
        _player.pause()
    elif action == "next":
        if count == 0:
            return jsonify(status="error", message="No videos"), 400
        video_index = (video_index + 1) % max(1, count)
        if not set_media_by_index(video_index):
            return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500
        _play_current()
    elif action == "prev":
        if count == 0:
            return jsonify(status="error", message="No videos"), 400
        video_index = (video_index - 1) % max(1, count)
        if not set_media_by_index(video_index):
            return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500
        _play_current()
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

    video_index = idx
    if not set_media_by_index(video_index):
        return jsonify(status="error", message=f"Failed to set media: {_last_vlc_error}"), 500

    _play_current()
    app.logger.info("Now playing index=%d name=%s", video_index, video_name)
    return jsonify(status="playing", video=video_name)


@app.route("/status")
def status():
    """Statut complet (ne doit pas bloquer)."""
    cnt, cur = get_snapshot()
    try:
        vol = _player.audio_get_volume() if _player is not None else None
    except Exception:
        vol = None
    return jsonify(
        running=True,
        videos=cnt,
        volume=vol,
        state=get_vlc_state_str(),
        current=cur,
        vlc_ready=(_player is not None),
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
    from flask import make_response, send_from_directory
    if not os.path.isfile(os.path.join(HLS_DIR, filename)) and filename != "index.m3u8":
        # on laisse VLC créer les fichiers; si absent -> 404
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
    set_preview_enabled(True)
    clear_hls_dir()
    # recharge le média courant pour (ré)appliquer le sout
    if get_snapshot()[0] > 0:
        try:
            _player.stop()
        except Exception:
            pass
        if set_media_by_index(max(0, min(video_index, len(videos)-1))):
            _player.play()
    return jsonify(ok=True, url="/hls/index.m3u8")

@app.route("/api/preview/disable", methods=["POST"])
def api_preview_disable():
    set_preview_enabled(False)
    # recharge le média courant pour retirer le sout
    if get_snapshot()[0] > 0:
        try:
            _player.stop()
        except Exception:
            pass
        if set_media_by_index(max(0, min(video_index, len(videos)-1))):
            _player.play()
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
        return jsonify(error='Token JSON manquant (utilisez rclone authorize "drive")'), 400

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


