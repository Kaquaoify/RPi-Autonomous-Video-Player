# app/main.py
import os
from . import create_app
from .config import ensure_dirs
from .tasks import start_bootstrap

# Expose l'objet WSGI "app" pour systemd/gunicorn
app = create_app()

def _run():
    """Point d'entrée pour le lancement en mode 'python app/main.py'."""
    # 1) S'assure que tous les dossiers existent
    ensure_dirs()

    # 2) Démarre les tâches de bootstrap (sync rclone, thumbnails, autoplay)
    #    -> non bloquant : crée ses propres threads
    start_bootstrap(app)

    # 3) Lance le serveur Flask
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)

if __name__ == "__main__":
    _run()
