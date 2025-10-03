# app/utils.py
import os
import subprocess
from PIL import Image, ImageColor

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".webm")

def refresh_videos_list(video_dir):
    """
    Returns a sorted list of video filenames in video_dir.
    """
    if not os.path.isdir(video_dir):
        return []
    files = sorted([f for f in os.listdir(video_dir)
                    if f.lower().endswith(VIDEO_EXTENSIONS)])
    return files

def generate_thumbnails(video_dir, thumb_dir, seek_seconds=5):
    """
    Generate thumbnails (png) for each video in video_dir into thumb_dir.
    Extracts a frame at `seek_seconds` when possible.
    Skips thumbnails that already exist.
    Requires ffmpeg installed on the system.
    """
    os.makedirs(thumb_dir, exist_ok=True)
    videos = refresh_videos_list(video_dir)

    for v in videos:
        base, _ = os.path.splitext(v)
        thumb_path = os.path.join(thumb_dir, base + ".png")
        if os.path.exists(thumb_path):
            continue

        video_path = os.path.join(video_dir, v)
        success = False

        # Try ffmpeg at seek_seconds
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-ss", str(int(seek_seconds)), "-vframes", "1",
            "-vf", "scale=320:-1", thumb_path
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            success = True
        except subprocess.CalledProcessError:
            # Fallback: first frame
            cmd2 = ["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-vf", "scale=320:-1", thumb_path]
            try:
                subprocess.run(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                success = True
            except subprocess.CalledProcessError:
                success = False

        # If ffmpeg fails, create placeholder image
        if not success:
            img = Image.new("RGB", (320, 180), ImageColor.getrgb("#2a2a2a"))
            img.save(thumb_path)

        print(f"[Thumbnail] {'Created' if success else 'Placeholder'}: {thumb_path}")
