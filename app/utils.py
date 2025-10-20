# app/utils.py
import os
import subprocess
from PIL import Image, ImageColor


# ==============================
# Constantes
# ==============================
# Extensions de fichiers vidÃ©o acceptÃ©es (en minuscules)
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".webm")

# Largeur cible des miniatures
THUMB_WIDTH = 320

# Couleur placeholder si ffmpeg Ã©choue
THUMB_PLACEHOLDER_COLOR = "#2a2a2a"


# ==============================
# VidÃ©os : listing
# ==============================
def refresh_videos_list(video_dir):
    """
    Retourne la liste trie (insensible  la casse) des fichiers vido prsents
    DANS `video_dir` (non rcursif). Si le dossier n'existe pas  [].

    NOTE: On filtre uniquement des fichiers rguliers dont l'extension est dans
    VIDEO_EXTENSIONS. Les sous-dossiers, fichiers cachs, etc. sont ignors.
    """
    if not os.path.isdir(video_dir):
        return []

    files = []
    try:
        for fname in os.listdir(video_dir):
            if fname.startswith("."):
                continue  # ignore fichiers cachÃ©s
            fpath = os.path.join(video_dir, fname)
            if not os.path.isfile(fpath):
                continue
            if not fname.lower().endswith(VIDEO_EXTENSIONS):
                continue
            files.append(fname)
    except Exception:
        # Si un souci de permission/IO apparaÃ®t, on renvoie au moins ce qu'on a.
        pass

    # Tri alpha insensible Ã  la casse
    files.sort(key=lambda s: s.lower())
    return files


# ==============================
# Miniatures : gÃ©nÃ©ration via ffmpeg (+fallback)
# ==============================
def generate_thumbnails(video_dir, thumb_dir, seek_seconds=5):
    """
    Gnre des miniatures PNG (THUMB_WIDTH px de large) dans `thumb_dir` pour
    chaque vido de `video_dir`.

    - Essaye d'extraire 1 frame  `seek_seconds` via ffmpeg.
    - Si chec, essaye la premire frame.
    - Si encore chec, cre une image grise placeholder.
    - Ne rgnre pas les miniatures dj prsentes.
    - Ne parcourt PAS rcursivement les sous-dossiers (cohrent avec refresh_videos_list).

    Renvoie le nombre de miniatures effectivement cres (hors placeholders).
    """
    os.makedirs(thumb_dir, exist_ok=True)
    videos = refresh_videos_list(video_dir)

    created = 0
    for v in videos:
        base, _ = os.path.splitext(v)
        thumb_path = os.path.join(thumb_dir, base + ".png")

        # DÃ©jÃ  gÃ©nÃ©rÃ©e â†’ on passe
        if os.path.exists(thumb_path):
            continue

        video_path = os.path.join(video_dir, v)
        success = False

        # Tentative 1 : frame Ã  t = seek_seconds
        # NOTE: placer -ss APRÃˆS -i â†’ seek prÃ©cis (un peu plus lent, mais fiable)
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-ss", str(int(seek_seconds)), "-vframes", "1",
            "-vf", f"scale={THUMB_WIDTH}:-1", thumb_path
        ]
        try:
            subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
            )
            success = True
        except subprocess.CalledProcessError:
            # Tentative 2 : prendre la premiÃ¨re frame
            cmd2 = [
                "ffmpeg", "-y", "-i", video_path,
                "-vframes", "1",
                "-vf", f"scale={THUMB_WIDTH}:-1", thumb_path
            ]
            try:
                subprocess.run(
                    cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
                )
                success = True
            except subprocess.CalledProcessError:
                success = False

        # Fallback : image placeholder si ffmpeg a Ã©chouÃ©
        if not success:
            img = Image.new("RGB", (THUMB_WIDTH, 180), ImageColor.getrgb(THUMB_PLACEHOLDER_COLOR))
            img.save(thumb_path)
            print(f"[Thumbnail] Placeholder: {thumb_path}")
        else:
            created += 1
            print(f"[Thumbnail] Created: {thumb_path}")

    return created
