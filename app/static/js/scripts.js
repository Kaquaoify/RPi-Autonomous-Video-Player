function sendAction(action) {
    fetch(`/control/${action}`, { method: 'POST' })
        .then(response => response.json())
        .then(data => console.log(data))
        .catch(err => console.error(err));
}

// Jouer une vidéo depuis l’explorateur
document.querySelectorAll(".video-item").forEach(item => {
    item.addEventListener("click", () => {
        const videoName = item.querySelector(".scrolling-text").innerText;
        fetch("/play-video", {
            method: "POST",
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({video: videoName})
        })
        .then(res => res.json())
        .then(data => console.log(data))
        .catch(err => console.error(err));
    });
});
