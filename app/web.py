from flask import Flask, render_template, request, jsonify
import vlc
import os

app = Flask(__name__)

VIDEO_DIR = os.path.expanduser("~/Videos/RPi-Autonomous-Video-Player")
videos = [f for f in os.listdir(VIDEO_DIR) if f.endswith((".mp4", ".mkv", ".avi"))]

player = vlc.MediaPlayer()
video_index = 0  # pour next/prev

@app.route("/")
def index():
    return render_template("index.html", videos=videos)

# Route pour les contrôles (play/pause/next/prev/volup/voldown)
@app.route("/control/<action>", methods=["POST"])
def control(action):
    global video_index
    if action == "play":
        player.play()
    elif action == "pause":
        player.pause()
    elif action == "next":
        video_index = (video_index + 1) % len(videos)
        player.set_media(vlc.Media(os.path.join(VIDEO_DIR, videos[video_index])))
        player.play()
    elif action == "prev":
        video_index = (video_index - 1) % len(videos)
        player.set_media(vlc.Media(os.path.join(VIDEO_DIR, videos[video_index])))
        player.play()
    elif action == "volup":
        player.audio_set_volume(min(player.audio_get_volume()+10, 100))
    elif action == "voldown":
        player.audio_set_volume(max(player.audio_get_volume()-10, 0))
    return jsonify({"status": "ok", "action": action})

# Route pour lancer une vidéo depuis l'explorateur
@app.route("/play-video", methods=["POST"])
def play_video():
    global video_index
    video_name = request.json.get("video")
    video_path = os.path.join(VIDEO_DIR, video_name)
    if os.path.exists(video_path):
        player.set_media(vlc.Media(video_path))
        player.play()
        video_index = videos.index(video_name)
        return jsonify({"status": "playing", "video": video_name})
    return jsonify({"status": "error", "message": "Fichier introuvable"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
