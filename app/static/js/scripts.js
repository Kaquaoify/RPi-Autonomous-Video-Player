// app/static/js/scripts.js

// Envoi des actions VLC
function sendAction(action) {
    fetch(`/control/${action}`, { method: 'POST' })
        .then(response => response.json())
        .then(data => console.log(data))
        .catch(err => console.error(err));
}

// Jouer une vidéo depuis l’explorateur
function playVideo(videoName) {
    fetch("/play-video", {
        method: "POST",
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({video: videoName})
    })
    .then(res => res.json())
    .then(data => console.log(data))
    .catch(err => console.error(err));
}

// Attacher tous les événements après le DOM chargé
function attachClickHandlers() {
    // Contrôles VLC
    document.querySelectorAll('.vlc-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            sendAction(btn.dataset.action);
        });
    });

    // Explorateur vidéo
    document.querySelectorAll('.video-item').forEach(item => {
        item.addEventListener('click', () => {
            playVideo(item.dataset.name);
        });
    });

    // Fallback pour images manquantes
    document.querySelectorAll('.video-thumb').forEach(img => {
        img.addEventListener('error', () => {
            img.src = "{{ url_for('static', filename='img/placeholder.png') }}";
        });
    });

    // Bouton paramètres
    const btnSettings = document.getElementById('btn-settings');
    if (btnSettings) {
        btnSettings.addEventListener('click', () => {
            window.location.href = "/settings";
        });
    }
}

// Initialisation
document.addEventListener("DOMContentLoaded", attachClickHandlers);
