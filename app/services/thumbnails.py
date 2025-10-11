# app/services/thumbnails.py
from __future__ import annotations
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from ..config import THUMB_DIR
from .videos import list_videos

_LOCK = threading.RLock()

# seconds to seek for the thumbnail (avoid black frames)
THUMB_SEEK_SECONDS = 5
# max workers for parallel generation
MAX_WORKERS = 2


def _thumb_path_for(name: str) -> str:
    """Retourne le chemin absolu de la miniature pour un fichier vidéo 'name'."""
    safe = name + ".jpg"
    return os.path.join(THUMB_DIR, safe)


def _ffmpeg_cmd(input_path: str, output_path: str) -> List[str]:
    # Capture 1 frame at t=THUMB_SEEK_SECONDS, scale to 480px width, keep AR, good quality
    return [
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-ss", str(THUMB_SEEK_SECONDS),
        "-i", input_path,
        "-frames:v", "1",
        "-q:v", "2",
        "-vf", "scale=480:-2",
        output_path,
    ]


def _make_placeholder(output_path: str, text: str = "No preview") -> None:
    """Crée une vignette de secours simple (si ffmpeg échoue)."""
    img = Image.new("RGB", (480, 270), color=(24, 24, 28))
    draw = ImageDraw.Draw(img)
    try:
        # Police par défaut; on évite des dépendances système
        font = ImageFont.load_default()
    except Exception:
        font = None
    w, h = draw.textsize(text, font=font)
    draw.text(((480 - w) / 2, (270 - h) / 2), text, fill=(200, 200, 200), font=font)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="JPEG", quality=85)


def _generate_one(video_path: str, name: str) -> Tuple[str, bool, Optional[str]]:
    """
    Génère la miniature pour une vidéo.
    Retourne (thumb_path, created_or_updated, error_msg_or_None).
    """
    thumb_path = _thumb_path_for(name)
    Path(thumb_path).parent.mkdir(parents=True, exist_ok=True)

    # Si la miniature existe et est plus récente que la vidéo → skip
    try:
        if os.path.exists(thumb_path):
            if os.path.getmtime(thumb_path) >= os.path.getmtime(video_path):
                return thumb_path, False, None
    except Exception:
        # Si stat échoue, on tente quand même de régénérer
        pass

    cmd = _ffmpeg_cmd(video_path, thumb_path)
    try:
        subprocess.run(cmd, check=True)
        return thumb_path, True, None
    except Exception as e:
        # Dernière chance : placeholder
        try:
            _make_placeholder(thumb_path)
            return thumb_path, True, f"ffmpeg failed: {e}"
        except Exception as e2:
            return thumb_path, False, f"ffmpeg & placeholder failed: {e} / {e2}"


def ensure_thumbnail(video_name: str, video_path: str) -> Tuple[str, bool, Optional[str]]:
    """Génère une miniature pour cette vidéo si nécessaire."""
    with _LOCK:
        return _generate_one(video_path, video_name)


def generate_all(parallel: bool = True) -> Dict[str, Dict[str, object]]:
    """
    Génère/rafraîchit les miniatures pour toutes les vidéos listées.
    Retourne un dict: {name: {"thumb": path, "updated": bool, "error": str|None}}
    """
    videos = list_videos()  # [{name, path}]
    results: Dict[str, Dict[str, object]] = {}

    if not videos:
        return results

    if parallel:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {
                ex.submit(_generate_one, v["path"], v["name"]): v["name"]
                for v in videos
            }
            for fut in as_completed(futures):
                name = futures[fut]
                thumb, updated, err = fut.result()
                results[name] = {"thumb": thumb, "updated": updated, "error": err}
    else:
        for v in videos:
            thumb, updated, err = _generate_one(v["path"], v["name"])
            results[v["name"]] = {"thumb": thumb, "updated": updated, "error": err}

    return results


def cleanup_orphans() -> List[str]:
    """
    Supprime les miniatures dont la vidéo n'existe plus. Retourne la liste supprimée.
    """
    existing_names = {v["name"] for v in list_videos()}
    removed: List[str] = []
    try:
        for p in Path(THUMB_DIR).glob("*.jpg"):
            # On enlève l'extension .jpg ajoutée
            base = p.name[:-4]
            if base not in existing_names:
                p.unlink(missing_ok=True)
                removed.append(str(p))
    except Exception:
        # On ignore les erreurs de nettoyage
        pass
    return removed
