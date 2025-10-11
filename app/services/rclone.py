# app/services/rclone.py
from __future__ import annotations
import os
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import VIDEO_DIR, RCLONE_LOG, RCLONE_LOG_DIR
from .settings_store import get_remote_folder, set_remote_folder

# Nom du remote par défaut (doit exister dans "rclone listremotes")
DEFAULT_REMOTE_NAME = os.environ.get("RCLONE_REMOTE", "gdrive")

_LOCK = threading.RLock()
_sync_thread: Optional[threading.Thread] = None


# ---------------------------
# Utils de base / Intégration
# ---------------------------

def which_rclone() -> Optional[str]:
    """Retourne le chemin de rclone, ou None s'il est introuvable."""
    return shutil.which("rclone")

def rclone_exists() -> bool:
    return which_rclone() is not None

def rclone_env() -> Dict[str, str]:
    """Variables d'environnement à passer à rclone (expandables plus tard si besoin)."""
    env = os.environ.copy()
    # Exemple: env["RCLONE_CONFIG"] = "/home/ubuntu/.config/rclone/rclone.conf"
    return env

def _run(cmd: List[str], capture: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    """Exécute une commande rclone en protégeant l'environnement."""
    return subprocess.run(
        cmd,
        env=rclone_env(),
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
        check=check,
    )


# ---------------------------
# Infos & configuration rclone
# ---------------------------

def rclone_version() -> Dict[str, str]:
    if not rclone_exists():
        return {"available": "no", "version": ""}
    try:
        cp = _run(["rclone", "version"])
        # première ligne type: "rclone v1.67.0"
        line0 = (cp.stdout or "").splitlines()[0] if cp.stdout else ""
        return {"available": "yes", "version": line0.strip()}
    except Exception as e:
        return {"available": "error", "version": str(e)}

def list_remotes() -> List[str]:
    if not rclone_exists():
        return []
    try:
        cp = _run(["rclone", "listremotes"])
        # Chaque remote finit par ":" (ex: "gdrive:\nother:\n")
        remotes = []
        for line in (cp.stdout or "").splitlines():
            line = line.strip()
            if line.endswith(":"):
                remotes.append(line[:-1])
        return remotes
    except Exception:
        return []

def remote_exists(name: str) -> bool:
    return name in list_remotes()

def get_default_remote() -> str:
    """Remote par défaut utilisé pour la sync (ex: gdrive)."""
    remotes = list_remotes()
    if DEFAULT_REMOTE_NAME in remotes:
        return DEFAULT_REMOTE_NAME
    return remotes[0] if remotes else DEFAULT_REMOTE_NAME


# ---------------------------
# CRUD minimal de remote
# ---------------------------

def config_create(name: str, provider: str, **kwargs) -> Dict[str, object]:
    """
    Crée un remote via 'rclone config create <name> <provider> key=value ...'
    Ex: provider='drive', kwargs={'scope': 'drive', 'service_account_file': '/path.json'}
    """
    if not rclone_exists():
        return {"ok": False, "error": "rclone not found"}

    if remote_exists(name):
        return {"ok": False, "error": f"remote '{name}' already exists"}

    args = ["rclone", "config", "create", name, provider]
    for k, v in kwargs.items():
        args.append(f"{k}={v}")

    try:
        cp = _run(args)
        return {"ok": True, "output": cp.stdout}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": e.stdout or str(e)}

def config_delete(name: str) -> Dict[str, object]:
    if not rclone_exists():
        return {"ok": False, "error": "rclone not found"}
    if not remote_exists(name):
        return {"ok": False, "error": f"remote '{name}' not found"}
    try:
        cp = _run(["rclone", "config", "delete", name])
        return {"ok": True, "output": cp.stdout}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": e.stdout or str(e)}

def config_update(name: str, **kwargs) -> Dict[str, object]:
    """
    Met à jour des clés sur un remote.
    Implémentation simple: 'rclone config update name key=value ...'
    """
    if not rclone_exists():
        return {"ok": False, "error": "rclone not found"}
    if not remote_exists(name):
        return {"ok": False, "error": f"remote '{name}' not found"}
    args = ["rclone", "config", "update", name] + [f"{k}={v}" for k, v in kwargs.items()]
    try:
        cp = _run(args)
        return {"ok": True, "output": cp.stdout}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": e.stdout or str(e)}


# ---------------------------
# Sync (blocante et asynchrone)
# ---------------------------

def _ensure_log_dir():
    Path(RCLONE_LOG_DIR).mkdir(parents=True, exist_ok=True)

def _remote_spec(remote: Optional[str], folder: Optional[str]) -> str:
    """Construit 'remote:folder' proprement."""
    r = remote or get_default_remote()
    f = (folder or get_remote_folder()).lstrip("/").rstrip("/")
    return f"{r}:{f}" if f else f"{r}:"

def _sync_blocking(remote: Optional[str], folder: Optional[str], dest_dir: str) -> Tuple[bool, str]:
    """
    Exécute rclone sync de manière BLOQUANTE, écrit un log, retourne (ok, log_path).
    """
    if not rclone_exists():
        return False, "rclone not found"

    _ensure_log_dir()
    remote_spec = _remote_spec(remote, folder)
    dst = dest_dir or VIDEO_DIR

    # Commande rclone
    cmd = [
        "rclone", "sync",
        "--progress",
        "--create-empty-src-dirs",
        "--delete-excluded",
        "--transfers", "4",
        "--checkers", "8",
        "--fast-list",
        "--log-file", RCLONE_LOG,
        "--log-format", "date,time,microseconds,level,caller",
        remote_spec,
        dst,
    ]

    try:
        # On n'intercepte pas la sortie (déjà dans le log-file), mais on capte pour les erreurs
        subprocess.run(cmd, env=rclone_env(), check=True, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return True, RCLONE_LOG
    except subprocess.CalledProcessError as e:
        # L’erreur est aussi dans le log; on renvoie le path pour consultation
        return False, RCLONE_LOG

def sync_blocking(remote: Optional[str] = None, folder: Optional[str] = None, dest_dir: Optional[str] = None) -> Tuple[bool, str]:
    """API publique blocante. Retourne (ok, log_path_or_msg)."""
    return _sync_blocking(remote, folder, dest_dir or VIDEO_DIR)

def _sync_thread_target(remote: Optional[str], folder: Optional[str], dest_dir: str):
    ok, _ = _sync_blocking(remote, folder, dest_dir)
    # Rien d'autre: le front peut lire le log et relancer thumbnails/refresh côté tâches.

def sync_async(remote: Optional[str] = None, folder: Optional[str] = None, dest_dir: Optional[str] = None) -> Dict[str, object]:
    """
    Lance une sync en THREAD si aucune n'est en cours.
    Retourne immédiatement un petit statut.
    """
    global _sync_thread
    with _LOCK:
        if _sync_thread and _sync_thread.is_alive():
            return {"started": False, "status": "running", "log": RCLONE_LOG}

        _ensure_log_dir()
        t = threading.Thread(
            target=_sync_thread_target,
            args=(remote, folder, dest_dir or VIDEO_DIR),
            daemon=True,
            name="rclone-sync",
        )
        _sync_thread = t
        t.start()
        return {"started": True, "status": "started", "log": RCLONE_LOG}

def sync_status() -> Dict[str, object]:
    """Renvoie l'état du thread de sync et le chemin du log."""
    with _LOCK:
        running = bool(_sync_thread and _sync_thread.is_alive())
        return {"running": running, "log": RCLONE_LOG}


# ---------------------------
# Logs & settings de dossier
# ---------------------------

def log_tail(lines: int = 200) -> str:
    """Retourne les dernières lignes du log rclone."""
    p = Path(RCLONE_LOG)
    if not p.exists():
        return ""
    try:
        content = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(content[-max(1, lines):])
    except Exception:
        return ""

def get_remote_folder_setting() -> str:
    return get_remote_folder()

def set_remote_folder_setting(folder: str) -> Dict[str, object]:
    set_remote_folder(folder)
    return {"ok": True, "remote_folder": get_remote_folder()}
