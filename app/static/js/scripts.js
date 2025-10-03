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

// Init au chargement du DOM
document.addEventListener("DOMContentLoaded", attachClickHandlers);
