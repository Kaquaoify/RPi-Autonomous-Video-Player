// app/static/js/scripts.js

// ==============================
// Constantes
// ==============================

// Délai max pour fetch (évite les requêtes bloquées)
const FETCH_TIMEOUT_MS = 8000;

// 1x1 PNG transparent (placeholder local, pas de fichier statique requis)
const PLACEHOLDER_THUMB =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";

// ==============================
// Utilitaires
// ==============================

// Log uniforme côté console (filtrable)
function log(...args) {
  console.log("[RPi-AVP]", ...args);
}

// fetch avec timeout (AbortController)
async function fetchWithTimeout(url, options = {}, timeoutMs = FETCH_TIMEOUT_MS) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    clearTimeout(id);
    return res;
  } catch (err) {
    clearTimeout(id);
    throw err;
  }
}

// parse JSON sans casser le flux si la réponse n'est pas JSON
async function parseJsonSafe(res) {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

// ==============================
// API: commandes VLC & lecture
// ==============================

// Envoi d'une action à /control/<action> (play/pause/next/prev/vol)
async function sendAction(action) {
  try {
    const res = await fetchWithTimeout(`/control/${encodeURIComponent(action)}`, { method: "POST" });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      console.error("[control] HTTP", res.status, body);
      return;
    }
    const data = await parseJsonSafe(res);
    log("[control]", action, data);
  } catch (err) {
    console.error("[control][error]", action, err);
  }
}

// Demande de lecture d'une vidéo précise via /play-video
async function playVideo(videoName) {
  log("[play-video] ->", videoName);
  try {
    const res = await fetchWithTimeout("/play-video", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video: videoName }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      console.error("[play-video] HTTP", res.status, body);
      return;
    }
    const data = await parseJsonSafe(res);
    log("[play-video][ok]", data);
  } catch (err) {
    console.error("[play-video][error]", err);
  }
}

// ==============================
// Rattachement des handlers DOM
// ==============================

function attachClickHandlers() {
  // Boutons de contrôle VLC
  document.querySelectorAll(".vlc-btn").forEach((btn) => {
    const action = btn.dataset.action;
    if (!action) return;
    btn.addEventListener("click", () => sendAction(action), { passive: true });
  });

  // Clic sur une carte vidéo → lecture
  document.querySelectorAll(".video-item").forEach((item) => {
    item.addEventListener(
      "click",
      () => {
        const name = item.dataset.name || item.getAttribute("data-name");
        if (name) playVideo(name);
      },
      { passive: true }
    );
  });

  // Fallback pour miniatures cassées → 1x1 transparent
  document.querySelectorAll(".video-thumb").forEach((img) => {
    img.addEventListener("error", () => {
      if (img.dataset.fallbackApplied) return;
      img.dataset.fallbackApplied = "1";
      img.src = PLACEHOLDER_THUMB;
    });
  });

  // Bouton d'accès à la page paramètres
  const btnSettings = document.getElementById("btn-settings");
  if (btnSettings) {
    btnSettings.addEventListener(
      "click",
      () => {
        window.location.href = "/settings";
      },
      { passive: true }
    );
  }
}

// -------- Aperçu HLS (toggle) --------
let hlsInstance = null;

function startHlsPlayback(url) {
  const video = document.getElementById("preview-video");
  if (!video) return;

  // Affiche la vidéo, masque le placeholder
  video.style.display = "block";
  const ph = document.getElementById("vlc-output");
  if (ph) ph.style.display = "none";

  if (window.Hls && Hls.isSupported()) {
    if (hlsInstance) {
      hlsInstance.destroy();
      hlsInstance = null;
    }
    hlsInstance = new Hls({ liveSyncDuration: 4, maxLiveSyncPlaybackRate: 1.0 });
    hlsInstance.loadSource(url);
    hlsInstance.attachMedia(video);
    hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(()=>{}));
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url; // Safari / iOS
    video.play().catch(()=>{});
  } else {
    console.warn("HLS non supporté");
  }
}

function stopHlsPlayback() {
  const video = document.getElementById("preview-video");
  if (!video) return;
  try { video.pause(); } catch {}
  video.removeAttribute("src");
  if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
  video.style.display = "none";
  const ph = document.getElementById("vlc-output");
  if (ph) ph.style.display = "flex";
}

async function refreshPreviewToggleUI() {
  try {
    const r = await fetch("/api/preview/status");
    if (!r.ok) return;
    const s = await r.json();
    const cb = document.getElementById("preview-toggle");
    if (cb) cb.checked = !!s.enabled;
    if (s.enabled) startHlsPlayback(s.url || "/hls/index.m3u8");
    else stopHlsPlayback();
  } catch {}
}

function wirePreviewToggle() {
  const cb = document.getElementById("preview-toggle");
  if (!cb) return;
  cb.addEventListener("change", async () => {
    try {
      if (cb.checked) {
        const r = await fetch("/api/preview/enable", { method: "POST" });
        const s = await r.json().catch(()=>({}));
        startHlsPlayback((s && s.url) || "/hls/index.m3u8");
      } else {
        await fetch("/api/preview/disable", { method: "POST" });
        stopHlsPlayback();
      }
    } catch (e) {
      console.error("preview toggle error:", e);
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  wirePreviewToggle();
  refreshPreviewToggleUI();
});


// Init au chargement du DOM
document.addEventListener("DOMContentLoaded", attachClickHandlers);
