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

function log(...args) { console.log("[RPi-AVP]", ...args); }

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
  try { return await res.json(); } catch { return null; }
}

// ==============================
// API: commandes VLC & lecture
// ==============================

async function sendAction(action) {
  try {
    const res = await fetchWithTimeout(`/control/${encodeURIComponent(action)}`, { method: "POST" });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      console.error("[control] HTTP", res.status, body);
      return null;
    }
    const data = await parseJsonSafe(res);
    log("[control]", action, data);
    return data;
  } catch (err) {
    console.error("[control][error]", action, err);
    return null;
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

// ==============================
// Rattachement des handlers DOM
// ==============================

function attachClickHandlers() {
  // Boutons de contrôle VLC (sauf Play/Pause intelligent)
  document.querySelectorAll(".vlc-btn").forEach((btn) => {
    if (btn.id === "btn-playpause") return; // géré séparément
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
    btnSettings.addEventListener("click", () => { window.location.href = "/settings"; }, { passive: true });
  }

  // Bouton Play/Pause intelligent
  const btnPP = document.getElementById("btn-playpause");
  if (btnPP) {
    btnPP.addEventListener("click", togglePlayPause, { passive: true });
  }
}

// -------- Aperçu HLS (toggle + volume local) --------
let hlsInstance = null;

function showPreviewUI(show) {
  const wrap = document.getElementById("preview-wrap");
  const ph = document.getElementById("vlc-output");
  if (!wrap || !ph) return;
  wrap.style.display = show ? "block" : "none";
  ph.style.display = show ? "none" : "flex";
}

function applyVideoNoControls(video) {
  video.controls = false;
  video.setAttribute("disablepictureinpicture", "");
  video.setAttribute("disableremoteplayback", "");
  video.setAttribute("controlslist", "nodownload noplaybackrate nofullscreen");
  video.addEventListener("contextmenu", (e) => e.preventDefault());
}

function initLocalVolumeUI(video) {
  const slider = document.getElementById("preview-vol");
  if (!slider || !video) return;

  const saved = Number(localStorage.getItem("previewVolume") || "0");
  const vol0to1 = Math.min(1, Math.max(0, saved / 100));
  video.volume = vol0to1;
  video.muted = vol0to1 === 0;
  slider.value = String(Math.round(vol0to1 * 100));

  slider.addEventListener("input", () => {
    const v = Number(slider.value || "0");
    const f = Math.min(1, Math.max(0, v / 100));
    video.volume = f;
    video.muted = v === 0;
    localStorage.setItem("previewVolume", String(v));
  });
}

function startHlsPlayback(url) {
  const video = document.getElementById("preview-video");
  if (!video) return;

  showPreviewUI(true);
  applyVideoNoControls(video);
  initLocalVolumeUI(video);

  if (window.Hls && Hls.isSupported()) {
    if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
    hlsInstance = new Hls({ liveSyncDuration: 4, maxLiveSyncPlaybackRate: 1.0 });
    hlsInstance.loadSource(url);
    hlsInstance.attachMedia(video);
    hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => { video.play().catch(() => {}); });
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;
    video.play().catch(() => {});
  } else {
    console.warn("HLS non supporté par ce navigateur");
  }
}

function stopHlsPlayback() {
  const video = document.getElementById("preview-video");
  if (video) {
    try { video.pause(); } catch {}
    video.removeAttribute("src");
  }
  if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
  showPreviewUI(false);
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
        const s = await r.json().catch(() => ({}));
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

// ===== Bouton Play/Pause intelligent =====

function updatePlayPauseUI(isPlaying) {
  const btn = document.getElementById("btn-playpause");
  if (!btn) return;
  const icon = btn.querySelector("i");
  btn.dataset.action = isPlaying ? "pause" : "play";
  icon.className = `fa-solid fa-${isPlaying ? "pause" : "play"}`;
  btn.title = isPlaying ? "Pause" : "Lecture";
  btn.classList.toggle("active", isPlaying);
}

async function getIsPlaying() {
  try {
    const r = await fetchWithTimeout("/status", { method: "GET" });
    if (!r.ok) return false;
    const s = await r.json();
    return s.state === "playing";
  } catch { return false; }
}

async function togglePlayPause() {
  const btn = document.getElementById("btn-playpause");
  if (!btn) return;

  // 1) Lis l'état réel
  const isPlayingNow = await getIsPlaying();
  const action = isPlayingNow ? "pause" : "play";

  // 2) Envoie la commande correspondante
  await sendAction(action);

  // 3) Petite anim' puis resync avec l'état réel (évite tout décalage)
  btn.classList.add("switching");
  setTimeout(async () => {
    const isPlayingAfter = await getIsPlaying();
    updatePlayPauseUI(isPlayingAfter);
    btn.classList.remove("switching");
  }, 200);
}

async function syncPlayButton() {
  try {
    const r = await fetchWithTimeout('/status', { method: 'GET' });
    if (!r.ok) return;
    const s = await r.json();
    updatePlayPauseUI(s.state === 'playing');
    updateStatusPanelPayload(s);
  } catch {}
}

function updateStatusPanelPayload(s){
  const t = document.getElementById('s-title');
  const st = document.getElementById('s-state');
  const v = document.getElementById('s-vol');
  if (t) t.textContent = `🎬 ${s.current ?? '—'}`;
  if (st) st.textContent = `▶ ${s.state ?? '—'}`;
  if (v) v.textContent = `🔊 ${s.volume ?? '—'}`;
}


// ==============================
// Initialisation
// ==============================

document.addEventListener("DOMContentLoaded", () => {
  // UI Aperçu HLS
  wirePreviewToggle();
  refreshPreviewToggleUI();

  // Contrôles & handlers
  attachClickHandlers();

  // Sync initiale du bouton Play/Pause + polling périodique
  syncPlayButton();
  setInterval(syncPlayButton, 3000);
});
