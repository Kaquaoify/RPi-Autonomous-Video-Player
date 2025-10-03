// app/static/js/scripts.js

// --- Constantes ---
const FETCH_TIMEOUT_MS = 8000;
// 1x1 PNG transparent (évite d'avoir un fichier placeholder sur le disque)
const PLACEHOLDER_THUMB =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";

// --- Utils ---
function log(...args) {
  console.log("[RPi-AVP]", ...args);
}

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

async function parseJsonSafe(res) {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

// --- API ---
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

// --- DOM wiring ---
function attachClickHandlers() {
  // Contrôles VLC
  document.querySelectorAll(".vlc-btn").forEach((btn) => {
    const action = btn.dataset.action;
    if (!action) return;
    btn.addEventListener("click", () => sendAction(action), { passive: true });
  });

  // Explorateur vidéo (click sur la carte)
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

  // Fallback pour images manquantes (pas de Jinja ici)
  document.querySelectorAll(".video-thumb").forEach((img) => {
    img.addEventListener("error", () => {
      if (img.dataset.fallbackApplied) return;
      img.dataset.fallbackApplied = "1";
      img.src = PLACEHOLDER_THUMB;
    });
  });

  // Bouton paramètres
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

document.addEventListener("DOMContentLoaded", attachClickHandlers);
