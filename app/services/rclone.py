import json
import os
import subprocess
import time
import logging
import shutil
from typing import Tuple, List, Optional

try:
    from flask import current_app
    _svc_logger = current_app.logger
except Exception:
    _svc_logger = logging.getLogger('rpi_avp')


class RcloneService:
    """rclone orchestration: check/install/config/sync and log tailing."""

    def __init__(self, settings_service, video_dir: str, log_dir: str):
        self._settings = settings_service
        self.video_dir = video_dir
        self.log_dir = log_dir
        # Align with legacy filename for continuity
        self.log_path = os.path.join(self.log_dir, "rclone_sync.log")

    # ----- helpers -----
    def which_rclone(self) -> Optional[str]:
        for exe in ("rclone", "/usr/bin/rclone", "/usr/local/bin/rclone"):
            if shutil.which(exe):
                return exe
        return None

    def rclone_conf_path(self) -> str:
        home = os.path.expanduser("~")
        return os.path.join(home, ".config", "rclone", "rclone.conf")

    def rclone_base_env(self) -> dict:
        env = os.environ.copy()
        # Align with legacy expectations for systemd environment
        home = os.path.expanduser("~")
        env["HOME"] = home
        env.setdefault("RCLONE_CONFIG_DIR", os.path.dirname(self.rclone_conf_path()))
        return env

    # ----- API-like operations -----
    def check(self) -> dict:
        rc = self.which_rclone()
        result = {"which": rc, "version": None, "remotes": []}
        if not rc:
            return result
        try:
            out = subprocess.check_output([rc, "version"], text=True, env=self.rclone_base_env())
            result["version"] = (out.splitlines()[0] if out else None)
        except Exception as e:
            _svc_logger.warning("rclone version failed: %s", e)
        try:
            out = subprocess.check_output([rc, "listremotes"], text=True, env=self.rclone_base_env())
            result["remotes"] = [x.strip().rstrip(":") for x in out.splitlines() if x.strip()]
        except Exception as e:
            _svc_logger.warning("rclone listremotes failed: %s", e)
        return result

    def install(self) -> Tuple[bool, str]:
        # Best effort: run upstream install script; requires sudo when called under system user.
        # Caller should handle permissions/UX.
        try:
            p = subprocess.run(
                ["bash", "-lc", "curl -fsSL https://rclone.org/install.sh | sudo bash"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            return (p.returncode == 0), p.stdout or ""
        except Exception as e:
            return False, f"install error: {e}"

    def save_settings(self, remote_name: Optional[str], remote_folder: Optional[str]) -> dict:
        data = {}
        if remote_name is not None:
            data["remote_name"] = remote_name
        if remote_folder is not None:
            data["remote_folder"] = remote_folder
        return self._settings.set(**data)

    def create_remote(self, remote_name: str, token_json: str, scope: str = "drive") -> Tuple[bool, str]:
        rc = self.which_rclone()
        if not rc:
            return False, "rclone non installé"
        try:
            token = json.loads(token_json)
        except Exception as e:
            return False, f"token JSON invalide: {e}"

        # Configure via `rclone config create` with pre-authorized token
        name = (remote_name or "gdrive").strip()
        args = [rc, "config", "create", name, scope, "token", json.dumps(token)]
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=self.rclone_base_env(), timeout=60)
        ok = (p.returncode == 0)
        return ok, p.stdout or ""

    def test_list(self, remote_name: str, remote_folder: str) -> Tuple[bool, str]:
        rc = self.which_rclone()
        if not rc:
            return False, "rclone non installé"
        target = f"{remote_name}:{remote_folder}" if remote_folder else f"{remote_name}:"
        p = subprocess.run([rc, "lsd", target], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=self.rclone_base_env(), timeout=60)
        return (p.returncode == 0), (p.stdout or "")

    def sync_async(self, remote_name: str, remote_folder: str) -> None:
        rc = self.which_rclone()
        if not rc:
            raise RuntimeError("rclone non installé")
        os.makedirs(self.log_dir, exist_ok=True)
        target = f"{remote_name}:{remote_folder}" if remote_folder else f"{remote_name}:"

        def _run():
            try:
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write(f"\n--- sync started {time.ctime()} ---\n")
                    cmd = [rc, "sync", target, self.video_dir, "--delete-during", "--fast-list"]
                    p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=self.rclone_base_env())
                    p.communicate()
                    fh.write(f"--- sync finished {time.ctime()} exit={p.returncode} ---\n")
            except Exception as e:
                try:
                    with open(self.log_path, "a", encoding="utf-8") as fh:
                        fh.write(f"ERROR: {type(e).__name__}: {e}\n")
                except Exception:
                    pass

        import threading
        threading.Thread(target=_run, daemon=True).start()

    def sync_blocking(self, remote_name: str, remote_folder: str) -> Tuple[bool, int]:
        """Run rclone sync blocking and log to the service log path.
        Returns (ok, returncode)."""
        rc = self.which_rclone()
        if not rc:
            return False, 127
        os.makedirs(self.log_dir, exist_ok=True)
        target = f"{remote_name}:{remote_folder}" if remote_folder else f"{remote_name}:"
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n--- sync started {time.ctime()} ---\n")
                cmd = [rc, "sync", target, self.video_dir, "--delete-during", "--fast-list"]
                p = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True, env=self.rclone_base_env())
                fh.write(f"--- sync finished {time.ctime()} exit={p.returncode} ---\n")
                return (p.returncode == 0), p.returncode
        except Exception:
            try:
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write("ERROR: exception during sync\n")
            except Exception:
                pass
            return False, 1

    def sync_blocking_from_settings(self) -> Tuple[bool, str]:
        rc = self.which_rclone()
        if not rc:
            return False, "rclone non installé"
        rn = (self._settings.get("remote_name", "gdrive") or "gdrive").strip()
        rf = (self._settings.get("remote_folder", "VideosRPi") or "VideosRPi").strip()
        target = f"{rn}:{rf}" if rf else f"{rn}:"
        os.makedirs(self.log_dir, exist_ok=True)
        banner = f"--- boot sync {time.ctime()} -> {target} ---\n"
        ok = False
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(banner)
                p = subprocess.run([rc, "sync", target, self.video_dir, "--delete-during", "--fast-list"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=self.rclone_base_env())
                fh.write(p.stdout or "")
                fh.write(f"--- boot sync done rc={p.returncode} ---\n")
                ok = (p.returncode == 0)
        except Exception as e:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(f"ERROR boot sync: {type(e).__name__}: {e}\n")
        return ok, ("OK" if ok else "Échec")

    def delete_remote(self, remote_name: str) -> Tuple[bool, str]:
        rc = self.which_rclone()
        if not rc:
            return False, "rclone non installé"
        p = subprocess.run([rc, "config", "delete", remote_name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=self.rclone_base_env(), timeout=60)
        return (p.returncode == 0), (p.stdout or "")

    def list_remotes(self) -> List[str]:
        rc = self.which_rclone()
        if not rc:
            return []
        try:
            out = subprocess.check_output([rc, "listremotes"], text=True, env=self.rclone_base_env())
            return [x.strip().rstrip(":") for x in out.splitlines() if x.strip()]
        except Exception:
            return []

    def create_or_update_remote(
        self,
        remote_name: str,
        token_json: str,
        scope: str = "drive",
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Create or update a Drive remote using a pre-authorized token JSON.
        Mirrors legacy behavior (create if missing, otherwise update).
        """
        rc = self.which_rclone()
        if not rc:
            return False, "rclone non installé"
        try:
            token_obj = json.loads(token_json)
            token_min = json.dumps(token_obj, ensure_ascii=False)
        except Exception as e:
            return False, f"token JSON invalide: {e}"

        rn = (remote_name or "gdrive").strip()
        existing = set(self.list_remotes())
        exists = rn in existing

        base = [rc, "config", "update" if exists else "create", "--non-interactive", "--auto-confirm", rn]
        if not exists:
            base.append("drive")
        kv = [f"scope={scope}", f"token={token_min}"]
        if client_id:
            kv.append(f"client_id={client_id}")
        if client_secret:
            kv.append(f"client_secret={client_secret}")

        p = subprocess.run(base + kv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           env=self.rclone_base_env(), timeout=180)
        if p.returncode == 0:
            return True, (p.stdout or "")

        # If create failed because it exists, try update once
        msg = (p.stdout or "").lower()
        if not exists and ("exist" in msg or "already" in msg):
            p2 = subprocess.run([rc, "config", "update", "--non-interactive", "--auto-confirm", rn] + kv,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                env=self.rclone_base_env(), timeout=120)
            return (p2.returncode == 0), (p2.stdout or "")
        return False, (p.stdout or "")

    def tail_log(self, tail: int = 200) -> str:
        if not os.path.isfile(self.log_path):
            return "(aucun log pour le moment)\n"
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return "".join(lines[-tail:]) if tail > 0 else "".join(lines)
        except Exception as e:
            return f"Erreur lecture log: {e}\n"
