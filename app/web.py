from flask import Flask, render_template, jsonify, request
import os
import subprocess

app = Flask(__name__)

# ==============================
# Variables configurables pour multi-Pi
# ==============================
USER_HOME = os.path.expanduser("~")
INSTALL_DIR = os.path.join(USER_HOME, "RPi-Autonomous-Video-Player")
VIDEO_DIR = os.path.join(USER_HOME, "Videos", "RPi-Autonomous-Video-Player")
REMOTE_NAME = "gdrive"
REMOTE_FOLDER = "VideosRPi"  # Nom du dossier sur Google Drive

# ==============================
# Routes Flask
# ==============================
@app.route("/")
def index():
    # Liste des vidéos locales
    videos = [f for f in os.listdir(VIDEO_DIR) if f.lower().endswith((".mp4", ".mkv"))]
    return render_template("index.html", videos=videos)

@app.route("/control/<action>", methods=["POST"])
def control(action):
    """
    Endpoints pour les commandes VLC :
    - play, pause, next, prev, volup, voldown
    """
    # Pour l'instant juste un print, on intégrera VLC via dbus ou interface web
    print(f"Action VLC reçue: {action}")
    return jsonify({"status": "ok", "action": action})

@app.route("/sync", methods=["POST"])
def sync_videos():
    """
    Synchronise le dossier Google Drive local
    """
    cmd = ["rclone", "sync", f"{REMOTE_NAME}:{REMOTE_FOLDER}", VIDEO_DIR]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return f"<pre>{result.stdout}\n{result.stderr}</pre><a href='/'>Retour</a>"

# ==============================
# Main
# ==============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
