import os
from flask import Flask
from .blueprints.legacy import bp as legacy_bp
from .services.settings import SettingsService
from .services.preview import PreviewService
from .services.rclone import RcloneService

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Paths (match legacy defaults)
    user_home = os.path.expanduser("~")
    video_dir = os.path.join(user_home, "Videos", "RPi-Autonomous-Video-Player")
    thumb_dir = os.path.join(video_dir, "thumbnails")
    hls_dir = os.path.join(user_home, ".local", "share", "rpi-avp", "hls")
    hls_index = os.path.join(hls_dir, "index.m3u8")

    # Ensure directories exist where appropriate
    os.makedirs(video_dir, exist_ok=True)
    os.makedirs(thumb_dir, exist_ok=True)
    os.makedirs(hls_dir, exist_ok=True)

    # Services
    settings_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")
    settings = SettingsService(settings_path)
    preview = PreviewService(settings, hls_dir=hls_dir, hls_index=hls_index)
    # Align rclone logs directory with legacy path (no extra 'logs' subdir)
    rclone_logs = os.path.join(user_home, ".local", "share", "rpi-avp")
    rclone = RcloneService(settings, video_dir=video_dir, log_dir=rclone_logs)

    app.extensions.setdefault("services", {})
    app.extensions["services"].update({
        "settings": settings,
        "preview": preview,
        "rclone": rclone,
        # Potential future services (player, library, thumbnails) can be added here.
    })
    app.extensions.setdefault("paths", {})
    app.extensions["paths"].update({
        "VIDEO_DIR": video_dir,
        "THUMB_DIR": thumb_dir,
        "HLS_DIR": hls_dir,
        "HLS_INDEX": hls_index,
    })

    app.register_blueprint(legacy_bp)
    return app
