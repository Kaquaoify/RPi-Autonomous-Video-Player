# app/services/videos.py
from __future__ import annotations
import os
import threading
from typing import List, Dict, Optional, Tuple
from pathlib import Path

from ..config import VIDEO_DIR

# Extensions prises en charge (tu peux en ajouter)
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm"}

_lock = threading.RLock()

# État en mémoire
_videos: List[str] = []          # chemins absolus
_names: List[str] = []           # noms de fichiers (affichage)
_current_idx: int = -1           # -1 = rien
_last_scan_sig: Tuple[int, int] | None = None  # (nb_fichiers, somme_timestamps)


def _scan_signature() -> Tuple[int, int]:
    """Retourne une petite signature du répertoire pour éviter des refresh inutiles."""
    count = 0
    tsum = 0
    vdir = Path(VIDEO_DIR)
    if not vdir.exists():
        return (0, 0)
    for p in vdir.iterdir():
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            count += 1
            try:
                tsum += int(p.stat().st_mtime)
            except Exception:
                pass
    return (count, tsum)


def _collect_videos() -> Tuple[List[str], List[str]]:
    vdir = Path(VIDEO_DIR)
    files = []
    if vdir.exists():
        for p in vdir.iterdir():
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                files.append(str(p.resolve()))
    # Tri alpha “case-insensitive”
    files.sort(key=lambda s: os.path.basename(s).lower())
    names = [os.path.basename(f) for f in files]
    return files, names


def refresh_videos(force: bool = False) -> Dict[str, int]:
    """
    Rafraîchit la liste des vidéos depuis VIDEO_DIR.
    - Évite le travail si la signature n'a pas changé (sauf force=True).
    - Conserve au mieux l'index courant (par nom).
    """
    global _videos, _names, _current_idx, _last_scan_sig
    with _lock:
        sig = _scan_signature()
        if not force and sig == _last_scan_sig and _videos:
            return {"count": len(_videos), "index": _current_idx}

        old_name = _names[_current_idx] if 0 <= _current_idx < len(_names) else None

        files, names = _collect_videos()
        _videos, _names = files, names
        _last_scan_sig = sig

        if not _videos:
            _current_idx = -1
        else:
            # Essaie de retomber sur le même fichier qu'avant
            if old_name and old_name in _names:
                _current_idx = _names.index(old_name)
            else:
                # Sinon, démarre au premier
                _current_idx = 0

        return {"count": len(_videos), "index": _current_idx}


def list_videos() -> List[Dict[str, str]]:
    """Retourne la liste [{name, path}] triée."""
    with _lock:
        return [{"name": n, "path": p} for n, p in zip(_names, _videos)]


def has_videos() -> bool:
    with _lock:
        return len(_videos) > 0


def get_current_index() -> int:
    with _lock:
        return _current_idx


def get_count() -> int:
    with _lock:
        return len(_videos)


def set_index(idx: int) -> int:
    """Fixe l’index courant en le bornant correctement. Retourne l’index effectif."""
    global _current_idx
    with _lock:
        if not _videos:
            _current_idx = -1
            return -1
        _current_idx = max(0, min(idx, len(_videos) - 1))
        return _current_idx


def set_index_by_name(filename: str) -> int:
    """Positionne l’index sur le fichier 'filename' (nom seul). Retourne l’index ou -1."""
    with _lock:
        if not _videos:
            return -1
        try:
            idx = _names.index(filename)
        except ValueError:
            return -1
        return set_index(idx)


def next_index(loop_all: bool = True) -> int:
    """Calcule l’index suivant (avec boucle si loop_all=True)."""
    with _lock:
        if not _videos:
            return -1
        if _current_idx < len(_videos) - 1:
            return _current_idx + 1
        return 0 if loop_all else _current_idx


def prev_index(loop_all: bool = True) -> int:
    """Calcule l’index précédent (avec boucle si loop_all=True)."""
    with _lock:
        if not _videos:
            return -1
        if _current_idx > 0:
            return _current_idx - 1
        return len(_videos) - 1 if loop_all else _current_idx


def select_next(loop_all: bool = True) -> int:
    """Applique le passage à la vidéo suivante. Retourne l'index."""
    return set_index(next_index(loop_all=loop_all))


def select_prev(loop_all: bool = True) -> int:
    """Applique le passage à la vidéo précédente. Retourne l'index."""
    return set_index(prev_index(loop_all=loop_all))


def get_video_path(idx: Optional[int] = None) -> Optional[str]:
    """Retourne le chemin absolu de la vidéo à 'idx' (ou de l'index courant si None)."""
    with _lock:
        if not _videos:
            return None
        i = _current_idx if idx is None else idx
        if 0 <= i < len(_videos):
            return _videos[i]
        return None


def get_video_name(idx: Optional[int] = None) -> Optional[str]:
    """Retourne le nom de fichier (affichage) à 'idx' (ou courant)."""
    with _lock:
        if not _videos:
            return None
        i = _current_idx if idx is None else idx
        if 0 <= i < len(_names):
            return _names[i]
        return None


def snapshot() -> Dict[str, object]:
    """Petit snapshot pour l’UI/Status."""
    with _lock:
        return {
            "count": len(_videos),
            "index": _current_idx,
            "current_name": _names[_current_idx] if 0 <= _current_idx < len(_names) else None,
            "video_dir": VIDEO_DIR,
        }
