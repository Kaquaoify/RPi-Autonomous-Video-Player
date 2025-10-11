# app/__init__.py
from flask import Flask
from .config import TEMPLATE_DIR, STATIC_DIR

def create_app() -> Flask:
    """
    Crée et configure l'application Flask, enregistre les blueprints.
    Aucune logique métier ici (pas de VLC, pas de threads).
    """
    app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

    # Import tardif pour éviter les imports circulaires pendant l'initialisation
    from .routes.ui import ui_bp
    from .routes.api_vlc import api_vlc_bp
    from .routes.api_status import api_status_bp
    from .routes.api_preview import api_preview_bp
    from .routes.api_rclone import api_rclone_bp
    from .routes.files import files_bp

    # Pages
    app.register_blueprint(ui_bp)

    # APIs
    app.register_blueprint(api_vlc_bp,    url_prefix="/control")
    app.register_blueprint(api_status_bp)                 # /status, /status_min, /health
    app.register_blueprint(api_preview_bp, url_prefix="/api/preview")
    app.register_blueprint(api_rclone_bp,  url_prefix="/api/rclone")
    app.register_blueprint(files_bp)                      # /thumbnails/<f>, /hls/<f>

    # Config Flask optionnelle
    app.config["JSON_SORT_KEYS"] = False

    return app
