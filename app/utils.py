# app/utils.py
import os
import subprocess
from PIL import Image, ImageColor

# Extensions de fichiers vidéo acceptées (en minuscules)
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".webm")


def refresh_videos_list(video_dir):
    """
    Retourne la liste triée des fichiers vidéo présents dans `video_dir`.
    Ignore le dossier si inexistant.
    """
    if not os.path.isdir(video_dir):
        return []
    files = sorted(
        f for f in os.listdir(video_dir)
        if f.lower().endswith(VIDEO_EXTENSIONS)
    )
    return files


def generate_thumbnails(video_dir, thumb_dir, seek_seconds=5):
    """
    Génère des miniatures PNG (320px de large) dans `thumb_dir` pour
    chaque vidéo de `video_dir`.

    - Essaye d'extraire 1 frame à `seek_seconds` via ffmpeg.
    - Si échec, essaye la première frame.
    - Si encore échec, crée une image grise placeholder.
    - Ne régénère pas les miniatures déjà présentes.
    """
    os.makedirs(thumb_dir, exist_ok=True)
    videos = refresh_videos_list(video_dir)

    for v in videos:
        base, _ = os.path.splitext(v)
        thumb_path = os.path.join(thumb_dir, base + ".png")

        # Déjà générée → on passe
        if os.path.exists(thumb_path):
            continue

        video_path = os.path.join(video_dir, v)
        success = False

        # Tentative 1 : frame à t = seek_seconds
        # NOTE: -ss après -i = seek précis (un peu plus lent), mais suffisant ici.
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-ss", str(int(seek_seconds)), "-vframes", "1",
            "-vf", "scale=320:-1", thumb_path
        ]
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )
            success = True
        except subprocess.CalledProcessError:
            # Tentative 2 : prendre la première frame
            cmd2 = [
                "ffmpeg", "-y", "-i", video_path,
                "-vframes", "1",
                "-vf", "scale=320:-1", thumb_path
            ]
            try:
                subprocess.run(
                    cmd2,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True
                )
                success = True
            except subprocess.CalledProcessError:
                success = False

        # Fallback : image placeholder si ffmpeg a échoué
        if not success:
            img = Image.new("RGB", (320, 180), ImageColor.getrgb("#2a2a2a"))
            img.save(thumb_path)

        print(f"[Thumbnail] {'Created' if success else 'Placeholder'}: {thumb_path}")
