# app/utils.py
import os
import subprocess
from PIL import Image, ImageColor


# ==============================
# Constantes
# ==============================
# Extensions de fichiers vidéo acceptées (en minuscules)
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".webm")

# Largeur cible des miniatures
THUMB_WIDTH = 320

# Couleur placeholder si ffmpeg échoue
THUMB_PLACEHOLDER_COLOR = "#2a2a2a"


# ==============================
# Vidéos : listing
# ==============================
def refresh_videos_list(video_dir):
    """
    Retourne la liste triée (insensible à la casse) des fichiers vidéo présents
    DANS `video_dir` (non récursif). Si le dossier n'existe pas → [].

    NOTE: On filtre uniquement des fichiers réguliers dont l'extension est dans
    VIDEO_EXTENSIONS. Les sous-dossiers, fichiers cachés, etc. sont ignorés.
    """
    if not os.path.isdir(video_dir):
        return []

    files = []
    try:
        for fname in os.listdir(video_dir):
            if fname.startswith("."):
                continue  # ignore fichiers cachés
            fpath = os.path.join(video_dir, fname)
            if not os.path.isfile(fpath):
                continue
            if not fname.lower().endswith(VIDEO_EXTENSIONS):
                continue
            files.append(fname)
    except Exception:
        # Si un souci de permission/IO apparaît, on renvoie au moins ce qu'on a.
        pass

    # Tri alpha insensible à la casse
    files.sort(key=lambda s: s.lower())
    return files


# ==============================
# Miniatures : génération via ffmpeg (+fallback)
# ==============================
def generate_thumbnails(video_dir, thumb_dir, seek_seconds=5):
    """
    Génère des miniatures PNG (THUMB_WIDTH px de large) dans `thumb_dir` pour
    chaque vidéo de `video_dir`.

    - Essaye d'extraire 1 frame à `seek_seconds` via ffmpeg.
    - Si échec, essaye la première frame.
    - Si encore échec, crée une image grise placeholder.
    - Ne régénère pas les miniatures déjà présentes.
    - Ne parcourt PAS récursivement les sous-dossiers (cohérent avec refresh_videos_list).

    Renvoie le nombre de miniatures effectivement créées (hors placeholders).
    """
    os.makedirs(thumb_dir, exist_ok=True)
    videos = refresh_videos_list(video_dir)

    created = 0
    for v in videos:
        base, _ = os.path.splitext(v)
        thumb_path = os.path.join(thumb_dir, base + ".png")

        # Déjà générée → on passe
        if os.path.exists(thumb_path):
            continue

        video_path = os.path.join(video_dir, v)
        success = False

        # Tentative 1 : frame à t = seek_seconds
        # NOTE: placer -ss APRÈS -i → seek précis (un peu plus lent, mais fiable)
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
            # Tentative 2 : prendre la première frame
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

        # Fallback : image placeholder si ffmpeg a échoué
        if not success:
            img = Image.new("RGB", (THUMB_WIDTH, 180), ImageColor.getrgb(THUMB_PLACEHOLDER_COLOR))
            img.save(thumb_path)
            print(f"[Thumbnail] Placeholder: {thumb_path}")
        else:
            created += 1
            print(f"[Thumbnail] Created: {thumb_path}")

    return created
