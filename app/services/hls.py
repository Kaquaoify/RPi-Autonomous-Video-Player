# app/services/hls.py
from __future__ import annotations
import os
import shutil
from pathlib import Path
from typing import Dict, List

from ..config import HLS_DIR, HLS_INDEX
from .settings_store import is_preview_enabled, set_preview_enabled

SEGMENT_PATTERN = "segment-%08d.ts"

def hls_index_path() -> str:
    return os.path.join(HLS_DIR, HLS_INDEX)

def hls_segment_path() -> str:
    return os.path.join(HLS_DIR, SEGMENT_PATTERN)

def hls_index_url_path() -> str:
    """Chemin URL servi par le blueprint /hls/<file> (pas une URL absolue)."""
    return f"/hls/{HLS_INDEX}"

def clear_hls_dir() -> None:
    """Vide complètement le répertoire HLS (index + segments)."""
    try:
        Path(HLS_DIR).mkdir(parents=True, exist_ok=True)
        for p in Path(HLS_DIR).iterdir():
            if p.is_file():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
    except Exception:
        # Ne jamais bloquer la lecture si nettoyage impossible
        pass

def preview_status() -> Dict[str, object]:
    """Petit état pour l'UI."""
    enabled = is_preview_enabled()
    idx_exists = Path(hls_index_path()).exists()
    return {
        "enabled": enabled,
        "index_file": hls_index_path(),
        "index_exists": idx_exists,
        "index_url": hls_index_url_path() if enabled else None,
        "dir": HLS_DIR,
    }

def enable_preview() -> Dict[str, object]:
    """Active l’aperçu HLS et nettoie le dossier HLS."""
    set_preview_enabled(True)
    clear_hls_dir()
    return preview_status()

def disable_preview() -> Dict[str, object]:
    """Désactive l’aperçu HLS et nettoie le dossier HLS."""
    set_preview_enabled(False)
    clear_hls_dir()
    return preview_status()

def vlc_sout_args_for_preview() -> List[str]:
    """
    Construit les options VLC pour dupliquer la sortie vers HLS
    tout en conservant l'affichage local (HDMI).
    À passer à media.add_option(...) dans vlc_player.
    """
    if not is_preview_enabled():
        return []

    index_file = hls_index_path()
    seg_file   = hls_segment_path()

    # Important: le "display" conserve la sortie locale (HDMI),
    # le second dst envoie vers livehttp (HLS) dans HLS_DIR.
    # index-url est l’URL servie par Flask (/hls/xxx).
    sout = (
        "#duplicate{{"
        "dst=display,"
        "dst=std{{"
        "access=livehttp{{"
        "seglen=2,"
        "delsegs=true,"
        "numsegs=6,"
        "index={index},"
        "index-url={index_url},"
        "}},"  # fin livehttp
        "mux=ts{{use-key-frames}},"
        "dst={segment}"
        "}}"
        "}}"
    ).format(
        index=index_file,
        index_url=hls_index_url_path(),
        segment=seg_file,
    )

    # Les options VLC doivent commencer par ':'
    return [f":sout={sout}", ":sout-all", ":sout-keep"]
